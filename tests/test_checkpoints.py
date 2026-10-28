"""Real SQLite checkpoints, fake clocks/clients and temporary evidence only."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app import investigation_core as core
from app.checkpoint_codec import CheckpointCodec
from app.investigation_checkpoint import (
    CheckpointError, InvestigationInterrupted, _Store, resume_investigation,
    start_persisted_investigation,
)
from app.knowledge import DEFAULT_CATALOG, ROOT
from app.router import run_query
from tests.test_investigation_graph import canonical, OfflineClient


AS_OF = "2026-08-03T01:48:00Z"


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "state.sqlite3"
        self.now = [1000.0]

    def start(self, question="项目内部 Caution 和英国官方预警有什么区别？", **kwargs):
        return start_persisted_investigation(question, state_db=self.db, as_of_utc=AS_OF,
                                             now=lambda: self.now[0], **kwargs)

    def resume(self, token, **kwargs):
        return resume_investigation(token, state_db=self.db, now=lambda: self.now[0], **kwargs)

    def audit_count(self, execution_id):
        with _Store(self.db, create=False).connection() as conn:
            return conn.execute("SELECT count(*) FROM floodpred_audits WHERE run_id=?", (execution_id,)).fetchone()[0]

    def test_roundtrip_custom_objects_and_ephemeral_reasoning(self):
        result = run_query("预测峰值、历史水位、MAE 和项目内部 Caution 是什么？", as_of_utc=AS_OF,
                           audit_log=None, generate_answer=False)
        result.reasoning_traces = [{"stage": "test", "content": "private transient reasoning"}]
        expected = deepcopy(result)
        expected.reasoning_traces = []
        codec = CheckpointCodec()
        serialized = codec.dumps(result)
        restored = codec.loads(serialized)
        self.assertEqual(canonical(expected), canonical(restored))
        self.assertNotIn("private transient reasoning", serialized)
        self.assertEqual(restored.forecast.run_id, result.forecast.run_id)

    def test_codec_rejects_unknown_types_versions_and_field_drift(self):
        codec = CheckpointCodec()
        with self.assertRaises(ValueError):
            codec.loads('{"type":"os.system","value":"echo unsafe"}')
        with self.assertRaises(ValueError):
            codec.loads_typed(("unknown-version", b"null"))
        with self.assertRaises(ValueError):
            codec.loads('{"type":"QueryResult","value":{"type":"dict","value":[]}}')

    def test_pause_resume_matches_ephemeral_answer_without_resetting_budget(self):
        before = self.start(pause_after="first_pass")
        self.assertEqual(before.status, "paused")
        self.assertEqual(before.next_nodes, ("decide",))
        self.now[0] += 5
        after = self.resume(before.resume_token)
        from app.investigation import run_investigation
        direct = run_investigation(before.result.question, as_of_utc=AS_OF, audit_log=None)
        self.assertEqual(after.status, "completed")
        self.assertEqual(after.execution_id, before.execution_id)
        self.assertEqual(after.deadline_utc, before.deadline_utc)
        self.assertEqual(after.result.answer_text, direct.answer_text)
        self.assertEqual(after.tool_calls, direct.investigation_tool_calls)
        self.assertGreater(after.tool_calls, before.tool_calls)
        self.assertEqual(after.result.as_of_utc, AS_OF)
        self.assertEqual(self.audit_count(after.execution_id), 1)

    def test_completed_resume_is_idempotent_even_after_deadline(self):
        completed = self.start("当时预测峰值是多少？")
        self.now[0] += 1000
        with patch.object(core, "first_pass", side_effect=AssertionError("must not rerun")):
            again = self.resume(completed.resume_token)
        self.assertEqual(canonical(again.result), canonical(completed.result))
        self.assertEqual(again.tool_calls, completed.tool_calls)
        self.assertEqual(self.audit_count(completed.execution_id), 1)

    def test_downtime_exhausts_absolute_deadline_without_model_or_tool_calls(self):
        before = self.start(pause_after="first_pass", limits=core.InvestigationLimits(max_seconds=1))
        self.now[0] += 2
        with patch.object(core, "_fill_gaps", side_effect=AssertionError("expired tool")):
            after = self.resume(before.resume_token)
        self.assertEqual(after.result.investigation_stop_reason, "time_limit")
        self.assertEqual(after.tool_calls, before.tool_calls)
        self.assertEqual(after.result.as_of_utc, AS_OF)
        self.assertEqual(after.result.answer_status, "partial_verified")

    def test_persisted_model_counts_and_no_credentials_or_reasoning_in_storage(self):
        client = OfflineClient()
        before = self.start("项目内部 Caution 是什么？", pause_after="first_pass", use_llm=True, client=client)
        self.assertEqual(before.llm_calls, 1)
        client_after = OfflineClient()
        after = self.resume(before.resume_token, client=client_after)
        self.assertEqual(after.llm_calls, 2)
        self.assertEqual(client_after.events, ["select"])
        with _Store(self.db, create=False).connection() as conn:
            content = "\n".join(str(tuple(row)) for table in ("floodpred_runs", "floodpred_nodes", "checkpoints")
                                 for row in conn.execute(f"SELECT * FROM {table}"))
        self.assertNotIn('"content": "项目内部 Caution 是什么？"', content)
        self.assertNotIn('"api_key"', content)
        self.assertNotIn('"provider"', content)

    def test_greeting_reply_is_reserved_before_call_without_double_count(self):
        client = OfflineClient("greeting")
        run = self.start("你好", use_llm=True, client=client)
        self.assertEqual(run.llm_calls, 2)
        self.assertEqual(client.events, ["plan", "reply"])
        self.assertEqual(self.resume(run.resume_token).llm_calls, 2)

    def test_pause_after_followup_resumes_pending_recheck_without_source_change(self):
        run = self.start(pause_after="followup")
        self.assertEqual(run.next_nodes, ("recheck",))
        after = self.resume(run.resume_token)
        self.assertNotEqual(after.result.investigation_stop_reason, "source_changed")
        self.assertEqual(after.tool_calls, run.tool_calls)
        self.assertEqual(after.result.investigation_rounds, 2)

    def test_reply_failure_still_persists_its_precharged_model_call(self):
        client = OfflineClient("greeting")
        with patch.object(client, "reply", side_effect=RuntimeError("lost reply")):
            with self.assertRaises(InvestigationInterrupted) as interrupted:
                self.start("你好", use_llm=True, client=client)
        after = self.resume(interrupted.exception.token, client=OfflineClient("greeting"))
        self.assertEqual(after.llm_calls, 2)
        self.assertEqual(after.result.investigation_stop_reason, "interrupted_inflight")

    def test_changed_data_file_invalidates_saved_numeric_batch(self):
        copied_db = Path(self.temp.name) / "forecast.sqlite3"
        copied_db.write_bytes(core.DEFAULT_DB.read_bytes())
        before = self.start("当时预测峰值是多少？", forecast_db=copied_db, pause_after="first_pass")
        import os
        stat = copied_db.stat()
        os.utime(copied_db, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
        after = self.resume(before.resume_token)
        self.assertIsNone(after.result.forecast)
        self.assertEqual(after.tool_calls, before.tool_calls)
        self.assertEqual(after.result.investigation_stop_reason, "source_changed")

    def test_saved_evaluation_paths_are_explicitly_used_by_followup(self):
        from app import router
        initial = run_query("预测峰值和MAE的联系是什么？", as_of_utc=AS_OF, audit_log=None, generate_answer=False)
        initial.plan = core.RoutePlan(("archived_forecast",), (), None)
        initial.evaluation = None
        assess_evidence = core.assess_evidence
        assess_evidence(initial, root=ROOT)
        with patch.object(core, "run_query", return_value=initial):
            before = self.start(initial.question, pause_after="first_pass")
        with patch.object(router, "evaluation_metrics_tool", wraps=router.evaluation_metrics_tool) as tool:
            after = self.resume(before.resume_token)
        self.assertIsNotNone(after.result.evaluation)
        self.assertEqual(tool.call_args.kwargs["overall_path"], core.DEFAULT_DB.parent / "output" / "summary.json")
        self.assertEqual(tool.call_args.kwargs["high_water_path"], core.DEFAULT_DB.parent / "output_above_2m" / "high_water_summary.json")

    def test_incompatible_run_version_is_rejected_before_execution(self):
        run = self.start(pause_after="first_pass")
        store = _Store(self.db, create=False)
        codec = CheckpointCodec()
        payload = codec.loads(store.row(run.execution_id)["payload"])
        payload["schema_version"] = 999
        store.update(run.execution_id, payload=codec.dumps(payload))
        with patch.object(core, "first_pass", side_effect=AssertionError("incompatible state")), self.assertRaises(CheckpointError):
            self.resume(run.resume_token)

    def test_streamlit_removes_recovery_controls_in_both_modes(self):
        from streamlit.testing.v1 import AppTest
        with patch("app.investigation.start_persisted_investigation") as start, \
             patch("app.investigation.resume_investigation") as resume:
            app = AppTest.from_file(str(ROOT / "streamlit_app.py")).run(timeout=10)
            app.session_state["last_resume_token"] = "old-private-token"
            for debug in (False, True):
                app.toggle[0].set_value(debug).run()
                self.assertFalse(app.exception)
                self.assertEqual(len(app.text_input), 2)
                self.assertEqual(len(app.checkbox), 3)
                labels = [item.label for kind in (app.text_input, app.checkbox, app.button, app.expander) for item in kind]
                self.assertFalse(any("恢复" in label or "保存调查" in label for label in labels))
                self.assertNotIn("last_resume_token", app.session_state)
            start.assert_not_called()
            resume.assert_not_called()

    def test_streamlit_ordinary_submission_does_not_use_persistence(self):
        from app.router import QueryResult, RoutePlan
        from streamlit.testing.v1 import AppTest
        result = QueryResult(request_id="ui", question="原问题", as_of_utc=AS_OF,
                             plan=RoutePlan((), (), None), answer_text="普通回答")
        with patch("app.investigation.run_investigation", return_value=result) as run, \
             patch("app.investigation.start_persisted_investigation") as start, \
             patch("app.investigation.resume_investigation") as resume:
            app = AppTest.from_file(str(ROOT / "streamlit_app.py")).run(timeout=10)
            app.button[0].click().run(timeout=10)
            app.toggle[0].set_value(True).run()
            self.assertFalse(app.exception)
            self.assertTrue(any("普通回答" in item.value for item in app.markdown))
            run.assert_called_once()
            start.assert_not_called()
            resume.assert_not_called()

    def test_wrong_owner_token_and_different_execution_are_rejected(self):
        first = self.start(pause_after="first_pass")
        second = self.start("当时预测峰值是多少？", pause_after="first_pass")
        mixed = first.execution_id + "." + second.resume_token.split(".", 1)[1]
        with self.assertRaises(CheckpointError):
            self.resume(mixed)
        self.assertNotEqual(first.execution_id, second.execution_id)
        restored = self.resume(first.resume_token)
        self.assertEqual(restored.result.question, first.result.question)

    def test_same_execution_cannot_run_concurrently(self):
        before = self.start(pause_after="first_pass")
        store = _Store(self.db, create=False)
        with store.lock(before.execution_id), self.assertRaises(CheckpointError):
            self.resume(before.resume_token)

    def test_state_store_refuses_existing_evidence_database(self):
        foreign = Path(self.temp.name) / "evidence.sqlite3"
        conn = sqlite3.connect(foreign)
        conn.execute("CREATE TABLE evidence(value TEXT)")
        conn.commit()
        conn.close()
        with self.assertRaises(CheckpointError):
            start_persisted_investigation("项目内部 Caution 是什么？", state_db=foreign)
        conn = sqlite3.connect(foreign)
        self.assertEqual([row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")], ["evidence"])
        conn.close()

    def test_mid_node_failure_keeps_spent_calls_and_does_not_repeat_node(self):
        def fail_after_reservation(request, runtime):
            runtime.budget.reserve_tool("synthetic-tool")
            raise RuntimeError("lost result")
        with patch.object(core, "first_pass", side_effect=fail_after_reservation):
            with self.assertRaises(InvestigationInterrupted) as interrupted:
                self.start()
        token = interrupted.exception.token
        with patch.object(core, "first_pass", side_effect=AssertionError("unknown call must not repeat")):
            after = self.resume(token)
        self.assertEqual(after.tool_calls, 1)
        self.assertEqual(after.result.investigation_stop_reason, "interrupted_inflight")
        self.assertEqual(after.result.answer_status, "fallback")
        self.assertEqual(self.audit_count(after.execution_id), 1)

    def test_completed_node_journal_replays_update_without_repeating_tools(self):
        before = self.start("当时预测峰值是多少？", pause_after="first_pass")
        # Rewind only the graph checkpoint to before first_pass. The completed
        # node journal remains, simulating a checkpoint write lost after the
        # node-result transaction committed.
        with _Store(self.db, create=False).connection() as conn:
            checkpoints = conn.execute("SELECT checkpoint_id FROM checkpoints WHERE thread_id=? ORDER BY checkpoint_id DESC",
                                       (before.execution_id,)).fetchall()
            newest = checkpoints[0][0]
            conn.execute("DELETE FROM checkpoints WHERE thread_id=? AND checkpoint_id=?", (before.execution_id, newest))
            conn.execute("DELETE FROM writes WHERE thread_id=?", (before.execution_id,))
        with patch.object(core, "first_pass", side_effect=AssertionError("completed node must be cached")):
            after = self.resume(before.resume_token)
        self.assertEqual(after.tool_calls, before.tool_calls)
        self.assertEqual(after.result.answer_status, "verified")

    def test_source_change_is_revalidated_without_issuing_new_business_calls(self):
        catalog = Path(self.temp.name) / "catalog.json"
        original = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
        catalog.write_text(json.dumps(original), encoding="utf-8")
        before = self.start("项目内部 Caution 是什么？", catalog=catalog, pause_after="first_pass")
        for record in original["records"]:
            if record.get("concept") == "internal_watch":
                record["version"] = "changed-version"
        catalog.write_text(json.dumps(original), encoding="utf-8")
        with patch.object(core, "_fill_gaps", side_effect=AssertionError("changed-source tool")):
            after = self.resume(before.resume_token)
        self.assertEqual(after.tool_calls, before.tool_calls)
        self.assertEqual(after.result.investigation_stop_reason, "source_changed")
        self.assertFalse(after.result.verified_claims)

    def test_missing_catalog_keeps_only_saved_numeric_evidence(self):
        catalog = Path(self.temp.name) / "catalog.json"
        catalog.write_text(DEFAULT_CATALOG.read_text(encoding="utf-8"), encoding="utf-8")
        before = self.start("当时预测峰值是多少？项目内部 Caution 是什么？", catalog=catalog, pause_after="first_pass")
        catalog.unlink()
        after = self.resume(before.resume_token)
        self.assertIsNotNone(after.result.forecast)
        self.assertFalse(after.result.knowledge)
        self.assertEqual(after.result.answer_status, "partial_verified")
        self.assertEqual(after.result.investigation_stop_reason, "source_changed")
        self.assertEqual(after.tool_calls, before.tool_calls)

    def test_corrupt_payload_fails_without_running_business_nodes(self):
        before = self.start(pause_after="first_pass")
        _Store(self.db, create=False).update(before.execution_id, payload='{"invalid":true}')
        with patch.object(core, "first_pass", side_effect=AssertionError("corrupt state")), self.assertRaises(CheckpointError):
            self.resume(before.resume_token)

    def test_actual_cross_process_recovery_demo(self):
        completed = subprocess.run([sys.executable, "-X", "utf8", "-m", "app.checkpoint_cli", "demo",
                                    "--state-db", str(self.db)], cwd=ROOT,
                                   capture_output=True, text=True, encoding="utf-8", timeout=45)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = json.loads(completed.stdout)
        self.assertTrue(output["separate_process"])
        self.assertTrue(output["same_execution"])
        self.assertTrue(output["same_replay_time"])
        self.assertTrue(output["budget_not_reset"])
        self.assertEqual(output["after"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
