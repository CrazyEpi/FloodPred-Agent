"""Anchored Chinese/English point-time parsing; never silently default a date."""
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import re

@dataclass
class QueryTime:
    status: str
    utc: str | None = None
    zone: str = 'Europe/London'
    original: str = ''
    note: str = ''

def number(s):
    if s.isdigit(): return int(s)
    digits = dict(zip('零一二三四五六七八九', range(10)))
    if s == '两': return 2
    if '十' in s:
        a,b=s.split('十'); return (digits.get(a,1)*10)+digits.get(b,0)
    return int(''.join(str(digits[c]) for c in s))

def parse_query_time(text, *, now=None, override=None):
    now=now or datetime.now(timezone.utc)
    if override:
        if re.search(r'比较|对比|时间段|之间|至|分别',text):
            return QueryTime('clarify',note='这是多时刻／区间查询，请关闭调试时间覆盖后再查。')
        try:
            dt=datetime.fromisoformat(override.replace('Z','+00:00'))
            if dt.tzinfo is None: raise ValueError()
            return QueryTime('parsed',dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z'),'UTC',text,'调试模式：原文业务时间已被侧栏时刻覆盖。')
        except ValueError: return QueryTime('invalid',note='调试时间必须是包含时区的完整时间。')
    zone='Asia/Shanghai' if re.search(r'北京时间|中国时间|Beijing|China time',text,re.I) else 'UTC' if re.search(r'\bUTC\b|\bGMT\b',text,re.I) else 'Europe/London'
    dates=re.findall(r'\d{2,4}年\d{1,2}月\d{1,2}[日号]|\d{4}-\d{1,2}-\d{1,2}',text)
    if len(dates)>1 or len(re.findall(r'昨天|今天|前天|明天|\byesterday\b|\btoday\b|\btomorrow\b',text,re.I))>1:
        return QueryTime('clarify',note='这条请求包含多个日期。当前点时查询请分开提问，避免把多个日期合成一个时刻。')
    iso=re.search(r'\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?',text)
    try:
        note=''
        if iso:
            dt=datetime.fromisoformat(iso.group().replace('Z','+00:00'))
            original=iso.group()
            if dt.tzinfo is not None:
                return QueryTime('parsed',dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z'),'explicit offset',original)
        else:
            cn=re.search(r'(\d{2,4})年(\d{1,2})月(\d{1,2})[日号]',text)
            simple=re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})',text)
            slash=re.search(r'\d{1,2}/\d{1,2}/\d{2,4}',text)
            if slash: return QueryTime('clarify',original=slash.group(),note='斜杠日期的月／日顺序不明确，请用 YYYY-MM-DD。')
            eng=re.search(r'(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})',text,re.I)
            eng2=re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),?\s+(\d{4})',text,re.I)
            rel=re.search(r'昨天|今天|前天|明天|yesterday|today|tomorrow',text,re.I)
            if cn or simple:
                m=cn or simple; y,mo,day=map(int,m.groups()); original=m.group()
                if y<100: y+=2000; note=f'两位年份按 {y} 年理解。'
            elif eng or eng2:
                m=eng or eng2; a,b,y=m.groups(); month=b if eng else a; day=int(a if eng else b)
                mo=['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'].index(month[:3].lower())+1
                y=int(y); original=m.group()
            elif rel:
                offset={'昨天':-1,'今天':0,'前天':-2,'明天':1,'yesterday':-1,'today':0,'tomorrow':1}[rel.group().lower()]
                date=now.astimezone(ZoneInfo(zone)).date()+timedelta(days=offset)
                y,mo,day=date.year,date.month,date.day; original=rel.group()
            else:
                if re.search(r'\d+年|\d+月|\d+日',text): return QueryTime('invalid',note='日期未能完整解析，请提供年、月、日和时刻。')
                return QueryTime('no_time')
            datetime(y,mo,day) # validate date even when hour absent
            tail=text[m.end():] if (cn or simple or eng or eng2) else text[rel.end():]
            clock=re.search(r'(凌晨|早上|上午|中午|下午|晚上)?\s*([零一二三四五六七八九十两\d]{1,3})[点时](?:([零一二三四五六七八九十\d]{1,3})分?|半)?',tail)
            enclock=re.search(r'(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|(?:at\s+)?(\d{1,2}):(\d{2})',tail,re.I)
            if clock:
                period,h,mi=clock.groups(); h=number(h); minute=30 if '半' in clock.group() else number(mi) if mi else 0
                if period in ('下午','晚上') and h<12: h+=12
                if period=='中午' and h<11: h+=12
                if period in ('凌晨','早上','上午') and h==12: h=0
            elif enclock:
                h,mi,period,h24,mi24=enclock.groups(); h=int(h or h24); minute=int(mi or mi24 or 0)
                if period:
                    if not 1<=h<=12: raise ValueError()
                    h=h%12+(12 if period.lower()=='pm' else 0)
            else: return QueryTime('clarify',original=original,note='已识别日期。你想查当天哪个时刻？请补充小时和分钟。')
            dt=datetime(y,mo,day,h,minute)
        tz=ZoneInfo(zone)
        possibilities=[]
        for fold in (0,1):
            aware=dt.replace(tzinfo=tz,fold=fold); utc=aware.astimezone(timezone.utc)
            if utc.astimezone(tz).replace(tzinfo=None)==dt and utc not in possibilities: possibilities.append(utc)
        if len(possibilities)!=1: return QueryTime('clarify',original=original,note='该当地时间处于夏令时切换的重复或不存在时段，请提供 UTC 或明确时区偏移。')
        return QueryTime('parsed',possibilities[0].isoformat().replace('+00:00','Z'),zone,original,note+f'未指定其他时区时按 {zone} 解释。')
    except (ValueError,KeyError,OverflowError):
        return QueryTime('invalid',note='日期或时刻无效，请检查月份、日期及小时。')
