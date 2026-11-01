"""Opt-in local checkpoints, durable budgets and conservative node recovery."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Callable
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.runtime import Runtime
from langsmith import tracing_context

from . import investigation_core as core
from .checkpoint_codec import CheckpointCodec
from .evidence_gate import verify_final_claims
from .investigation_graph import GraphState, GRAPH_RECURSION_LIMIT, build_investigation_graph
from .investigation_state import InvestigationRequest, InvestigationRuntime, initial_investigation_state, snapshot_budget
from .read_tools import OVERALL_SUMMARY, HIGH_WATER_SUMMARY
from .router import QueryResult, RoutePlan, UnsupportedRoute


DEFAULT_STATE_DB = core.ROOT / "state" / "investigations.sqlite3"
SCHEMA_VERSION = 1
_RISKY_NODES = {"initialize", "first_pass", "followup", "finalize"}
_CODEC = CheckpointCodec()


class CheckpointError(ValueError):
    pass


class InvestigationInterrupted(RuntimeError):
    def __init__(self, token: str, error_type: str):
        self.token = token
        super().__init__(f"调查中断（{error_type}）；请使用恢复码读取保存的进度。")


@dataclass(frozen=True)
class PersistedInvestigation:
    execution_id: str
    resume_token: str
    status: str
    next_nodes: tuple[str, ...]
    result: QueryResult | None
    tool_calls: int
    llm_calls: int
    deadline_utc: str


class _Store:
    def __init__(self, path: Path, *, create: bool):
        self.path = Path(path).resolve()
        if not create and not self.path.is_file():
            raise CheckpointError("未找到本机调查状态数据库。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "floodpred_runs" not in tables:
                raise CheckpointError("该文件不是 FloodPred 状态库，拒绝写入已有数据数据库。")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS floodpred_runs (
                    id TEXT PRIMARY KEY, secret_digest TEXT NOT NULL, payload TEXT NOT NULL,
                    tools INTEGER NOT NULL DEFAULT 0, llms INTEGER NOT NULL DEFAULT 0,
                    exceeded TEXT, status TEXT NOT NULL, epoch INTEGER NOT NULL DEFAULT 0,
                    result TEXT);
                CREATE TABLE IF NOT EXISTS floodpred_nodes (
                    run_id TEXT NOT NULL, epoch INTEGER NOT NULL, node_key TEXT NOT NULL,
                    node_name TEXT NOT NULL, status TEXT NOT NULL, update_json TEXT,
                    PRIMARY KEY (run_id, epoch, node_key));
                CREATE TABLE IF NOT EXISTS floodpred_audits (
                    run_id TEXT PRIMARY KEY, record_json TEXT NOT NULL);
            """)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def connection(self):
        conn = self.connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def row(self, execution_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM floodpred_runs WHERE id=?", (execution_id,)).fetchone()
        if row is None:
            raise CheckpointError("恢复码无效或不属于此调查。")
        return dict(row)

    def create(self, execution_id, secret, payload):
        with self.connection() as conn:
            conn.execute("INSERT INTO floodpred_runs(id,secret_digest,payload,status) VALUES(?,?,?,'running')",
                         (execution_id, hashlib.sha256(secret.encode()).hexdigest(), _CODEC.dumps(payload)))

    def authenticate(self, token):
        parts = token.split(".")
        if len(parts) != 2 or len(parts[0]) != 32 or any(c not in "0123456789abcdef" for c in parts[0]) or len(parts[1]) != 43:
            raise CheckpointError("恢复码无效或不属于此调查。")
        row = self.row(parts[0])
        if not hmac.compare_digest(row["secret_digest"], hashlib.sha256(parts[1].encode()).hexdigest()):
            raise CheckpointError("恢复码无效或不属于此调查。")
        return row

    def update(self, execution_id, **values):
        allowed = {"tools", "llms", "exceeded", "status", "epoch", "result", "payload"}
        if not values or set(values) - allowed:
            raise ValueError("Invalid run update")
        with self.connection() as conn:
            conn.execute("UPDATE floodpred_runs SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?",
                         (*values.values(), execution_id))

    def journal(self, execution_id, epoch, key):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM floodpred_nodes WHERE run_id=? AND epoch=? AND node_key=?",
                               (execution_id, epoch, key)).fetchone()
        return dict(row) if row else None

    def save_node(self, execution_id, epoch, key, name, status, update=None):
        with self.connection() as conn:
            conn.execute("INSERT OR REPLACE INTO floodpred_nodes VALUES(?,?,?,?,?,?)",
                         (execution_id, epoch, key, name, status, _CODEC.dumps(update) if update is not None else None))

    def uncertain(self, execution_id, epoch):
        with self.connection() as conn:
            rows = conn.execute("SELECT node_name FROM floodpred_nodes WHERE run_id=? AND epoch=? AND status='inflight'",
                                (execution_id, epoch)).fetchall()
        return any(row[0] in _RISKY_NODES for row in rows)

    @contextmanager
    def lock(self, execution_id):
        directory = self.path.parent / "locks"
        directory.mkdir(exist_ok=True)
        with (directory / (execution_id + ".lock")).open("a+b") as stream:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if __import__("os").name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise CheckpointError("该调查正在另一个进程中执行，请稍后恢复。") from exc
            try:
                yield
            finally:
                stream.seek(0)
                if __import__("os").name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)


