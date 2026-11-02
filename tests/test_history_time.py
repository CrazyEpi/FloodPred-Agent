from datetime import datetime, timezone
from pathlib import Path
import tempfile
import json
import unittest
from app.query_time import parse_query_time
from app.history_data import build_catalog,query_history
from app.dialogue import submit_dialogue
from app.conversation import ConversationStore

class TimeTests(unittest.TestCase):
    def test_cn_en_and_offset(self):
        for q in ['2023年7月2日早上8点','23年7月2日上午八点','2 July 2023 at 8 am','July 2, 2023 at 8am']:
            with self.subTest(q=q): self.assertEqual(parse_query_time(q).utc,'2023-07-02T07:00:00Z')
        self.assertEqual(parse_query_time('2023年7月2日下午三点半 北京时间').utc,'2023-07-02T07:30:00Z')
        self.assertEqual(parse_query_time('2023-01-02T08:00:00Z').utc,'2023-01-02T08:00:00Z')

    def test_invalid_ambiguous_and_dst(self):
        for q in ['2023年2月30日早上8点','2023年1月2日25点']:
            self.assertEqual(parse_query_time(q).status,'invalid')
        for q in ['01/02/2023 at 8 am','2023年7月2日','2023-03-26 01:30','2023-10-29 01:30']:
            self.assertEqual(parse_query_time(q).status,'clarify')

    def test_relative_and_debug(self):
        now=datetime(2026,11,1,12,tzinfo=timezone.utc)
        self.assertEqual(parse_query_time('昨天早上八点',now=now).utc,'2026-10-31T08:00:00Z')
        self.assertEqual(parse_query_time('yesterday at 8am',now=now).utc,'2026-10-31T08:00:00Z')
        self.assertEqual(parse_query_time('2023年1月2日8点',override='2026-08-03T01:48:00Z').utc,'2026-08-03T01:48:00Z')
        self.assertEqual(parse_query_time('比较昨天和今天',override='2026-08-03T01:48:00Z').status,'clarify')

class HistoryTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        p=self.root/'house_mill_processed.csv'
        p.write_text('_time,internal_water_m\n2023-07-02T07:00:00Z,2.5\n2023-07-02T07:15:00Z,\n',encoding='utf-8')
        self.catalog=self.root/'catalog.json'
        self.catalog.write_text(json.dumps(build_catalog(self.root)),encoding='utf-8')
    def query(self,t):return query_history(t,data=self.root,catalog=self.catalog)
    def test_exact_missing_blank_integrity(self):
        self.assertEqual(self.query('2023-07-02T07:00:00Z')['matches'][0]['value'],2.5)
        self.assertEqual(self.query('2023-07-02T07:01:00Z')['status'],'no_exact_record')
        self.assertEqual(self.query('2023-07-02T07:15:00Z')['status'],'no_exact_record')
        self.assertEqual(self.query('2023-01-02T08:00:00Z')['status'],'no_data')
        (self.root/'house_mill_processed.csv').write_text('changed')
        with self.assertRaises(ValueError):self.query('2023-07-02T07:00:00Z')
    def test_normal_time_no_default_and_outside_range(self):
        store=ConversationStore(self.root/'chat.sqlite3'); cap=store.create()
        result=submit_dialogue(store,cap,'当时预测峰值是多少？',use_llm=False,time_mode='normal')
        self.assertIn('没有默认回放时间',result.answer)
        result=submit_dialogue(store,cap,'2023年1月2日早上8点的洪水水位是多少',use_llm=False,time_mode='normal')
        self.assertIn('2023-01-02T08:00:00Z',result.answer)
        self.assertIn('不覆盖',result.answer)
        self.assertNotIn('2026-08-03',result.answer)

    def test_normal_ui_hides_time_debug_reveals(self):
        import os
        from unittest.mock import patch
        from streamlit.testing.v1 import AppTest
        with patch.dict(os.environ,{'FLOODPRED_CHAT_DB':str(self.root/'ui.sqlite3')}):
            app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py')).run()
            self.assertFalse(any(x.key=='replay_time' for x in app.text_input))
            app.toggle(key='debug_mode').set_value(True).run()
            self.assertTrue(any(x.key=='replay_time' for x in app.text_input))
            app.toggle(key='debug_mode').set_value(False).run()
            self.assertFalse(any(x.key=='replay_time' for x in app.text_input))
