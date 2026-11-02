"""Read-only historical source registry and exact point queries. No free SQL."""
import csv
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timedelta

ROOT=Path(__file__).resolve().parents[1]
DATA=Path(os.environ.get('FLOODPRED_HISTORY_DATA',ROOT.parent/'CASA0022-Dissertation-FloodPredict'/'data'))
CATALOG=ROOT/'knowledge'/'history_sources.json'

def rows(path):
    with path.open(encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(line for line in f if not line.startswith('#')):
            if row.get('_time') and row['_time']!='_time': yield row

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def build_catalog(data=DATA):
    sources=[]
    files=list((data/'data'/'sonar').glob('*.csv'))+[data/n for n in ('house_mill_processed.csv','house_mill_integrated_dataset.csv')]
    for path in sorted(files):
        if not path.is_file(): continue
        first,last,count,fields=None,None,0,set()
        for row in rows(path):
            stamp=row['_time']; first=min(first or stamp,stamp); last=max(last or stamp,stamp); count+=1
            fields.update([row['water-height']] if 'water-height' in row else row.keys())
        kind='raw_sensor' if path.parent.name=='sonar' else 'integrated_training' if 'integrated' in path.name else 'processed_history'
        sources.append(dict(id=path.stem,relative_path=str(path.relative_to(data)).replace('\\','/'),sha256=digest(path),
            first=first,last=last,rows=count,fields=sorted(fields),kind=kind,timezone='UTC',
            unit='channel-specific: sonar distance mm; station heights m' if kind=='raw_sensor' else 'internal_water_m: m, sensor-relative, not official datum',
            processing='none recorded by importer' if kind=='raw_sensor' else 'derived; review pipeline: median filtering, 15-minute mean, clipping; integrated also includes interpolation',
            availability='historical event time only; original ingestion time unknown'))
    return {'schema_version':1,'sources':sources,
        'field_definitions':{
            'internal_water_m':{'site':'House Mill internal','unit':'m','datum':'sensor-relative; reference pipeline uses 5m minus sonar distance; not official elevation datum'},
            'sonar_dist_mm':{'site':'House Mill sonar','unit':'mm','meaning':'distance, not water height'},
            **{f:{'site':site,'unit':'m','datum':'station-specific, not verified comparable to House Mill'} for f,site in [('lea_height_m','Lea Bridge'),('silver_tidal_m','Silvertown'),('tower_tidal_m','Tower Pier')]}},
        'reference_pipeline_hashes_not_generation_proof':{str(p.relative_to(data)):digest(p) for p in [data/'main_pipeline.py',data/'processors'/'sonar_processor.py'] if p.is_file()},
        'warning':'Processed CSV versions are not interchangeable. Raw distance is not water height.'}

def query_history(utc, *, field='internal_water_m', source_kind='processed_history', data=DATA, catalog=CATALOG,
                  mode='exact', end_utc=None, tolerance_minutes=15):
    if field not in ('internal_water_m','lea_height_m','silver_tidal_m','tower_tidal_m','sonar_dist_mm'): raise ValueError('unsupported field')
    target=datetime.fromisoformat(utc.replace('Z','+00:00'))
    if target.tzinfo is None: raise ValueError('timezone required')
    if mode not in ('exact','nearest','mean','max'): raise ValueError('unsupported operation')
    if not 1<=tolerance_minutes<=60: raise ValueError('invalid tolerance')
    end=datetime.fromisoformat(end_utc.replace('Z','+00:00')) if end_utc else target
    if mode in ('mean','max') and (not end_utc or end.tzinfo is None or not target<end<=target+timedelta(days=31)):
        raise ValueError('range must be positive and at most 31 days')
    low=target-timedelta(minutes=tolerance_minutes) if mode=='nearest' else target
    high=target+timedelta(minutes=tolerance_minutes) if mode=='nearest' else end
    meta=json.loads(catalog.read_text(encoding='utf-8'))
    candidates=[s for s in meta['sources'] if s['kind']==source_kind and s['first'] and
        datetime.fromisoformat(s['first'].replace('Z','+00:00'))<=high and datetime.fromisoformat(s['last'].replace('Z','+00:00'))>=low]
    base={'query_utc':utc,'end_utc':end_utc,'operation':mode,'field':field,'source_kind':source_kind,'status':'no_data','matches':[]}
    if not candidates:
        base['reason']='该数据源不覆盖查询时刻。'
        base['coverage']=[{'first':s['first'],'last':s['last'],'kind':s['kind']} for s in meta['sources'] if s['kind']==source_kind]
        return base
    for s in candidates:
        path=(data/s['relative_path']).resolve()
        if not path.is_relative_to(data.resolve()) or digest(path)!=s['sha256']: raise ValueError('来源缺失或哈希变更，请重新核对目录。')
        for number,row in enumerate(rows(path),2):
            stamp=datetime.fromisoformat(row['_time'].replace('Z','+00:00'))
            if mode=='exact' and stamp!=target: continue
            if mode=='nearest' and not low<=stamp<=high: continue
            if mode in ('mean','max') and not target<=stamp<end: continue
            if source_kind=='raw_sensor':
                topics={'sonar_dist_mm':'/sonar/distance','lea_height_m':'/LeaBridgeRiver/height','silver_tidal_m':'/SilvertownTidal/height','tower_tidal_m':'/TowerPierTidal/height'}
                if field not in topics or not row.get('water-height','').endswith(topics[field]): continue
                value=row.get('_value')
            else: value=row.get(field)
            if value is None or not value.strip(): continue
            import math
            value=float(value)
            if not math.isfinite(value): continue
            quality=[]
            if field=='internal_water_m' and float(row.get('sonar_dist_mm_cleaned') or 0)>=4800:
                quality.append('声纳清洗距离达到异常／默认值区间，换算水位不能作为可靠实测值。')
            base['matches'].append({'value':value,'recorded_utc':row['_time'],'source':s,'record_index':number-1,'unit':'mm' if field=='sonar_dist_mm' else 'm','quality_warnings':quality})
    if mode=='nearest' and base['matches']:
        distance=lambda m:abs((datetime.fromisoformat(m['recorded_utc'].replace('Z','+00:00'))-target).total_seconds())
        closest=min(map(distance,base['matches']))
        base['matches']=[m for m in base['matches'] if distance(m)==closest]
        if len({m['recorded_utc'] for m in base['matches']})>1:
            base.update(status='conflict',reason='存在等距离的前后记录，请明确选择时刻。')
            return base
        base['offset_seconds']=closest
    base['status']='found' if base['matches'] else 'no_exact_record'
    grouped={}
    for m in base['matches']: grouped.setdefault(m['recorded_utc'],set()).add(m['value'])
    if any(len(v)>1 for v in grouped.values()): base['status']='conflict'
    if base['status']=='found' and any(m['quality_warnings'] for m in base['matches']):
        base['status']='quality_issue'
        base['reason']='记录存在，但声纳处于异常／默认值区间，不能给出可信水位。请检查原始传感器质量。'
    if base['status']!='quality_issue': base['reason']='没有精确匹配的有效记录；未自动插值或使用邻近值。' if not base['matches'] else ''
    if mode in ('mean','max') and base['status']=='found':
        # Duplicates cannot increase the denominator; conflicts were rejected above.
        valid={m['recorded_utc']:m['value'] for m in base['matches']}
        vals=list(valid.values())
        base['aggregate']={'value':sum(vals)/len(vals) if mode=='mean' else max(vals),
            'sample_count':len(vals),'first':min(valid),'last':max(valid),
            'meaning':'available sample mean, not time-weighted mean' if mode=='mean' else 'maximum of available samples',
            'missingness':'not established; do not claim continuous coverage'}
        # Bounded source evidence; aggregate calculated over ALL valid rows, not this excerpt.
        base['matches']=base['matches'][:3]
    return base

def render_history(result):
    if result['status']!='found':
        return f"查询时刻：{result['query_utc']}。{result.get('reason') or '来源存在冲突，不能自动选取。'}"
    m=result['matches'][0]; s=m['source']
    if result.get('aggregate'):
        a=result['aggregate']; op='可用样本均值（非时间加权）' if result['operation']=='mean' else '可用样本最大值'
        return f"{result['query_utc']} 至 {result['end_utc']}（左闭右开）的{op}为 {a['value']:.4f} {m['unit']}；分母为 {a['sample_count']} 个有效时间点。不代表连续完整覆盖。来源：{s['relative_path']}（{s['kind']}，SHA-256 {s['sha256'][:12]}）。"
    label={'processed_history':'处理后历史数据（不是未经处理的原始实测）','raw_sensor':'原始传感器记录','integrated_training':'整合训练数据（可能含插值或截断）'}[s['kind']]
    nearest=f"查询目标 {result['query_utc']}；返回邻近记录，时间差 {result.get('offset_seconds',0):g} 秒。" if result.get('operation')=='nearest' else ''
    return nearest+f"{m['recorded_utc']} 的 {result['field']} 为 {m['value']:.4f} {m['unit']}。来源：{label}，{s['relative_path']}，记录序号 {m['record_index']}。处理说明：{s['processing']}。原始入库时间未知，不能据此声称当时已可获得该记录。"
