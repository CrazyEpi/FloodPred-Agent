"""Local capability-scoped chat memory. History is context, never evidence.

Business archives stay read-only. Only this separate conversation DB is written.
No pickle, raw reasoning, credentials, implicit cross-user listing or LLM SQL.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from uuid import uuid4

from .archive import parse_utc
from .checkpoint_codec import CheckpointCodec
from .rag import DeepSeekClient
from .router import QueryResult, UnsupportedRoute, _reject_unsafe_intent

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIME = '2026-08-03T01:48:00Z'
TOPICS = {
    'peak': '当时预测峰值', 'water': '当时实测水位', 'mae': 'MAE',
    'caution': '项目内部 Caution', 'warning': '项目内部 Warning',
    'severe': '项目内部 Severe', 'old_sensor': 'House Mill 旧传感器',
    'method': 'FloodPred 预测方法', 'housemill': 'House Mill 背景',
}
PATTERNS = {
    'peak': r'峰值|最高水位|预测最高', 'water': r'实测水位|观测水位|当时水位|历史水位',
    'mae': r'(?<![a-zA-Z])mae(?![a-zA-Z])|平均绝对误差|平均误差', 'caution': r'caution|watch',
    'warning': r'(?<!flood )warning|警告等级', 'severe': r'severe',
    'old_sensor': r'旧传感器|旧声纳|旧系统|以前测水|早期传感器',
    'method': r'预测方法|预测模型|patchtst', 'housemill': r'house\s*mill|三磨坊',
}
FOLLOW = re.compile(r'它|这个|那个|刚才|上一个|继续|再说|再解释|通俗|简单点|换成|换到|另一|同样|那时候|什么时候到|讲人话')
ATTRIBUTES = {'explain': '是什么？请用通俗的话解释。', 'time': '目标时间是什么？',
              'value': '是多少？', 'location': '是怎么布置的？',
    'limitations': '有哪些局限？', 'cause': '为什么？', 'repeat': '请继续解释，尽量通俗。'}


class ConversationError(ValueError):
    pass


def stamp():
    return datetime.now(timezone.utc).isoformat()


def chat_db_path():
    return Path(os.environ.get('FLOODPRED_CHAT_DB', ROOT / 'state' / 'conversations.sqlite3'))


class ConversationStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or chat_db_path())
        # Never permit a mistaken configuration to create chat tables in evidence DBs.
        if self.path.name in {'prediction_history.sqlite3', 'sonar_history.sqlite3', 'investigations.sqlite3'}:
            raise ConversationError('会话数据库必须与预测、声纳和检查点数据库分开。')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS conversations (
              id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, title TEXT NOT NULL,
              state_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chat_turns (
              id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
              ordinal INTEGER NOT NULL, raw_question TEXT NOT NULL,
              effective_question TEXT NOT NULL, as_of_utc TEXT,
              answer TEXT NOT NULL, result_json TEXT, status TEXT NOT NULL,
              context_json TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(conversation_id, ordinal),
              FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self):
        cid, token = uuid4().hex, secrets.token_urlsafe(32)
        state = {'topics': [], 'as_of_utc': DEFAULT_TIME, 'last_question': '',
                 'run_id': None, 'evaluation_version': None, 'evaluation_scope': None,
                 'site': 'house_mill', 'plain_language': False}
        with self.connect() as db:
            db.execute('INSERT INTO conversations VALUES (?, ?, ?, ?, 0, ?, ?)',
                       (cid, hashlib.sha256(token.encode()).hexdigest(), '新对话', json.dumps(state), stamp(), stamp()))
        return cid + '.' + token

    def _authorized(self, db, capability):
        if not isinstance(capability, str) or not re.fullmatch(r'[a-f0-9]{32}\.[A-Za-z0-9_-]{43}', capability):
            raise ConversationError('恢复码无效或会话已删除。')
        cid, token = capability.split('.')
        row = db.execute('SELECT * FROM conversations WHERE id=?', (cid,)).fetchone()
        if row is None or not hmac.compare_digest(row['token_hash'], hashlib.sha256(token.encode()).hexdigest()):
            raise ConversationError('恢复码无效或会话已删除。')
        return row

    def load(self, capability):
        with self.connect() as db:
            row = self._authorized(db, capability)
            turns = db.execute('SELECT * FROM chat_turns WHERE conversation_id=? ORDER BY ordinal', (row['id'],)).fetchall()
        return {**dict(row), 'state': json.loads(row['state_json']), 'turns': [dict(t) for t in turns]}

    def append(self, capability, *, expected_revision, raw, resolution, answer, result, status, state):
        payload = CheckpointCodec().dumps(result) if result is not None else None
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._authorized(db, capability)
            if row['revision'] != expected_revision:
                raise ConversationError('对话已在另一页面更新，请刷新后重试，避免覆盖新上下文。')
            turn_id = uuid4().hex
            db.execute('INSERT INTO chat_turns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                       (turn_id, row['id'], expected_revision + 1, raw, resolution.question,
                        resolution.as_of_utc, answer, payload, status,
                        json.dumps(resolution.debug, ensure_ascii=False), stamp()))
            db.execute('UPDATE conversations SET state_json=?, revision=revision+1, title=?, updated_at=? WHERE id=?',
                       (json.dumps(state, ensure_ascii=False), raw[:32] if row['title'] == '新对话' else row['title'], stamp(), row['id']))
        return turn_id

    def clear(self, capability, delete=False):
        with self.connect() as db:
            row = self._authorized(db, capability)
            if delete:
                db.execute('DELETE FROM conversations WHERE id=?', (row['id'],))
            else:
                db.execute('DELETE FROM chat_turns WHERE conversation_id=?', (row['id'],))
                # New memory tables are optional for old installations.
                for table in ('chat_rank_cache', 'conversation_preferences', 'chat_summaries'):
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                        db.execute(f'DELETE FROM {table} WHERE conversation_id=?', (row['id'],))
                initial = {'topics': [], 'as_of_utc': DEFAULT_TIME, 'last_question': '', 'run_id': None,
                           'evaluation_version': None, 'evaluation_scope': None,
                           'site': 'house_mill', 'plain_language': False}
                db.execute('UPDATE conversations SET state_json=?, revision=revision+1, title=?, updated_at=? WHERE id=?',
                           (json.dumps(initial), '新对话', stamp(), row['id']))


@dataclass
class Resolution:
    question: str
    as_of_utc: str | None
    topics: list[str] = field(default_factory=list)
    clarification: str | None = None
    debug: dict = field(default_factory=dict)


def detect_topics(question):
    found = [key for key, pattern in PATTERNS.items() if any(
        not re.search(r'不是\s*$|别查\s*$|不要查\s*$', question[max(0, m.start()-6):m.start()])
        for m in re.finditer(pattern, question, re.I))]
    # House Mill is a site qualifier when a more specific topic is present.
    return [key for key in found if key != 'housemill'] if len(found) > 1 else found


def resolve_question(raw, state, *, as_of_utc=None, client=None, context_budget=65536, context_data=None):
    _reject_unsafe_intent(raw)
    if not raw.strip():
        raise ConversationError('请先输入问题。')
    if not 2048 <= context_budget <= 200000:
        raise ConversationError('上下文预算需在 2048–200000 之间。')
    current = detect_topics(raw)
    previous = [t for t in state.get('topics', []) if t in TOPICS]
    cutoff = as_of_utc or state.get('as_of_utc') or DEFAULT_TIME
    debug = {'original_question': raw, 'origin': 'rules', 'context_budget': context_budget,
             'inherited_topics': [], 'history_is_evidence': False}
    out = Resolution(raw, cutoff, current, debug=debug)
    timestamps = re.findall(r'\d{4}-\d{2}-\d{2}[T ][\d:]+(?:Z|[+-]\d{2}:\d{2})', raw)
    if timestamps:
        cutoff = parse_utc(timestamps[-1]).isoformat().replace('+00:00', 'Z')
        out.as_of_utc = cutoff
    elif re.search(r'\d{4}-\d{2}-\d{2}', raw):
        out.clarification = '请给出完整的回放时刻（含时间和时区，例如 2026-08-04T01:48:00Z），仅日期无法确定哪次预测。'
        return out
    parse_utc(cutoff)
    if (re.search(r'换一天|另一天|^(?:那)?(?:明天|昨天)(?:呢|的呢)?[？?。]*$', raw) and not timestamps
            and cutoff == state.get('as_of_utc')):
        out.clarification = '请指定完整的 UTC 回放时刻，或修改下方时间框；我不会自行把“明天”换成某个历史日期。'
        return out
    follow = bool(FOLLOW.search(raw)) or bool(timestamps and not current) or bool(current and previous and len(raw) < 12)
    if (re.search(r'它|这个|那个', raw) and re.search(r'关系|联系|区别|比较', raw)
            and current and len(previous) == 1 and previous[0] not in current):
        out.question = re.sub(r'它|这个|那个', TOPICS[previous[0]], raw, count=1)
        out.topics = [previous[0], *current]
        debug.update(inherited_topics=previous, effective_question=out.question)
        return out
    # A topic switch is explicit. Retain time but never earlier topic/numbers.
    if current and not re.search(r'它|刚才|那个|这个|上一个', raw) and not (previous and (follow or len(raw) < 12)):
        return out
    if not follow:
        return out
    candidates = current or previous
    if not candidates:
        out.clarification = '你想继续了解哪一项？可以说预测峰值、实测水位、MAE，或旧传感器。'
        return out
    if len(candidates) != 1:
        out.clarification = '你指的是' + '、'.join(TOPICS[t] for t in candidates) + '中的哪一项？'
        return out
    topic = candidates[0]
    attribute = ('time' if re.search(r'什么时候|几点|时间|何时', raw) else
                 'location' if re.search(r'哪里|哪儿|布置|放哪', raw) else
                 'value' if re.search(r'多少|多高', raw) else
                 'limitations' if re.search(r'局限|限制|不足', raw) else
                 'cause' if re.search(r'为什么|为何', raw) else 'explain')
    if client is not None:
        # Model chooses only an enum. It cannot author queries, dates, facts or tools.
        try:
            from .chat_context import assemble_context
            context_packet = assemble_context(raw, state, *(context_data or ([], [])),
                                              topic=topic, attributes=ATTRIBUTES, budget=context_budget)
            debug['context_assembly'] = context_packet.trace
            proposal = json.loads(client._call(context_packet.messages,
                max_tokens=max(256, min(context_packet.trace['output_reserve'], int(os.environ.get('FLOODPRED_CHAT_PLAN_MAX_TOKENS', '4096')))),
                thinking=False, stage='上下文指代'))
            if set(proposal) != {'attribute'} or proposal['attribute'] not in ATTRIBUTES:
                raise ValueError('invalid context proposal')
            # Explicit time/location/number cues always override an LLM suggestion.
            if attribute == 'explain':
                attribute = proposal['attribute']
            debug['origin'] = 'rules+validated_model'
        except Exception as exc:
            debug['model_fallback'] = '规则解析；模型分类失败或未配置'
            debug['model_error_type'] = type(exc).__name__
    canonical = TOPICS[topic]
    if topic == 'mae' and state.get('evaluation_scope') == 'high_water_2m':
        canonical = '2米以上高水位 MAE'
    # Re-run evidence tools instead of copying an earlier assistant answer.
    if topic == 'peak':
        canonical += '是多少？预测峰值目标时间是什么？' if attribute == 'time' else '是多少？'
    elif topic == 'water':
        canonical += '是多少？'
    elif topic == 'old_sensor':
        canonical += '是怎么布置的？'
    else:
        canonical += ATTRIBUTES[attribute]
    if re.search(r'通俗|简单|讲人话', raw):
        canonical += '请少用术语。'
    if re.search(r'不要联网|不联网|别联网|禁止联网', raw):
        canonical += '不要联网。'
    if len(canonical) > 500:
        raise ConversationError('补全问题过长，请拆成两次提问。')
    # Preserve the entire current utterance: negation, official/internal scope,
    # source restrictions and explicit requested attributes may not be discarded.
    canonical = '关于' + TOPICS[topic] + '，' + raw + '；' + canonical
    _reject_unsafe_intent(canonical)
    out.question, out.topics = canonical, [topic]
    debug['inherited_topics'] = [] if current else [topic]
    debug['effective_question'] = canonical
    debug['time_changed'] = cutoff != state.get('as_of_utc')
    return out


@dataclass
class ChatOutcome:
    answer: str
    result: QueryResult | None
    resolution: Resolution
    status: str
    turn_id: str


def submit_turn(store, capability, raw, *, as_of_utc=None, use_llm=False,
                thinking_enabled=True, allow_web=False, use_investigation=True,
                runner=None, client=None, context_budget=65536, cache_clock=None, cache_ttl=900):
    from .chat_memory import ChatMemory, RankCache, ranking_scope, recall_topics
    _reject_unsafe_intent(raw)
    memory = ChatMemory(store, capability)
    snapshot = store.load(capability)
    from .chat_context import extractive_summary, assemble_context
    summaries, summary_reused = extractive_summary(store, capability, snapshot, detect_topics)
    old = dict(snapshot['state'])
    recalled = recall_topics(raw, snapshot['turns'], detect_topics, TOPICS)
    if recalled and recalled['status'] == 'selected':
        old.update(topics=recalled['topics'], run_id=None, evaluation_version=None,
                   evaluation_scope=recalled.get('evaluation_scope'))
        # Explicitly returning to a topic never silently returns to an old date.
        if re.search(r'上次那个时间|那个回放时刻|那次回放时间', raw):
            old['as_of_utc'] = recalled['as_of_utc']
            as_of_utc = old['as_of_utc']
    provider = client or (DeepSeekClient(thinking_enabled=thinking_enabled, request_timeout_seconds=20) if use_llm else None)
    resolution = resolve_question(raw, old, as_of_utc=as_of_utc, client=provider,
                                  context_budget=context_budget, context_data=(snapshot['turns'], summaries))
    resolution.debug['summary'] = {'entries': len(summaries), 'reused': summary_reused, 'extractive': True}
    # Offline/debug mode also records the actual assembly policy for inspection.
    if 'context_assembly' not in resolution.debug:
        resolution.debug['context_assembly'] = assemble_context(raw, old, snapshot['turns'], summaries,
            topic=(resolution.topics or [''])[0], attributes=ATTRIBUTES, budget=context_budget).trace
    if recalled:
        resolution.debug['history_recall'] = recalled
        if recalled['status'] != 'selected':
            resolution.clarification = ('前面有多个不同问题，请说明想回到哪个话题。' if recalled['status'] == 'ambiguous'
                                        else '当前会话没有找到你说的那个历史问题，请补充话题。')
        else:
            # History retrieval selected a topic, not an answer or a new instruction.
            resolution = resolve_question(raw + '；继续解释' + TOPICS[recalled['topics'][0]], old,
                                          as_of_utc=as_of_utc, client=None, context_budget=context_budget)
            resolution.debug.update(original_question=raw, history_recall=recalled)
            resolution.debug['summary'] = {'entries': len(summaries), 'reused': summary_reused, 'extractive': True}
            resolution.debug['context_assembly'] = assemble_context(raw, old, snapshot['turns'], summaries,
                topic=recalled['topics'][0], attributes=ATTRIBUTES, budget=context_budget).trace
            if len(recalled['topics']) > 1 and not detect_topics(raw):
                resolution.clarification = '那个问题包含' + '、'.join(TOPICS[t] for t in recalled['topics']) + '，你想继续哪一项？'
    state = dict(old)
    if re.search(r'以后|接下来|后面都', raw):
        if re.search(r'通俗|少用术语|简单点|讲人话', raw):
            state['plain_language'] = True
        elif re.search(r'专业一点|使用术语|不用通俗', raw):
            state['plain_language'] = False
    preferences = memory.preferences()
    preference_value = state.get('plain_language_override')
    if re.search(r'以后|接下来|后面都', raw) and re.search(r'通俗|少用术语|简单点|讲人话|专业一点|使用术语|不用通俗', raw):
        preference_value = state['plain_language']
        state['plain_language_override'] = preference_value
    use_plain = preference_value if preference_value is not None else (state.get('plain_language') or bool(preferences and preferences['plain_language']))
    if use_plain and not resolution.clarification:
        resolution.question += '请少用术语，用通俗中文回答。'
        _reject_unsafe_intent(resolution.question)
    resolution.debug['preference_memory'] = preferences
    # Changing time invalidates bindings even when the request needs clarification.
    if resolution.as_of_utc != old.get('as_of_utc'):
        state.update(run_id=None, evaluation_version=None, evaluation_scope=None)
    state['as_of_utc'] = resolution.as_of_utc
    result = None
    if resolution.clarification:
        answer, status = resolution.clarification, 'clarify'
    else:
        if runner is None:
            from .investigation import run_investigation
            from .router import run_query
            runner = run_investigation if use_investigation else run_query
        kwargs = dict(as_of_utc=resolution.as_of_utc, use_llm=use_llm, thinking_enabled=thinking_enabled)
        if use_investigation:
            kwargs['allow_web'] = allow_web and not bool(re.search(r'不要联网|不联网|别联网|禁止联网', raw))
        try:
            scope = {'as_of_utc': resolution.as_of_utc, 'site': state.get('site', 'house_mill'),
                     'evaluation_scope': state.get('evaluation_scope')}
            cache = RankCache(memory, scope, ttl=cache_ttl,
                              **({'clock': cache_clock} if cache_clock else {}))
            with ranking_scope(cache):
                result = deepcopy(runner(resolution.question, **kwargs))
            resolution.debug['rank_cache'] = dict(cache.events)
            # The running app may still hold the documented pre-web result shape.
            for legacy_field in ('web_evidence', 'web_runs'):
                if not hasattr(result, legacy_field):
                    setattr(result, legacy_field, [])
            if resolution.debug.get('inherited_topics') and resolution.as_of_utc == old.get('as_of_utc'):
                if result.forecast and old.get('run_id') and result.forecast.run_id != old['run_id']:
                    raise ConversationError('同一回放时刻的预测来源已变化；请明确重新查询预测峰值，不沿用旧预测绑定。')
                if result.evaluation and old.get('evaluation_version') and result.evaluation.generated_utc != old['evaluation_version']:
                    raise ConversationError('评估资料版本已变化；请明确重新查询 MAE，不把新旧版本混为同一结果。')
            from .presentation import ResultPresentation
            answer = ResultPresentation(result).answer() if result.answer_text else ''
            if not answer:
                if result.forecast:
                    answer = f'当时预测峰值 {result.forecast.predicted_peak_m:.4f} m；目标时间 {result.forecast.predicted_peak_utc}。'
                elif result.evaluation:
                    answer = f'MAE {result.evaluation.mae_m:.6f} {result.evaluation.unit}；版本 {result.evaluation.generated_utc}。'
                elif result.water:
                    answer = f'当时实测水位 {result.water.water_m:.3f} m。'
                else:
                    answer = '\n'.join(result.errors or [k.fact for k in result.knowledge]) or '本轮没有足够的已核验资料。'
            status = 'error' if result.errors else 'answered'
            if not result.errors and result.response_mode != 'greeting':
                state.update(topics=resolution.topics, last_question=resolution.question,
                             run_id=result.forecast.run_id if result.forecast else None,
                             evaluation_version=result.evaluation.generated_utc if result.evaluation else None,
                             evaluation_scope=result.evaluation.scope if result.evaluation else None)
            result.trace.append({'step': 'conversation_context', 'status': 'ok',
                                 'original_question': raw, 'effective_question': resolution.question,
                                 'as_of_utc': resolution.as_of_utc})
        except UnsupportedRoute as exc:
            result, answer, status = None, str(exc), 'error'
        except ConversationError as exc:
            result, answer, status = None, str(exc), 'error'
        except Exception:
            # No exception text: network paths, provider payloads or secrets may be present.
            answer, status = '本轮执行失败，未沿用上一次的答案。你可以重试，或查看本机运行日志。', 'error'
    turn_id = store.append(capability, expected_revision=snapshot['revision'], raw=raw,
                           resolution=resolution, answer=answer, result=result, status=status, state=state)
    return ChatOutcome(answer, result, resolution, status, turn_id)
