"""Record a small, synthetic end-to-end acceptance set, optionally with DeepSeek."""
import sys,json,tempfile,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.conversation import ConversationStore
from app.dialogue import submit_dialogue

def main():
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');args=p.parse_args()
    rows=[]
    cases=[('你是谁','FloodPred'),
        ('2025年12月31日23点45分 UTC 的内部水位跟前一天比涨了吗？','1.3025'),
        ('那天早上八点河里多高？','Lea Bridge'),
        ('2023年1月2日早上8点的水位是多少？项目内部 Warning 是什么？','4.43')]
    with tempfile.TemporaryDirectory() as tmp:
        s=ConversationStore(Path(tmp)/'chat.sqlite3');cap=s.create()
        for question,expected in cases:
            result=submit_dialogue(s,cap,question,use_llm=args.live,time_mode='normal',allow_web=False)
            row={'question':question,'expected_substring':expected,'passed':expected in result.answer,
                 'answer':result.answer,'debug':result.resolution.debug}
            rows.append(row);print(json.dumps({k:row[k] for k in ('question','passed','answer')},ensure_ascii=False),flush=True)
    dest=Path(__file__).resolve().parents[2]/'ProjectTemp-Floodpred'/'reports'/('information_live.json' if args.live else 'information_offline.json')
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    if not all(r['passed'] for r in rows):sys.exit(1)
if __name__=='__main__':main()