class _PersistentBudget(core._Budget):
    """Persist reservations BEFORE operations; restoration never calls base init."""
    def __init__(self, store, execution_id, payload, now):
        self.store, self.execution_id, self.now = store, execution_id, now
        self.deadline = payload["deadline"]
        self.limits = core.InvestigationLimits(**{ "llm_request_timeout_seconds": 20.0, **payload["limits"]})
        row = store.row(execution_id)
        self._tools, self._llms, self._exceeded = row["tools"], row["llms"], row["exceeded"]
        if not (0 <= self._tools <= self.limits.max_tool_calls and 0 <= self._llms <= self.limits.max_llm_calls):
            raise CheckpointError("保存的调用计数不合法，拒绝重置预算。")
        self.reply_precharged = False

    def remaining(self):
        return min(self.limits.max_seconds, self.deadline - self.now())

    @property
    def tool_calls(self):
        return self._tools

    @tool_calls.setter
    def tool_calls(self, value):
        if value < self._tools:
            raise CheckpointError("恢复不能重置工具预算。")
        self.store.update(self.execution_id, tools=value)
        self._tools = value

    @property
    def llm_calls(self):
        return self._llms

    @llm_calls.setter
    def llm_calls(self, value):
        if self.reply_precharged:
            # core.first_pass retains the legacy post-reply increment; the
            # persisted provider has already reserved it before the call.
            self.reply_precharged = False
            return
        if value < self._llms:
            raise CheckpointError("恢复不能重置模型预算。")
        self.store.update(self.execution_id, llms=value)
        self._llms = value

    @property
    def exceeded(self):
        return self._exceeded

    @exceeded.setter
    def exceeded(self, value):
        self.store.update(self.execution_id, exceeded=value)
        self._exceeded = value

    def reserve_llm(self, provider):
        return super().reserve_llm(getattr(provider, "_checkpoint_provider", provider))


class _PersistentProvider:
    """Precharge chat replies; other stages retain their existing guards."""
    def __init__(self, provider, budget):
        self._checkpoint_provider, self._budget = provider, budget

    def __getattr__(self, name):
        value = getattr(self._checkpoint_provider, name)
        if name != "reply" or not callable(value):
            return value
        def reply(*args, **kwargs):
            self._budget.reserve_llm(self._checkpoint_provider)
            output = value(*args, **kwargs)
            self._budget.reply_precharged = True
            return output
        return reply


def _stamp(path):
    try:
        stat = Path(path).stat()
        return [stat.st_size, stat.st_mtime_ns]
    except OSError:
        return None


