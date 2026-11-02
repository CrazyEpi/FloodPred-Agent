"""Create a derived manifest only; never alter source CSVs."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.history_data import build_catalog,CATALOG
if __name__=='__main__':
    result=build_catalog()
    CATALOG.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Registered',len(result['sources']),'sources:',CATALOG)
