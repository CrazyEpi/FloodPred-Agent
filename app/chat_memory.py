"""Selective topic recall, opt-in preferences and source-revalidated rank cache.

No historical assistant answer enters retrieval, a model prompt or an evidence
bundle. Persistent preferences have a separate capability and fixed schema.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import hmac
import json
import math
from pathlib import Path
import re
import secrets
import time
from uuid import uuid4

from .knowledge import _semantic_rank, _tokens

_ACTIVE_CACHE = ContextVar('floodpred_rank_cache', default=None)
HISTORY_CUE = re.compile(r'回到|之前.*问题|之前.*话题|刚才.*问题|上次.*问题|再聊|最开始|最早|前面.*问题')


class MemoryAccessError(ValueError):
    pass


def recall_topics(question, turns, detect_topics, allowed_topics):
    """Recall only allowlisted topic slots from authorized user turns, not answers."""
    if not HISTORY_CUE.search(question):
        return None
    focused = detect_topics(question)
    records, by_id = [], {}
    for turn in turns[-500:]:
        if turn['status'] != 'answered':
            continue
        topics = [x for x in detect_topics(turn['effective_question']) if x in allowed_topics]
        if not topics:
            continue
        if focused and not set(focused) <= set(topics):
            continue
        text = turn['raw_question'][:500]
        record = dict(id=turn['id'], title='', section='', summary=text, keywords=[])
        records.append(record)
        by_id[turn['id']] = (turn, topics)
    if not records:
        return {'status': 'not_found', 'topics': [], 'candidate_count': 0}
    semantic = _semantic_rank(question, records)
    query_terms = set(_tokens(question))
    def score(record):
        terms = set(_tokens(record['summary']))
        overlap = len(query_terms & terms) / max(1, len(query_terms))
        return 0.65 * overlap + 0.35 * semantic.get(record['id'], 0)
    ranked = sorted(records, key=lambda r: (-score(r), -by_id[r['id']][0]['ordinal']))
    unique_topics = {tuple(sorted(topics)) for _, topics in by_id.values()}
    if not focused and len(unique_topics) > 1 and not re.search(r'最开始|最早', question):
        return {'status': 'ambiguous', 'topics': [], 'candidate_count': len(records)}
    selected = ranked[0]
    if re.search(r'最开始|最早', question):
        selected = min(records, key=lambda r: by_id[r['id']][0]['ordinal'])
    turn, topics = by_id[selected['id']]
    return {'status': 'selected', 'topics': topics, 'turn_id': turn['id'],
            'candidate_count': len(records), 'score': round(score(selected), 4),
            'as_of_utc': turn['as_of_utc'],
            'evaluation_scope': ('high_water_2m' if re.search(r'2\s*(?:米|m)以上|高水位', turn['effective_question']) else 'overall') if 'mae' in topics else None,
            'is_evidence': False}


class ChatMemory:
    def __init__(self, store, capability):
        self.store, self.capability = store, capability
        with store.connect() as db:
            store._authorized(db, capability)
            db.executescript('''
            CREATE TABLE IF NOT EXISTS chat_preferences (
              id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, plain_language INTEGER NOT NULL,
              revision INTEGER NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS conversation_preferences (
              conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
              preference_id TEXT NOT NULL REFERENCES chat_preferences(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS chat_rank_cache (
              conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
              cache_key TEXT NOT NULL, created_at REAL NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY(conversation_id,cache_key));
            ''')

    def _profile(self, db, code):
        if not isinstance(code, str) or not re.fullmatch(r'pref\.[a-f0-9]{32}\.[A-Za-z0-9_-]{43}', code):
            raise MemoryAccessError('偏好恢复码无效或已删除。')
        _, pid, token = code.split('.')
        row = db.execute('SELECT * FROM chat_preferences WHERE id=?', (pid,)).fetchone()
        if row is None or not hmac.compare_digest(row['token_hash'], hashlib.sha256(token.encode()).hexdigest()):
            raise MemoryAccessError('偏好恢复码无效或已删除。')
        return row

    def preferences(self):
        with self.store.connect() as db:
            row = self.store._authorized(db, self.capability)
            pref = db.execute('SELECT p.id,p.plain_language,p.revision FROM chat_preferences p JOIN conversation_preferences l ON p.id=l.preference_id WHERE l.conversation_id=?', (row['id'],)).fetchone()
        return ({'id': pref['id'], 'plain_language': bool(pref['plain_language']), 'revision': pref['revision']} if pref else None)

    def save_preferences(self, plain_language, *, confirmed=False, profile_code=None):
        if confirmed is not True or type(plain_language) is not bool:
            raise MemoryAccessError('请明确确认保存跨会话表达偏好。')
        with self.store.connect() as db:
            row = self.store._authorized(db, self.capability)
            if profile_code:
                pref = self._profile(db, profile_code)
                db.execute('UPDATE chat_preferences SET plain_language=?,revision=revision+1,updated_at=? WHERE id=?',
                           (int(plain_language), time.time(), pref['id']))
                pid, code = pref['id'], profile_code
            else:
                pid, token = uuid4().hex, secrets.token_urlsafe(32)
                db.execute('INSERT INTO chat_preferences VALUES (?,?,?,1,?)',
                           (pid, hashlib.sha256(token.encode()).hexdigest(), int(plain_language), time.time()))
                code = 'pref.' + pid + '.' + token
            db.execute('INSERT OR REPLACE INTO conversation_preferences VALUES (?,?)', (row['id'], pid))
            db.execute('UPDATE conversations SET revision=revision+1 WHERE id=?', (row['id'],))
        return code

    def attach_profile(self, code, *, confirmed=False):
        if confirmed is not True:
            raise MemoryAccessError('请确认将此表达偏好用于当前会话。')
        with self.store.connect() as db:
            row = self.store._authorized(db, self.capability)
            pref = self._profile(db, code)
            db.execute('INSERT OR REPLACE INTO conversation_preferences VALUES (?,?)', (row['id'], pref['id']))
            db.execute('UPDATE conversations SET revision=revision+1 WHERE id=?', (row['id'],))

    def forget(self, *, profile_code=None):
        with self.store.connect() as db:
            row = self.store._authorized(db, self.capability)
            if profile_code:
                pref = self._profile(db, profile_code)
                linked = db.execute('SELECT conversation_id FROM conversation_preferences WHERE preference_id=?', (pref['id'],)).fetchall()
                db.execute('DELETE FROM chat_preferences WHERE id=?', (pref['id'],))
                # Cascading links disappear; concurrent submissions must re-read settings.
                db.executemany('UPDATE conversations SET revision=revision+1 WHERE id=?', [(r[0],) for r in linked])
            else:
                db.execute('DELETE FROM conversation_preferences WHERE conversation_id=?', (row['id'],))
                db.execute('UPDATE conversations SET revision=revision+1 WHERE id=?', (row['id'],))


class RankCache:
    def __init__(self, memory, context, *, ttl=900, clock=time.time):
        if not 1 <= ttl <= 86400:
            raise ValueError('缓存 TTL 必须为 1–86400 秒。')
        self.memory, self.context, self.ttl, self.clock = memory, context, ttl, clock
        self.events = {'hits': 0, 'misses': 0, 'expired': 0, 'invalid': 0, 'sources_rechecked': False}
        self.revision = memory.store.load(memory.capability)['revision']
        root = Path(__file__).resolve().parent
        self.algorithm = hashlib.sha256(b''.join((root / name).read_bytes() for name in ('hybrid_retrieval.py','knowledge.py'))).hexdigest()

    def scores(self, channel, query, records, texts, compute):
        self.events['sources_rechecked'] = True
        signature = [self.algorithm, self.context, channel, query, records, texts]
        key = hashlib.sha256(json.dumps(signature, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        store = self.memory.store
        with store.connect() as db:
            row = store._authorized(db, self.memory.capability)
            cache = db.execute('SELECT * FROM chat_rank_cache WHERE conversation_id=? AND cache_key=?', (row['id'], key)).fetchone()
        if cache:
            age = self.clock() - cache['created_at']
            if 0 <= age < self.ttl:
                try:
                    values = json.loads(cache['payload'])
                    ids = {r['id'] for r in records}
                    if not isinstance(values, dict) or not set(values) <= ids or any(
                        type(v) not in (float,int) or not math.isfinite(v) or v < 0 for v in values.values()):
                        raise ValueError('invalid ranking')
                    self.events['hits'] += 1
                    return values
                except (ValueError, TypeError):
                    self.events['invalid'] += 1
            else:
                self.events['expired'] += 1
        self.events['misses'] += 1
        values = compute()
        with store.connect() as db:
            row = store._authorized(db, self.memory.capability)
            if row['revision'] == self.revision:
                db.execute('DELETE FROM chat_rank_cache WHERE conversation_id=? AND created_at<?', (row['id'], self.clock()-self.ttl))
                db.execute('INSERT OR REPLACE INTO chat_rank_cache VALUES (?,?,?,?)',
                           (row['id'], key, self.clock(), json.dumps(values, allow_nan=False)))
                db.execute('DELETE FROM chat_rank_cache WHERE conversation_id=? AND cache_key NOT IN (SELECT cache_key FROM chat_rank_cache WHERE conversation_id=? ORDER BY created_at DESC LIMIT 256)', (row['id'],row['id']))
        return values


@contextmanager
def ranking_scope(cache):
    token = _ACTIVE_CACHE.set(cache)
    try:
        yield cache
    finally:
        _ACTIVE_CACHE.reset(token)


def cached_scores(channel, query, records, texts, compute):
    cache = _ACTIVE_CACHE.get()
    return compute() if cache is None else cache.scores(channel, query, records, texts, compute)