def _source_context(paths):
    # Cheap change detection only. Existing evidence checks still verify the
    # exact referenced files; no project or historical-backup hash scan.
    try:
        records = json.loads(Path(paths["catalog"]).read_text(encoding="utf-8"))["records"]
        available = True
    except (OSError, ValueError, KeyError):
        records, available = [], False
    versions = {record["id"]: [record.get("version"), record.get("sha256")] for record in records}
    documents = {record["id"]: _stamp(Path(paths["root"]) / record["source_ref"])
                 for record in records if record.get("source_ref") and not record["source_ref"].startswith("https:")}
    return {"catalog_available": available,
            "data": {key: _stamp(paths[key]) for key in ("forecast_db", "sonar_db", "overall", "high_water")},
            "versions": versions, "documents": documents}


def _empty_result(request, execution_id, reason):
    return QueryResult(request_id=execution_id, question=request.question, as_of_utc=request.as_of_utc,
                       knowledge_checked_on_or_before=request.knowledge_checked_on_or_before,
                       plan=RoutePlan((), (), None), answer_status="fallback",
                       fallback_text="中断前没有已保存的可核验结论，请发起新的调查。", errors=[reason])


def _audit_once(store, execution_id, state, runtime):
    result = deepcopy(state["result"])
    record = {"request_id": result.request_id, "execution_id": execution_id,
              "at_utc": datetime.now(timezone.utc).isoformat(),
              "question_sha256": hashlib.sha256(state["request"].question.encode()).hexdigest(),
              "as_of_utc": result.as_of_utc, "knowledge_checked_on_or_before": result.knowledge_checked_on_or_before,
              "route": list(result.plan.steps), "rounds": result.investigation_rounds,
              "tool_calls": runtime.budget.tool_calls, "llm_calls": runtime.budget.llm_calls,
              "stop_reason": result.investigation_stop_reason, "answer_status": result.answer_status,
              "run_id": result.forecast.run_id if result.forecast else None,
              "citation_ids": [item.citation_id for item in result.knowledge], "error_count": len(result.errors)}
    with store.connection() as conn:
        conn.execute("INSERT INTO floodpred_audits VALUES(?,?) ON CONFLICT(run_id) DO UPDATE SET record_json=excluded.record_json",
                     (execution_id, json.dumps(record, ensure_ascii=False)))
    result.audit_status = "written"
    return {"result": result, "budget_snapshot": snapshot_budget(runtime.budget)}


def _node_wrapper(store, execution_id, epoch):
    def wrap(name, action):
        def wrapped(state: GraphState, runtime: Runtime[InvestigationRuntime], config: RunnableConfig) -> dict:
            key = f"{name}:{config['metadata']['langgraph_step']}"
            saved = store.journal(execution_id, epoch, key)
            if saved and saved["status"] == "done":
                return _CODEC.loads(saved["update_json"])
            if saved and name in _RISKY_NODES:
                raise CheckpointError("节点调用结果不确定，禁止自动重复执行。")
            store.save_node(execution_id, epoch, key, name, "inflight")
            update = _audit_once(store, execution_id, state, runtime.context) if name == "audit" else action(state, runtime)
            if name == "finalize" and state["result"].investigation_stop_reason in {"source_changed", "interrupted_inflight"} and not runtime.context.budget.exceeded:
                update["result"].investigation_stop_reason = state["result"].investigation_stop_reason
            store.save_node(execution_id, epoch, key, name, "done", update)
            return update
        return wrapped
    return wrap


