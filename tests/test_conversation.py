"""Chat step 1-2: no network, real DB isolation, deterministic follow-ups."""
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock

from app.conversation import (ConversationStore, ConversationError, DEFAULT_TIME,
                              resolve_question, submit_turn)
from app.router import QueryResult, RoutePlan, UnsupportedRoute, run_query


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ConversationStore(Path(self.tmp.name) / 'chat.sqlite3')
        self.cap = self.store.create()

    def send(self, q, **kwargs):
        return submit_turn(self.store, self.cap, q, runner=run_query,
                           use_investigation=False, use_llm=False, **kwargs)

    def test_real_peak_followup_requeries_and_keeps_original(self):
        first = self.send('当时预测峰值是多少？')
        self.assertIsNotNone(first.result.forecast)
        second = self.send('它什么时候到？')
        self.assertEqual(second.result.forecast.run_id, first.result.forecast.run_id)
        self.assertIn('预测峰值', second.resolution.question)
        snapshot = self.store.load(self.cap)
        self.assertEqual(snapshot['turns'][1]['raw_question'], '它什么时候到？')
        self.assertNotEqual(snapshot['turns'][0]['id'], snapshot['turns'][1]['id'])

    def test_ambiguous_pronoun_does_not_execute(self):
        self.send('当时预测峰值是多少？项目内部 Warning 是什么？')
        runner = Mock()
        outcome = submit_turn(self.store, self.cap, '它是什么意思？', runner=runner)
        self.assertEqual(outcome.status, 'clarify')
        runner.assert_not_called()
        self.assertIn('哪一项', outcome.answer)
        self.assertIsNotNone(self.send('峰值呢？').result.forecast)

    def test_forecast_mae_relation_resolves_both_ends(self):
        self.send('当时预测峰值是多少？')
        outcome = self.send('它和MAE有什么联系？')
        self.assertIn('当时预测峰值', outcome.resolution.question)
        self.assertIsNotNone(outcome.result.forecast)
        self.assertIsNotNone(outcome.result.evaluation)

    def test_reopen_store_and_capability_recover(self):
        self.send('当时预测峰值是多少？')
        reopened = ConversationStore(self.store.path)
        self.assertEqual(len(reopened.load(self.cap)['turns']), 1)
        outcome = submit_turn(reopened, self.cap, '它什么时候到？', runner=run_query, use_investigation=False)
        self.assertIsNotNone(outcome.result.forecast)

    def test_new_conversation_has_no_previous_reference(self):
        self.send('当时预测峰值是多少？')
        other = self.store.create()
        result = submit_turn(self.store, other, '它什么时候到？', runner=Mock())
        self.assertEqual(result.status, 'clarify')
        self.assertEqual(len(self.store.load(self.cap)['turns']), 1)

    def test_capability_is_required_for_read_and_delete(self):
        bad = self.cap[:33] + 'x' * 43
        for cap in (bad, self.cap.split('.')[0], '../state/chat.sqlite3'):
            with self.assertRaises(ConversationError): self.store.load(cap)
            with self.assertRaises(ConversationError): self.store.clear(cap, delete=True)
        self.assertEqual(self.store.load(self.cap)['title'], '新对话')

    def test_clear_resets_context_and_delete_revokes_access(self):
        self.send('当时预测峰值是多少？')
        self.store.clear(self.cap)
        self.assertFalse(self.store.load(self.cap)['turns'])
        self.assertFalse(self.store.load(self.cap)['state']['topics'])
        self.store.clear(self.cap, delete=True)
        with self.assertRaises(ConversationError): self.store.load(self.cap)

    def test_date_only_and_relative_date_require_clarification(self):
        self.send('当时预测峰值是多少？')
        for q in ('换成2026-08-04呢？', '换一天呢？'):
            outcome = self.send(q)
            self.assertEqual(outcome.status, 'clarify')

    def test_time_change_passed_to_fresh_tools(self):
        self.send('当时预测峰值是多少？')
        runner = Mock(return_value=QueryResult('new', 'peak', None, RoutePlan((), (), None)))
        out = submit_turn(self.store, self.cap, '换成2026-08-04T01:48:00Z呢？', runner=runner)
        self.assertEqual(runner.call_args.kwargs['as_of_utc'], '2026-08-04T01:48:00Z')
        self.assertIsNone(self.store.load(self.cap)['state']['run_id'])

    def test_historical_answer_is_not_supplied_to_context_model(self):
        state = self.store.load(self.cap)['state']
        state.update(topics=['peak'], last_question='SECRET old hallucinated 999m')
        model = Mock()
        model._call.return_value = '{"attribute":"time"}'
        out = resolve_question('它什么时候到？', state, client=model)
        request = str(model._call.call_args)
        self.assertNotIn('SECRET', request)
        self.assertNotIn('999', out.question)
        self.assertIn('目标时间', out.question)

    def test_invalid_model_output_cannot_change_tools_time_or_facts(self):
        model = Mock()
        model._call.return_value = '{"attribute":"time","tool":"delete","value":999}'
        state = self.store.load(self.cap)['state']
        state['topics'] = ['peak']
        out = resolve_question('它什么时候到？', state, client=model)
        self.assertIn('model_fallback', out.debug)
        self.assertNotIn('delete', out.question)
        self.assertEqual(out.as_of_utc, DEFAULT_TIME)

    def test_scope_negation_and_network_denial_survive(self):
        state = self.store.load(self.cap)['state']
        state['topics'] = ['warning']
        out = resolve_question('它不是官方预警吧，不要联网', state)
        self.assertIn('不是官方', out.question)
        self.assertIn('不要联网', out.question)
        runner = Mock(return_value=QueryResult('id', 'q', None, RoutePlan((), (), None)))
        submit_turn(self.store, self.cap, '你好，不要联网', runner=runner, allow_web=True)
        self.assertFalse(runner.call_args.kwargs['allow_web'])

    def test_explicit_correction_excludes_old_entity(self):
        state = self.store.load(self.cap)['state']
        state['topics'] = ['old_sensor']
        out = resolve_question('不是旧传感器，是现在的预测方法', state)
        self.assertEqual(out.topics, ['method'])
        self.assertIn('不是旧传感器', out.question)

    def test_unsafe_request_blocked_before_model_or_storage(self):
        model = Mock()
        with self.assertRaises(UnsupportedRoute):
            submit_turn(self.store, self.cap, '修改SQLite数据库', client=model, use_llm=True)
        model._call.assert_not_called()
        self.assertFalse(self.store.load(self.cap)['turns'])

    def test_unexpected_failure_is_safe_and_not_previous_answer(self):
        self.send('当时预测峰值是多少？')
        runner = Mock(side_effect=RuntimeError('private-key-must-not-leak'))
        out = submit_turn(self.store, self.cap, '它什么时候到？', runner=runner)
        self.assertEqual(out.status, 'error')
        self.assertIsNone(out.result)
        self.assertNotIn('private-key', out.answer)
        self.assertIsNone(self.store.load(self.cap)['turns'][-1]['result_json'])

    def test_stale_writer_rejected_and_no_reasoning_persisted(self):
        result = QueryResult('id', '你好', None, RoutePlan((), (), None), answer_text='你好',
                             reasoning_traces=[{'stage':'model','content':'private-reasoning'}])
        runner = Mock(return_value=result)
        out = submit_turn(self.store, self.cap, '你好', runner=runner)
        self.assertNotIn('private-reasoning', self.store.path.read_bytes().decode('latin1'))
        with self.assertRaises(ConversationError):
            self.store.append(self.cap, expected_revision=0, raw='stale', resolution=out.resolution,
                              answer='stale', result=None, status='error', state={})

    def test_budget_is_configurable_and_bounds_checked(self):
        out = resolve_question('你好', self.store.load(self.cap)['state'], context_budget=131072)
        self.assertEqual(out.debug['context_budget'], 131072)
        with self.assertRaises(ConversationError):
            resolve_question('你好', {}, context_budget=1000000)

    def test_business_db_names_refused(self):
        with self.assertRaises(ConversationError):
            ConversationStore(Path(self.tmp.name) / 'prediction_history.sqlite3')

    def test_greeting_does_not_discard_active_topic(self):
        self.send('当时预测峰值是多少？')
        self.send('你好')
        self.assertEqual(self.store.load(self.cap)['state']['topics'], ['peak'])

    def test_plain_language_is_a_session_preference(self):
        self.send('当时预测峰值是多少？')
        self.send('以后都说简单点')
        self.assertTrue(self.store.load(self.cap)['state']['plain_language'])
        outcome = self.send('MAE是多少？')
        self.assertIn('通俗中文', outcome.resolution.question)

    def test_source_binding_change_refuses_silent_substitution(self):
        first = self.send('当时预测峰值是多少？')
        from copy import deepcopy
        replacement = deepcopy(first.result)
        replacement.forecast = replacement.forecast.model_copy(update={'run_id': 'a' * 64})
        out = submit_turn(self.store, self.cap, '它什么时候到？', runner=Mock(return_value=replacement))
        self.assertIsNone(out.result)
        self.assertEqual(out.status, 'error')
        self.assertIn('来源已变化', out.answer)


if __name__ == '__main__': unittest.main()
