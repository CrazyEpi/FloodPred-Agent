"""Mixed-demand regression: real local tools, no API credentials needed."""
import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import asdict
from app.conversation import ConversationStore
from app.dialogue import submit_dialogue, local_needs, validate_needs
from app.retrieval_index import semantic_index, semantic_scores


class DialogueTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = ConversationStore(Path(tmp.name)/'chat.sqlite3')
        self.cap = self.store.create()

    def send(self, text):
        return submit_dialogue(self.store,self.cap,text,use_llm=False,allow_web=False)

    def test_24_multiturn_cases(self):
        mixed = [
            '昨天我吃了什么？预测峰值是多少？',
            '你好，项目内部 Warning 是什么？',
            '我今天很开心，预测峰值是多少？',
            '上次查询的时间是什么？项目内部 Caution 是什么？',
            '我昨天说吃的东西是什么，以及当时预测的峰值是多少？',
            '谢谢，MAE是多少？',
            '昨天吃了鸡肉，对了，预测峰值是多少？',
            '我喜欢摄影，历史水位是多少？',
            '我昨天吃了什么？项目内部 Warning 是什么？',
            '你好，预测峰值和MAE有什么关系？',
            '今天心情不错，官方 Flood Alert 是什么？',
            '昨天我吃了什么？这个时间点的预测水位是多少？',
        ]
        single = ['昨天我吃了什么？','上次查询的时间是什么？','你上次说了什么？',
                  '重新查一下预测峰值','你好','预测峰值和MAE有什么关系？',
                  '项目内部 Warning 是什么？','这个时间点的预测水位是多少？',
                  '不要联网，官方 Flood Alert 是什么？','当时实测水位是多少？',
                  '项目内部 Caution 是什么？','昨天我喝了什么？']
        for question in mixed+single:
            with self.subTest(question=question):
                self.store.clear(self.cap)
                self.send('昨天我吃了鸡肉')
                self.send('当时预测峰值是多少？')
                outcome = self.send(question)
                self.assertTrue(outcome.answer)
                needs = outcome.resolution.debug['needs']
                self.assertTrue(needs)
                self.assertFalse(any(n['status']=='pending' for n in needs))
                if question in mixed:
                    self.assertGreaterEqual(len({n['channel'] for n in needs}),2)
                if '吃了什么' in question or '吃的东西是什么' in question:
                    self.assertIn('鸡肉', outcome.answer)
                if '时间点的预测水位' in question:
                    self.assertIn('不会用预测峰值替代',outcome.answer)

    def test_coverage_wrong_history_cycle_and_channel_rejected(self):
        raw = '昨天我吃了什么？预测峰值是多少？'
        value = {'needs':[asdict(n) for n in local_needs(raw)]}
        validate_needs(value,raw,[])
        for field, bad in [('history_ids',['other-session']),('depends_on',['d2']),('channel','general')]:
            changed=json.loads(json.dumps(value))
            changed['needs'][1 if field=='channel' else 0][field]=bad
            with self.assertRaises(ValueError): validate_needs(changed,raw,[])
        with self.assertRaises(ValueError): validate_needs({'needs':value['needs'][:1]},raw,[])

    def test_clear_and_new_session_do_not_recall_food(self):
        self.send('昨天我吃了鸡肉')
        other=self.store.create()
        answer=submit_dialogue(self.store,other,'昨天我吃了什么？',use_llm=False)
        self.assertNotIn('鸡肉',answer.answer)
        self.store.clear(self.cap)
        self.assertNotIn('鸡肉',self.send('昨天我吃了什么？').answer)

    def test_live_discovered_invalid_proposals_are_rejected(self):
        for text,channel,clarify in [('昨天我吃了鸡肉','memory',''),('当时预测峰值是多少？','project','当时')]:
            value={'needs':[{'id':'d1','text':text,'channel':channel,'history_ids':[], 'depends_on':[], 'clarify':clarify}]}
            with self.assertRaises(ValueError): validate_needs(value,text,[])

    def test_correction_replaces_earlier_food_statement(self):
        self.send('昨天我吃了鸡肉')
        self.send('不是鸡肉，是牛肉')
        self.assertIn('更正', self.send('昨天我吃了什么？').answer)
        self.assertIn('牛肉', self.send('昨天我吃了什么？').answer)

    def test_exact_numeric_and_time_queries_do_not_call_provider(self):
        class NoCalls:
            def _call(self,*a,**kw): raise AssertionError('redundant API call')
        result=submit_dialogue(self.store,self.cap,'当时预测峰值是多少？',use_llm=True,client=NoCalls())
        self.assertEqual(result.resolution.debug['llm_calls'],0)
        self.assertIn('3.7832',result.answer)
        result=submit_dialogue(self.store,self.cap,'上一次查询的时间是多少？',use_llm=True,client=NoCalls())
        self.assertEqual(result.resolution.debug['llm_calls'],0)
        self.assertIn('2026-08-03',result.answer)

    def test_fixed_index_reused_and_content_changes_rebuild(self):
        semantic_index.cache_clear()
        docs=(('water','level'),('sensor','water'),('forecast','model'))
        semantic_scores(['water'],docs)
        before=semantic_index.cache_info()
        semantic_scores(['model'],docs)
        self.assertEqual(semantic_index.cache_info().misses,before.misses)
        self.assertGreater(semantic_index.cache_info().hits,before.hits)
        semantic_scores(['water'],docs+(('new','source'),))
        self.assertEqual(semantic_index.cache_info().misses,before.misses+1)

    def test_source_change_detected_at_final_epoch(self):
        from app.source_snapshot import source_snapshot, read_bytes, fresh_verification
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.txt'
            path.write_text('old')
            with source_snapshot():
                self.assertEqual(read_bytes(path),b'old')
                path.write_text('new')
                fresh_verification()
                self.assertEqual(read_bytes(path),b'new')
                path.unlink()
                fresh_verification()
                with self.assertRaises(FileNotFoundError): read_bytes(path)