def _run(store, token, *, fresh, client, pause_after, now):
    row = store.authenticate(token)
    execution_id = row["id"]
    with store.lock(execution_id):
        row = store.row(execution_id)
        payload = _CODEC.loads(row["payload"])
        if payload["schema_version"] != SCHEMA_VERSION:
            raise CheckpointError("调查状态版本不兼容，请发起新调查。")
        if row["status"] == "rejected":
            raise UnsupportedRoute("该调查已被拒绝，不能恢复。")
        request = payload["request"]
        if row["status"] == "completed":
            result = _CODEC.loads(row["result"])
            if not isinstance(result, QueryResult):
                raise CheckpointError("保存的最终结果不完整。")
            return _summary(row, token, payload, (), result)
        budget = _PersistentBudget(store, execution_id, payload, now)
        provider = client if client is not None else (core.DeepSeekClient(thinking_enabled=request.thinking_enabled is not False) if request.use_llm else None)
        if isinstance(provider, core.DeepSeekClient) and request.thinking_enabled is not None:
            provider.thinking_enabled = request.thinking_enabled
        if provider is not None:
            provider = _PersistentProvider(provider, budget)
        paths = payload["paths"]
        runtime = InvestigationRuntime(provider, budget, Path(paths["forecast_db"]), Path(paths["sonar_db"]),
                                       Path(paths["catalog"]), Path(paths["root"]), None,
                                       (Path(paths["overall"]), Path(paths["high_water"])))
        config = {"configurable": {"thread_id": execution_id}, "recursion_limit": GRAPH_RECURSION_LIMIT, "callbacks": []}
        # The saver connection is explicitly closed (sqlite's context manager
        # alone commits but does not close a connection on Windows).
        connection = store.connect()
        try:
            saver = SqliteSaver(connection, serde=_CODEC)
            graph = build_investigation_graph(checkpointer=saver,
                                              node_wrapper=_node_wrapper(store, execution_id, row["epoch"]),
                                              interrupt_after=[pause_after] if pause_after else None)
            with tracing_context(enabled=False):
                if not fresh:
                    saved = graph.get_state(config)
                    state = dict(saved.values)
                    if not state or state["request"] != request:
                        raise CheckpointError("保存的请求上下文不匹配，拒绝恢复。")
                    result = deepcopy(state["result"])
                    if result is not None and (result.question != request.question or result.as_of_utc != request.as_of_utc or result.knowledge_checked_on_or_before != request.knowledge_checked_on_or_before):
                        raise CheckpointError("保存的回放时间或问题发生变化，拒绝恢复。")
                    current = _source_context(paths)
                    changed = current != payload["source_context"]
                    if result is not None:
                        previous = core._check_fingerprint(result)
                        if changed:
                            data_before, data_now = payload["source_context"]["data"], current["data"]
                            if data_before["forecast_db"] != data_now["forecast_db"]:
                                result.forecast = None
                            if data_before["sonar_db"] != data_now["sonar_db"]:
                                result.water = None
                            if any(data_before[k] != data_now[k] for k in ("overall", "high_water")):
                                result.evaluation = None
                            result.document_candidates = [hit for hit in result.document_candidates
                                if current["versions"].get(hit.record["id"]) == [hit.record.get("version"), hit.record.get("sha256")]]
                            core.assess_evidence(result, root=runtime.root)
                        verify_final_claims(result, runtime.root)
                        changed = changed or core._check_fingerprint(result) != previous
                    uncertain = store.uncertain(execution_id, row["epoch"])
                    expired = budget.remaining() <= 0
                    if changed or uncertain or expired:
                        reason = "time_limit" if expired else ("interrupted_inflight" if uncertain else "source_changed")
                        if expired:
                            budget.exceeded = "time_limit"
                        result = result or _empty_result(request, execution_id, reason)
                        result.investigation_stop_reason = reason
                        result.warnings.append({"time_limit": "暂停及停机时间计入预算；恢复后不再调用工具或模型。",
                                                "interrupted_inflight": "上次节点调用结果不确定，已保留扣减额度，不自动重复调用。",
                                                "source_changed": "恢复时来源或数据批次发生变化，已重新校验并停止补查。"}[reason])
                        row["epoch"] += 1
                        payload["source_context"] = current
                        store.update(execution_id, epoch=row["epoch"], payload=_CODEC.dumps(payload))
                        # Rebuild the wrapper's journal generation before routing
                        # directly to finalization, never replaying an old result.
                        graph = build_investigation_graph(checkpointer=saver,
                            node_wrapper=_node_wrapper(store, execution_id, row["epoch"]))
                        graph.update_state(config, {"result": result, "llm_available": False}, as_node="decide")
                try:
                    state = graph.invoke(initial_investigation_state(request) if fresh else None,
                                         config=config, context=runtime, durability="sync")
                except UnsupportedRoute:
                    with store.connection() as conn:
                        conn.execute("INSERT OR IGNORE INTO floodpred_audits VALUES(?,?)",
                                     (execution_id, json.dumps({"execution_id": execution_id, "status": "rejected"})))
                    store.update(execution_id, status="rejected")
                    raise
                except Exception as exc:
                    store.update(execution_id, status="interrupted")
                    raise InvestigationInterrupted(token, type(exc).__name__) from exc
                saved = graph.get_state(config)
                status = "paused" if saved.next else "completed"
                result = state.get("result")
                store.update(execution_id, status=status, result=_CODEC.dumps(result))
                return _summary(store.row(execution_id), token, payload, saved.next, result)
        finally:
            connection.close()


