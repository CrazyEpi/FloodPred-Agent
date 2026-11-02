"""Small reproducible offline benchmark; --live adds three synthetic API turns."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.conversation import ConversationStore
from app.dialogue import submit_dialogue
from app.knowledge import _load, DEFAULT_CATALOG, _semantic_rank, _legacy_semantic_rank
from app.retrieval_index import semantic_index

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--diagnose-chat', action='store_true')
    args=parser.parse_args()
    report={'live':args.live,'benchmark':{},'turns':[]}
    records=_load(DEFAULT_CATALOG)
    queries=['旧传感器怎么布置','项目内部 Warning 是什么','模型评估有哪些局限']
    for name,fn in [('legacy',_legacy_semantic_rank),('fixed_index',_semantic_rank)]:
        semantic_index.cache_clear()
        samples=[]
        for q in queries*3:
            start=time.perf_counter()
            fn(q,records)
            samples.append(time.perf_counter()-start)
        report['benchmark'][name]={'seconds':samples,'total_seconds':sum(samples),
            'note':'local ranking only; not end-to-end latency or retrieval quality'}
    with tempfile.TemporaryDirectory() as tmp:
        store=ConversationStore(Path(tmp)/'chat.sqlite3')
        cap=store.create()
        from app.rag import DeepSeekClient
        class DiagnosticClient(DeepSeekClient):
            def _call(self,*a,**kw):
                value=super()._call(*a,**kw)
                if args.diagnose_chat and kw.get('stage')=='聊天回复': print('Synthetic chat JSON:',value,flush=True)
                return value
        for q in ['昨天我吃了鸡肉','我昨天吃了什么？当时预测峰值是多少？',
                  '上一次查询的时间是多少？项目内部 Warning 是什么？']:
            result=submit_dialogue(store,cap,q,use_llm=args.live,thinking_enabled=True,allow_web=False,client=DiagnosticClient() if args.live else None)
            report['turns'].append({'question':q,'answer':result.answer,'debug':result.resolution.debug})
            print(json.dumps({'question':q,'answer':result.answer,'calls':result.resolution.debug['llm_calls'],
                              'seconds':result.resolution.debug['elapsed_seconds']},ensure_ascii=False),flush=True)
    path=Path(__file__).resolve().parents[2]/'ProjectTemp-Floodpred'/'reports'/('dialogue_live.json' if args.live else 'dialogue_offline.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Saved',path)

if __name__=='__main__': main()
