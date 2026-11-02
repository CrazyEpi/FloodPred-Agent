import json,tempfile,time
from pathlib import Path
import unittest
from unittest.mock import patch
from functools import partial
from app.history_data import build_catalog,query_history
from app.information_query import checked_query,local_query,execute_information
from app.dialogue import submit_dialogue
from app.conversation import ConversationStore

class InformationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.path=self.root/'house_mill_processed.csv'
        self.path.write_text('_time,internal_water_m,sonar_dist_mm_cleaned\n2025-12-30T10:00:00Z,1,4000\n2025-12-31T10:00:00Z,2,3000\n2025-12-31T10:15:00Z,4,1000\n2025-12-31T10:30:00Z,3,2000\n',encoding='utf-8')
        self.catalog=self.root/'catalog.json';self.refresh()
        self.tool=partial(query_history,data=self.root,catalog=self.catalog)
    def refresh(self):self.catalog.write_text(json.dumps(build_catalog(self.root)),encoding='utf-8')
    def run_query(self,q,**kw):return execute_information(q,tool=self.tool,**kw)
    def test_exact_and_fuzzy_station_clarification(self):
        self.assertIn('2.0000',self.run_query('2025年12月31日10点 UTC 的内部水位')['answer'])
        for q in ['那天早上八点河里多高？','2025年12月31日10点河水多高？']:
            self.assertEqual(self.run_query(q)['status'],'clarify')
    def test_compare_values_and_no_trend_causality(self):
        r=self.run_query('2025年12月31日10点 UTC 内部水位跟前一天比涨了吗？')
        self.assertIn('高 1.0000 m',r['answer']); self.assertIn('不能据此说全天持续上涨',r['answer'])
        self.assertEqual(len(r['evidence']),2)
    def test_two_explicit_dates(self):
        r=self.run_query('比较2025年12月31日10点 UTC 和2025年12月30日10点 UTC 的内部水位')
        self.assertIn('高 1.0000',r['answer'])
    def test_mean_max_denominator_and_interval(self):
        for word,value in [('均值','3.0000'),('最大值','4.0000')]:
            r=self.run_query(f'2025年12月31日10点 UTC 至2025年12月31日10点30分 UTC 内部水位{word}')
            self.assertIn(value,r['answer']);self.assertIn('2 个有效时间点',r['answer'])
    def test_nearest_is_explicit_and_tie_is_not_selected(self):
        r=self.run_query('2025年12月31日10点1分 UTC 附近的内部水位')
        self.assertIn('时间差 60 秒',r['answer'])
        r=self.tool('2025-12-31T10:07:30Z',mode='nearest')
        self.assertEqual(r['status'],'conflict')
        q=local_query('2025年12月31日10点1分 UTC 内部水位').model_dump();q['operation']='nearest'
        with self.assertRaises(ValueError):checked_query(q,'2025年12月31日10点1分 UTC 内部水位')
    def test_metric_source_and_time_are_not_invented(self):
        text='2025年12月31日10点 UTC 内部水位';q=local_query(text).model_dump()
        for field,value in [('time_text','2024年1月1日'),('source','integrated_training'),('metric','sonar_distance'),('station','lea_bridge')]:
            p=dict(q);p[field]=value
            with self.assertRaises(ValueError): checked_query(p,text)
    def test_missing_data_partial_and_no_source_switch(self):
        r=self.run_query('2025年12月30日10点 UTC 内部水位跟前一天比涨了吗？')
        self.assertEqual(r['status'],'partial');self.assertIn('未改用其他版本',r['answer'])
        self.assertTrue(all(e['source_kind']=='processed_history' for e in r['evidence']))
        self.assertTrue(any(t['tool']=='inspect_sources' for t in r['trace']))
    def test_over_budget_unreliable_stops_with_explanation(self):
        r=self.run_query('2025年12月30日10点 UTC 内部水位跟前一天比涨了吗？',budget={'calls':8,'started':time.monotonic()})
        self.assertEqual(r['status'],'failed');self.assertTrue(r['answer']);self.assertEqual(r['evidence'],[])
    def test_model_cannot_release_bad_data(self):
        class Review:
            def call(self,*a,**kw):return '{"assessment":"sufficient","followup":"none"}'
        r=self.run_query('2025年12月30日10点 UTC 内部水位跟前一天比涨了吗？',provider=Review())
        self.assertEqual(r['status'],'partial')
    def test_model_gap_triggers_registered_inspection(self):
        class Review:
            def call(self,*a,**kw):return '{"assessment":"missing","followup":"inspect_sources"}'
        r=self.run_query('2025年12月31日10点 UTC 内部水位跟前一天比涨了吗？',provider=Review())
        self.assertEqual(r['status'],'partial')
        self.assertTrue(any(t['tool']=='inspect_sources' for t in r['trace']))
        self.assertLessEqual(max(t.get('round',1) for t in r['trace']),3)
    def test_model_can_recheck_after_new_coverage_without_changing_values(self):
        class Review:
            def __init__(self):self.calls=0
            def call(self,*a,**kw):
                self.calls+=1
                return '{"assessment":"missing","followup":"inspect_sources"}' if self.calls==1 else '{"assessment":"sufficient","followup":"none"}'
        provider=Review()
        r=self.run_query('2025年12月31日10点 UTC 内部水位跟前一天比涨了吗？',provider=provider)
        self.assertEqual(provider.calls,2);self.assertEqual(r['status'],'answered')
        self.assertIn('高 1.0000',r['answer'])
    def test_units_versions_mismatch_not_compared(self):
        def wrong(**kw):
            r=self.tool(**kw)
            if kw['utc'].startswith('2025-12-30'):r['matches'][0]['unit']='mm'
            return r
        r=execute_information('2025年12月31日10点 UTC 内部水位跟前一天比涨了吗？',tool=wrong)
        self.assertEqual(r['status'],'partial');self.assertIn('不能直接比较',r['answer'])
    def test_debug_rejects_aggregates(self):
        r=self.run_query('2025年12月31日10点 UTC 内部水位跟前一天比涨了吗？',mode='debug',override='2026-08-03T01:48:00Z')
        self.assertEqual(r['status'],'clarify')
    def test_dialogue_mixed_and_identity(self):
        store=ConversationStore(self.root/'chat.sqlite3');cap=store.create()
        self.assertIn('FloodPred',submit_dialogue(store,cap,'你是谁',use_llm=False,time_mode='normal').answer)
        with patch('app.information_query.query_history',self.tool):
            # Default tool is bound at definition; direct executor tests above use the injected fixture.
            r=submit_dialogue(store,cap,'2023年1月2日早上8点水位是多少？项目内部 Warning 是什么？',use_llm=False,time_mode='normal')
        self.assertIn('不覆盖',r.answer);self.assertIn('4.43',r.answer)
        self.assertTrue(all(n['answer'] for n in r.resolution.debug['needs']))

    def test_clarified_station_continues_pending_query(self):
        store=ConversationStore(self.root/'pending.sqlite3');cap=store.create()
        r=submit_dialogue(store,cap,'2025年12月31日23点45分 UTC 河里多高？',use_llm=False,time_mode='normal')
        self.assertIn('Lea Bridge',r.answer)
        r=submit_dialogue(store,cap,'House Mill 内部',use_llm=False,time_mode='normal')
        self.assertIn('2.8750',r.answer)

    def test_examples_and_coverage_do_not_submit(self):
        import os
        from streamlit.testing.v1 import AppTest
        with patch.dict(os.environ,{'FLOODPRED_CHAT_DB':str(self.root/'ui.sqlite3')}),patch('app.dialogue.submit_dialogue') as submit:
            app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py')).run()
            self.assertTrue(any(e.label=='示例与使用说明' for e in app.expander))
            app.selectbox(key='example_group').set_value('比较分析').run()
            self.assertTrue(any('前一天' in c.value for c in app.code))
            self.assertFalse(app.exception)
            submit.assert_not_called()

if __name__=='__main__':unittest.main()