def _summary(row, token, payload, next_nodes, result):
    return PersistedInvestigation(row["id"], token, row["status"], tuple(next_nodes), result,
                                  row["tools"], row["llms"],
                                  datetime.fromtimestamp(payload["deadline"], timezone.utc).isoformat())


def start_persisted_investigation(question: str, *, state_db: Path = DEFAULT_STATE_DB,
        as_of_utc: str | None = None, knowledge_checked_on_or_before: str | None = None,
        use_llm: bool = False, thinking_enabled: bool | None = None, client=None,
        allow_web: bool = False,
        forecast_db: Path = core.DEFAULT_DB, sonar_db: Path = core.DEFAULT_SONAR_DB,
        catalog: Path = core.DEFAULT_CATALOG, root: Path = core.ROOT,
        limits: core.InvestigationLimits = core.InvestigationLimits(),
        pause_after: str | None = None, now: Callable[[], float] = time.time) -> PersistedInvestigation:
    if pause_after not in (None, "first_pass", "followup"):
        raise CheckpointError("只支持在首次取证或一轮补查结束后暂停。")
    if type(allow_web) is not bool:
        raise CheckpointError("allow_web 必须为布尔值")
    paths = {name: str(Path(path).resolve()) for name, path in {
        "forecast_db": forecast_db, "sonar_db": sonar_db, "catalog": catalog, "root": root,
        "overall": OVERALL_SUMMARY, "high_water": HIGH_WATER_SUMMARY}.items()}
    if str(Path(state_db).resolve()) in set(paths.values()):
        raise CheckpointError("状态数据库必须独立于证据和历史数据。")
    store = _Store(state_db, create=True)
    execution_id, secret = uuid4().hex, secrets.token_urlsafe(32)
    payload = {"schema_version": SCHEMA_VERSION, "request": InvestigationRequest(question, as_of_utc,
               knowledge_checked_on_or_before, use_llm, thinking_enabled, allow_web),
               "limits": asdict(limits), "deadline": now() + limits.max_seconds,
               "paths": paths, "source_context": _source_context(paths)}
    store.create(execution_id, secret, payload)
    return _run(store, execution_id + "." + secret, fresh=True, client=client, pause_after=pause_after, now=now)


def resume_investigation(resume_token: str, *, state_db: Path = DEFAULT_STATE_DB,
                         client=None, now: Callable[[], float] = time.time) -> PersistedInvestigation:
    """Resume the saved request exactly; no question/time/budget overrides."""
    try:
        return _run(_Store(state_db, create=False), resume_token, fresh=False,
                    client=client, pause_after=None, now=now)
    except (CheckpointError, InvestigationInterrupted, UnsupportedRoute):
        raise
    except (ValueError, TypeError, KeyError, sqlite3.DatabaseError, OSError) as exc:
        raise CheckpointError("保存的调查状态无法读取或不兼容，请发起新调查。") from exc
