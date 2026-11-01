"""Generate matching editable SVG and README PNG; Pillow only."""
from pathlib import Path
from html import escape
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'assets'
OUT.mkdir(exist_ok=True)
W,H=1440,1180
im=Image.new('RGB',(W,H),'#f8fafc')
d=ImageDraw.Draw(im)
svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">',
     '<title id="title">FloodPred Agent architecture</title>',
     '<desc id="desc">One message is split into chat, conversation memory and project evidence needs. Project tools and hybrid retrieval feed an evidence gate with bounded follow-up. Verified project facts are combined with separately labelled chat and memory responses.</desc>',
     '<rect width="1440" height="1180" fill="#f8fafc"/>']

def font(size,bold=False):
    return ImageFont.truetype('C:/Windows/Fonts/'+('arialbd.ttf' if bold else 'arial.ttf'),size)

def text(x,y,s,size=22,color='#172b4d',bold=False,anchor='mm'):
    d.text((x,y),s,font=font(size,bold),fill=color,anchor=anchor)
    svg.append(f'<text x="{x}" y="{y}" fill="{color}" font-family="Arial, sans-serif" font-size="{size}" font-weight="{700 if bold else 400}" text-anchor="{"middle" if anchor=="mm" else "start"}" dominant-baseline="central">{escape(s)}</text>')

def rect(x,y,w,h,fill,stroke='#dbe3ed',radius=18):
    d.rounded_rectangle((x,y,x+w,y+h),radius,fill=fill,outline=stroke,width=2)
    svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')

def box(x,y,w,h,title,lines=(),fill='#ffffff',accent='#2563eb'):
    rect(x,y,w,h,fill)
    text(x+w/2,y+30,title,23,accent,True)
    for i,line in enumerate(lines):text(x+w/2,y+64+i*27,line,19)

def arrow(points,color='#71829c',dash=False):
    d.line(points,fill=color,width=3)
    import math
    x,y=points[-1]; px,py=points[-2]; a=math.atan2(y-py,x-px)
    tri=[(x,y),(x-12*math.cos(a-.45),y-12*math.sin(a-.45)),(x-12*math.cos(a+.45),y-12*math.sin(a+.45))]
    d.polygon(tri,fill=color)
    coords=' '.join(f'{x},{y}' for x,y in points)
    svg.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round"'+(' stroke-dasharray="7 5"' if dash else '')+'/>')
    svg.append('<polygon points="'+' '.join(f'{x},{y}' for x,y in tri)+f'" fill="{color}"/>')

text(60,52,'FloodPred Agent',36,bold=True,anchor='lm')
text(60,91,'Conversation-first. Evidence-grounded. Bounded by design.',22,'#52647e',anchor='lm')
box(480,128,480,74,'User message',(),accent='#172b4d')
arrow([(720,202),(720,230)])
box(340,230,760,108,'Validated demand plan',
    ['Original spans + history references + dependencies', 'Rules + DeepSeek  |  Thinking only when needed'])
rect(730,385,670,570,'#eef5ff','#d9e6fa',24)
for center in (210,535,1065):
    arrow([(720,338),(720,364),(center,364),(center,415)])
box(60,415,300,128,'Casual conversation',['Natural, contextual replies','No invented project facts'],fill='#ffffff',accent='#6450a4')
box(385,415,300,128,'Conversation memory',['User statements + past replies','Message / replay timestamps'],fill='#ffffff',accent='#087f80')
box(760,415,610,106,'Project evidence',['Validated question graph + read-only routing'],accent='#2563eb')
for center in (905,1225):arrow([(1065,521),(1065,542),(center,542),(center,562)])
box(760,562,290,108,'Read-only tools',['Forecast / observations','Versioned evaluation metrics'])
box(1080,562,290,108,'Hybrid RAG',['BM25 + fixed LSA + RRF','Documents + allowed web'])
for center in (905,1225):arrow([(center,670),(center,690),(1065,690),(1065,714)])
box(825,714,480,108,'Evidence gate',['Claim support, citations, units','Time, version and source hashes'],fill='#ffffff',accent='#087f80')
arrow([(1065,822),(1065,835),(905,835),(905,856)])
arrow([(1065,822),(1065,835),(1230,835),(1230,856)],'#b77b23')
box(760,856,290,80,'Checked conclusions',['Complete or verified subset'],accent='#087f80')
box(1080,856,290,80,'Targeted follow-up',['Gaps only; at most 3 rounds'],accent='#a46914')
arrow([(1370,896),(1385,896),(1385,548),(1240,548),(1240,562)],'#b77b23',True)
text(1220,974,'Budget exhausted?',18,'#7d5a22')
text(1220,999,'Keep supported facts; mark gaps.',18,'#7d5a22')
arrow([(210,543),(210,1005),(600,1005),(600,1035)],'#8b7bab')
arrow([(535,543),(535,985),(720,985),(720,1035)],'#419b9b')
arrow([(905,936),(905,1005),(840,1005),(840,1035)],'#419b9b')
box(420,1035,600,92,'Combined reply + inspectable sources',
    ['Chat and memory stay separate from verified project facts'],fill='#e8f3f0',accent='#0a7468')
text(720,1153,'Historical replay only  /  Not a live forecast or an official flood alert',19,'#52647e')
svg.append('</svg>')
(OUT/'how-it-works.svg').write_text('\n'.join(svg),encoding='utf-8')
im.save(OUT/'how-it-works.png')
print('Generated assets/how-it-works.svg and assets/how-it-works.png')
