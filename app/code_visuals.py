from __future__ import annotations

"""Trusted local editorial renderer for V30.

The LLM may describe *what* a visual should explain through a tiny JSON plan, but
this module owns every pixel.  It never executes model-authored Python and never
calls an image-generation API.  The output is a deterministic 1600x900 PNG data
URI that can travel through the existing preview/DOCX/PDF image pipeline.
"""

import base64
import io
import math
import os
import random
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1600, 900

_FONT_ROOTS = [
    Path('/usr/share/fonts/opentype/noto'), Path('/usr/share/fonts/truetype'), Path('/usr/share/fonts'),
    Path('C:/Windows/Fonts'), Path('/System/Library/Fonts'), Path('/Library/Fonts'),
]
_FONT_NAMES = {
    'regular': ('NotoSansCJK-Regular.ttc', 'NotoSansSC-Regular.otf', 'msyh.ttc', 'simhei.ttf', 'PingFang.ttc', 'Arial Unicode.ttf'),
    'bold': ('NotoSansCJK-Bold.ttc', 'NotoSansSC-Bold.otf', 'msyhbd.ttc', 'simhei.ttf', 'PingFang.ttc'),
    'serif': ('NotoSerifCJK-Regular.ttc', 'NotoSerifSC-Regular.otf', 'simsun.ttc', 'simsun.ttf', 'uming.ttc', 'Songti.ttc'),
}

_THEME_PALETTES = {
    'governance': ['#315DE8', '#11A99A', '#7659E8', '#F1A13E', '#DD526B'],
    'ai': ['#6457E8', '#2385DF', '#20BFA9', '#F3A33F', '#DA5D84'],
    'security': ['#173B7A', '#D95662', '#F09B3D', '#65758B', '#2A9D8F'],
    'public': ['#157AA6', '#20A897', '#F0B84A', '#6D63D8', '#D95C75'],
    'asset': ['#206B57', '#B28A3D', '#4F79C8', '#D36A5B', '#7960C9'],
    'research': ['#1E6F80', '#6856D8', '#E38C3D', '#2A9D8F', '#D65372'],
    'industry': ['#2D63B8', '#F08B3E', '#3D8C84', '#7A63D7', '#D85A6D'],
}

_ANALYTIC_TERMS = {
    'why': ('为什么', '原因', '失败', '问题', '风险', '影响', '导致', '制约', '瓶颈'),
    'flow': ('流程', '路径', '链路', '步骤', '形成', '转化', '生命周期', '进入', '回流'),
    'compare': ('对比', '相比', '传统', '新模式', '前后', '区别', '变化', '过去', '现在'),
    'layered': ('体系', '分层', '架构', '底座', '能力层', '层级', '框架'),
    'network': ('协同', '生态', '主体', '参与方', '网络', '空间', '流通', '共享', '交换'),
    'timeline': ('时间', '历程', '演进', '阶段', '发布', '推进', '落地', '时间轴'),
    'matrix': ('优先级', '象限', '价值', '风险', '高价值', '高风险', '可行性'),
}


def build_cover_data_uri(title: str, brief: str = '', query: str = '') -> str:
    title = ' '.join(str(title or '数据专题').split())[:84]
    brief = ' '.join(str(brief or '').split())[:96]
    img = _build_cover(choose_cover_style(title, brief, query), title, brief or query)
    return _to_data_uri(img)


def choose_cover_style(title: str, brief: str = '', query: str = '') -> str:
    text = f'{title} {brief} {query}'
    compact = re.sub(r'\s+', '', text)
    if any(k in compact for k in ('政策', '指引', '意见', '方案', '条例', '制度', '登记', '办法')):
        return 'policy'
    if re.search(r'\d+(?:\.\d+)?', text) and any(k in compact for k in ('个', '项', '条', '倍', '个月', '年', '%')):
        return 'number'
    if any(k in compact for k in ('可信数据空间', '数据流通', '数据交易', '协同', '生态', '网络', '平台', '产业链')):
        return 'network'
    if any(k in compact for k in ('工厂', '制造', '仓库', '仓储', '物流', '医院', '医疗', '交通', '城市', '园区', '车间', '生产线', '业务现场')):
        return 'scene'
    if any(k in compact for k in ('治理', '空间')):
        return 'network'
    if any(k in compact for k in ('研发', '技术', '创新', '实验', '模型', '算法', 'AI', '人工智能')):
        return 'geometric'
    # Stable per article, diverse across unrelated topics.
    return ['network', 'editorial', 'geometric', 'scene'][sum(ord(c) for c in text) % 4]


def visual_fit_score(slot: dict[str, Any], query: str) -> int:
    intent = str(slot.get('visualIntent') or 'auto').strip().lower()
    if intent == 'diagram':
        return 100
    if intent == 'real':
        return 5
    text = ' '.join(str(slot.get(k) or '') for k in ('afterHeading', 'anchorText', 'purpose', 'query'))
    kind = suggest_body_visual_kind(slot, query)
    score = 18
    if kind in {'flow', 'causal', 'compare', 'layered', 'network', 'timeline', 'kpi', 'matrix'}:
        score += 42
    if len(re.findall(r'\d+(?:\.\d+)?%?', text)) >= 2:
        score += 18
    if any(k in text for k in ('流程', '机制', '路径', '链路', '因果', '关系', '对比', '前后', '分层', '架构', '阶段', '演进', '指标', '风险', '价值')):
        score += 18
    if slot.get('visualPlan'):
        score += 12
    if slot.get('sourceExplicit') or slot.get('sourceId'):
        score -= 28
    if any(k in text for k in ('发布会', '现场', '签约', '大赛', '活动', '会议', '项目现场', '工厂', '实验室', '园区')):
        score -= 18
    return max(0, min(100, score))


def suggest_body_visual_kind(slot: dict[str, Any], query: str) -> str:
    explicit = str(slot.get('visualType') or '').strip().lower()
    allowed = {'flow', 'causal', 'compare', 'layered', 'network', 'timeline', 'kpi', 'matrix', 'concept'}
    if explicit in allowed:
        return explicit
    text = ' '.join(str(slot.get(k) or '') for k in ('afterHeading', 'anchorText', 'purpose', 'query'))
    digits = re.findall(r'\d+(?:\.\d+)?%?', text)
    if len(digits) >= 2 and any(k in text for k in ('个', '项', '条', '倍', '%', '个月', '年', '场景', '赛道', '增长', '下降', '提升')):
        return 'kpi'
    if any(k in text for k in _ANALYTIC_TERMS['matrix']): return 'matrix'
    if any(k in text for k in _ANALYTIC_TERMS['compare']): return 'compare'
    if any(k in text for k in _ANALYTIC_TERMS['why']): return 'causal'
    if any(k in text for k in _ANALYTIC_TERMS['timeline']): return 'timeline'
    if any(k in text for k in _ANALYTIC_TERMS['network']): return 'network'
    if any(k in text for k in _ANALYTIC_TERMS['layered']): return 'layered'
    if any(k in text for k in _ANALYTIC_TERMS['flow']): return 'flow'
    if any(k in text for k in ('分析', '机制', '逻辑', '关系', '框架', '价值')): return 'flow'
    return 'concept'


def should_generate_visual(slot: dict[str, Any], query: str, *, strict: bool = False) -> bool:
    return visual_fit_score(slot, query) >= (18 if strict else 58)


def build_body_visual(slot: dict[str, Any], query: str) -> dict[str, Any]:
    kind = suggest_body_visual_kind(slot, query)
    # KPI cards are only appropriate when the paragraph/plan contains real numeric
    # evidence.  Never invent display numbers merely to fill a visual template.
    if kind == 'kpi' and not _metric_pairs(slot):
        kind = 'concept'
    img = _build_body(kind, slot, query)
    plan = _plan(slot)
    heading = str(slot.get('afterHeading') or '正文')
    purpose = str(slot.get('purpose') or '').strip() or '系统根据该段内容绘制示意图'
    return {
        'url': _to_data_uri(img), 'description': str(plan.get('title') or purpose)[:120],
        'source': '系统根据本文内容绘制', 'sourceUrl': '', 'sourceTitle': heading,
        'sourceSnippet': str(slot.get('anchorText') or '')[:180], 'provider': 'generated-diagram',
        'width': W, 'height': H, 'matchScore': round(88.0 + min(10.0, visual_fit_score(slot, query) / 12), 1),
        'generatedKind': kind, 'visualFitScore': visual_fit_score(slot, query),
    }


@lru_cache(maxsize=3)
def _discover_font(kind: str) -> Path | None:
    env_key = {'regular': 'DEG_VISUAL_FONT', 'bold': 'DEG_VISUAL_FONT_BOLD', 'serif': 'DEG_VISUAL_FONT_SERIF'}[kind]
    override = str(os.getenv(env_key) or '').strip()
    if override:
        p = Path(override).expanduser()
        if p.exists() and p.is_file(): return p
    for root in _FONT_ROOTS:
        if not root.exists(): continue
        for name in _FONT_NAMES[kind]:
            direct = root / name
            if direct.exists(): return direct
        try:
            wanted = set(_FONT_NAMES[kind])
            for path in root.rglob('*'):
                if path.is_file() and path.name in wanted: return path
        except (OSError, PermissionError):
            continue
    return None


@lru_cache(maxsize=96)
def _font(size: int, *, bold: bool = False, serif: bool = False):
    kind = 'serif' if serif else ('bold' if bold else 'regular')
    path = _discover_font(kind)
    if path is not None:
        for kwargs in ({'index': 2}, {}):
            try: return ImageFont.truetype(str(path), size=size, **kwargs)
            except Exception: pass
    return ImageFont.load_default()


def _to_data_uri(img: Image.Image) -> str:
    out = io.BytesIO(); img.convert('RGB').save(out, format='PNG', optimize=True)
    return 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode('ascii')


def _hex(h: str) -> tuple[int, int, int]:
    h = h.lstrip('#'); return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _mix(a, b, t): return tuple(int(a[i]*(1-t)+b[i]*t) for i in range(3))


def _gradient(c1: str, c2: str, *, horizontal=False) -> Image.Image:
    img = Image.new('RGB', (W,H), _hex(c1)); d = ImageDraw.Draw(img); steps = W if horizontal else H
    for i in range(steps):
        c = _mix(_hex(c1), _hex(c2), i/max(1,steps-1))
        d.line((i,0,i,H), fill=c) if horizontal else d.line((0,i,W,i), fill=c)
    return img.convert('RGBA')


def _glow(img, center, radius, color, alpha=.28):
    layer=Image.new('RGBA',img.size,(0,0,0,0)); d=ImageDraw.Draw(layer)
    for r in range(radius,0,-18):
        a=int(255*alpha*(r/radius)*.18); d.ellipse((center[0]-r,center[1]-r,center[0]+r,center[1]+r),fill=(*_hex(color),a))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(28)))


def _shadow_card(img, box, radius=24, fill=(255,255,255,250), shadow=(22,40,72,35)):
    x0,y0,x1,y1=box; layer=Image.new('RGBA',img.size,(0,0,0,0)); d=ImageDraw.Draw(layer)
    d.rounded_rectangle((x0,y0+10,x1,y1+10),radius=radius,fill=shadow); img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(18)))
    ImageDraw.Draw(img).rounded_rectangle(box,radius=radius,fill=fill)


def _wrap(draw, text, f, width):
    lines=[]
    for para in str(text or '').split('\n'):
        cur=''
        for ch in para:
            if cur and draw.textlength(cur+ch,font=f)>width: lines.append(cur); cur=ch
            else: cur+=ch
        if cur: lines.append(cur)
    return lines or ['']


def _fit_text(draw, text, box, max_size, min_size=26, *, fill=(20,30,50), bold=True, center=False):
    x0,y0,x1,y1=box; w=x1-x0; h=y1-y0
    for size in range(max_size,min_size-1,-2):
        f=_font(size,bold=bold); lines=_wrap(draw,text,f,w); lh=int(size*1.18)
        if len(lines)*lh<=h:
            yy=y0
            for line in lines:
                xx=x0 + ((w-draw.textlength(line,font=f))/2 if center else 0)
                draw.text((xx,yy),line,font=f,fill=fill); yy+=lh
            return
    draw.text((x0,y0),_clean_label(text,28),font=_font(min_size,bold=bold),fill=fill)


def _label(draw, xy, text, fill, *, dark=False):
    x,y=xy; f=_font(21,bold=True); tw=draw.textlength(text,font=f)
    draw.rounded_rectangle((x,y,x+tw+34,y+40),radius=14,fill=_hex(fill)); draw.text((x+17,y+8),text,font=f,fill=_hex('#162039') if dark else (255,255,255))


def _arrow(draw,p1,p2,color,width=5):
    draw.line([p1,p2],fill=_hex(color),width=width); ang=math.atan2(p2[1]-p1[1],p2[0]-p1[0]); head=14
    draw.polygon([p2,(p2[0]-head*math.cos(ang-.45),p2[1]-head*math.sin(ang-.45)),(p2[0]-head*math.cos(ang+.45),p2[1]-head*math.sin(ang+.45))],fill=_hex(color))


def _footer(draw, color=(100,120,150)):
    draw.text((80,H-58),'数治攻关 · 数据要素治理',font=_font(21),fill=color)


def _body_base(title: str, tag: str, subtitle: str=''):
    img=Image.new('RGBA',(W,H),_hex('#F7F9FC')+(255,)); d=ImageDraw.Draw(img)
    _label(d,(90,48),tag,'#173B7A'); _fit_text(d,title,(90,105,1510,172),46,28,fill=_hex('#172238'),bold=True)
    if subtitle: d.text((90,184),subtitle,font=_font(22),fill=_hex('#6B7890'))
    d.line((90,224,1510,224),fill=_hex('#D8E2F0'),width=2); return img,d


def _palette(slot):
    text=' '.join(str(slot.get(k) or '') for k in ('afterHeading','anchorText','purpose','query'))
    if any(k in text for k in ('安全','合规','隐私','个人信息')): return _THEME_PALETTES['security']
    if any(k in text for k in ('人工智能','大模型','AI','算法','模型','语料','训练数据')): return _THEME_PALETTES['ai']
    if any(k in text for k in ('公共数据','政务','政府','城市')): return _THEME_PALETTES['public']
    if any(k in text for k in ('资产','入表','融资','估值','交易')): return _THEME_PALETTES['asset']
    if any(k in text for k in ('研发','科研','实验','技术突破','技术创新')): return _THEME_PALETTES['research']
    if any(k in text for k in ('工厂','制造','仓储','物流','供应链','产业')): return _THEME_PALETTES['industry']
    return _THEME_PALETTES['governance']


def _plan(slot): return slot.get('visualPlan') if isinstance(slot.get('visualPlan'),dict) else {}


def _clean_label(value: Any, limit: int=14) -> str:
    text=re.sub(r'\[[0-9,， ]+\]','',str(value or '')); text=re.sub(r'[\r\n\t]+',' ',text); text=re.sub(r'\s+',' ',text).strip(' ，。；：、-—|/')
    return text if len(text)<=limit else text[:max(2,limit-1)].rstrip('，。；：、 ')+'…'


def _plan_list(slot,*keys,max_items=6,label_limit=14):
    plan=_plan(slot)
    for key in keys:
        raw=plan.get(key)
        if isinstance(raw,list):
            out=[]
            for item in raw[:max_items]:
                if isinstance(item,dict): item=item.get('label') or item.get('title') or item.get('name') or item.get('text')
                lab=_clean_label(item,label_limit)
                if lab and lab not in out: out.append(lab)
            if out: return out
    return []


def _content_clauses(slot,max_items=6,label_limit=14):
    text=' '.join(str(slot.get(k) or '') for k in ('anchorText','purpose','afterHeading'))
    clauses=[_clean_label(x,label_limit) for x in re.split(r'[，。；：！？、]|(?:并且|以及|同时|进而|从而)',text) if len(re.sub(r'\s+','',x))>=4]
    out=[]
    for x in clauses:
        if x and x not in out: out.append(x)
    return out[:max_items]


def _title(slot,fallback): return str(_plan(slot).get('title') or slot.get('afterHeading') or fallback)


def _flow_nodes(slot):
    rows=_plan_list(slot,'nodes','steps','items',max_items=6,label_limit=12)
    if len(rows)>=3: return rows
    text=' '.join(str(slot.get(k) or '') for k in ('anchorText','purpose','query'))
    if '资产' in text: return ['原始数据','质量治理','权属确认','资产登记','数据产品','经营应用']
    if any(k in text for k in ('研发','科研','实验')): return ['历史数据','标准整理','模型分析','候选筛选','小步验证','结果回流']
    if '公共数据' in text: return ['公共数据','目录开放','授权使用','场景开发','服务应用','效果反馈']
    if any(k in text for k in ('AI','模型','训练数据','语料')): return ['数据寻源','清洗去重','授权合规','质量评测','模型训练','效果回流']
    clauses=_content_clauses(slot,max_items=6,label_limit=12)
    return clauses if len(clauses)>=3 else ['业务数据','清洗标准','权属治理','数据产品','业务应用']


def _causal_nodes(slot):
    rows=_plan_list(slot,'nodes','causes','items',max_items=4,label_limit=12)
    if len(rows)>=3: return rows
    clauses=_content_clauses(slot,max_items=4,label_limit=12)
    if len(clauses)>=3: return clauses
    text=' '.join(str(slot.get(k) or '') for k in ('anchorText','purpose','query'))
    if '治理' in text: return ['口径不一','责任不清','数据孤岛','质量失控']
    return ['信息不完整','边界不清晰','过程难追踪','反馈不及时']


def _metric_pairs(slot):
    plan=_plan(slot); out=[]
    raw=plan.get('metrics') or plan.get('numbers')
    if isinstance(raw,list):
        for item in raw[:4]:
            if isinstance(item,dict):
                value=_clean_label(item.get('value') or item.get('number'),9); label=_clean_label(item.get('label') or item.get('name'),13)
                if value: out.append((value,label or '关键指标'))
    if out: return out
    text=' '.join(str(slot.get(k) or '') for k in ('anchorText','purpose','query'))
    pat=re.compile(r'(?P<num>\d+(?:\.\d+)?(?:%|倍|个月|年|条|项|个|家|亿元|万元)?)')
    for m in pat.finditer(text):
        value=m.group('num'); start=max(0,m.start()-12); end=min(len(text),m.end()+15); context=_clean_label(text[start:end].replace(value,''),14)
        if value not in [x[0] for x in out]: out.append((value,context or '关键数据'))
        if len(out)>=4: break
    return out


def _number_focus(title: str, brief: str = '') -> tuple[str, str]:
    text = f'{title} {brief}'
    # Keep number and unit as one semantic token so “3年 -> 8个月” can never
    # become the misleading “3个月”. Prefer the target/result side of change
    # expressions, otherwise use the first complete pair.
    pair_re = re.compile(r'(\d+(?:\.\d+)?)\s*(亿元|万元|万|亿|个月|月|年|条|项|个|家|倍|%|％)')
    pairs = [(m.group(1), m.group(2), m.start()) for m in pair_re.finditer(text)]
    if not pairs:
        raw = re.search(r'\d+(?:\.\d+)?', text)
        return (raw.group(0), '') if raw else ('—', '')
    target_markers = ('缩短到', '降至', '降到', '提升至', '提升到', '增长至', '增长到', '达到', '增至', '变成', '变为')
    marker_pos = max((text.find(k) for k in target_markers if text.find(k) >= 0), default=-1)
    if marker_pos >= 0:
        after = [p for p in pairs if p[2] > marker_pos]
        if after:
            return after[0][0], after[0][1]
    # A “从 A 到 B” title usually wants the outcome B as the visual focal point.
    if ('从' in text and ('到' in text or '至' in text)) and len(pairs) >= 2:
        return pairs[-1][0], pairs[-1][1]
    return pairs[0][0], pairs[0][1]


def _policy_rows(title: str, brief: str = '') -> tuple[str, str, str, str]:
    text = f'{title} {brief}'
    if any(k in text for k in ('产权', '登记', '确权')):
        return ('权利边界', '来源证明', '登记流程', '流通应用')
    if any(k in text for k in ('安全', '合规', '隐私', '个人信息')):
        return ('风险识别', '合规边界', '责任机制', '审计留痕')
    if '公共数据' in text:
        return ('开放目录', '授权运营', '场景开发', '收益机制')
    if any(k in text for k in ('AI', '人工智能', '模型', '训练数据', '语料')):
        return ('数据供给', '训练合规', '质量评测', '安全边界')
    if any(k in text for k in ('资产', '入表', '融资')):
        return ('确认范围', '计量基础', '入表路径', '经营使用')
    return ('政策目标', '适用对象', '实施路径', '业务影响')


def _scene_variant(title: str, brief: str = '') -> str:
    text = f'{title} {brief}'
    if any(k in text for k in ('医院', '医疗', '健康')): return 'health'
    if any(k in text for k in ('城市', '交通', '政务')): return 'city'
    if any(k in text for k in ('仓库', '仓储', '物流')): return 'warehouse'
    return 'factory'


def _build_cover(style,title,brief):
    return {'policy':_cover_policy,'number':_cover_number,'geometric':_cover_geometric,'editorial':_cover_editorial,'scene':_cover_scene}.get(style,_cover_network)(title,brief)


def _cover_network(title,brief):
    img=_gradient('#071B3C','#0B58C7',horizontal=True); _glow(img,(1220,220),420,'#10D9C4'); _glow(img,(980,690),500,'#7D5CFF',.18); d=ImageDraw.Draw(img)
    for x in range(80,W,80): d.line((x,0,x,H),fill=(255,255,255,14),width=1)
    for y in range(80,H,80): d.line((0,y,W,y),fill=(255,255,255,12),width=1)
    nodes=[(1060,160),(1280,250),(1400,430),(1230,520),(1000,450),(1140,660),(1420,700)]
    for i,p in enumerate(nodes):
        for q in nodes[i+1:]:
            if math.dist(p,q)<320: d.line((*p,*q),fill=(125,230,255,96),width=3)
    for x,y in nodes: d.ellipse((x-14,y-14,x+14,y+14),fill=(235,253,255,240),outline=(78,233,255,255),width=4)
    _label(d,(88,82),'DATA GOVERNANCE','#0DD3B3',dark=True); _fit_text(d,title,(90,190,900,500),82,48,fill=(246,250,255))
    if brief: _fit_text(d,brief[:58],(94,570,850,630),30,23,fill=(200,224,255),bold=True)
    d.line((95,650,610,650),fill=_hex('#26E1C3'),width=8); d.text((95,688),'权属 · 质量 · 安全 · 产品 · 应用',font=_font(26,bold=True),fill=(228,239,255)); _footer(d,(170,210,244)); return img


def _cover_policy(title,brief):
    img=_gradient('#F5F0E7','#E9F0F5',horizontal=True); _shadow_card(img,(115,90,925,810),24,(255,255,255,245),(24,42,71,45)); d=ImageDraw.Draw(img)
    d.rectangle((115,90,925,108),fill=_hex('#8F1D2C')); d.text((170,150),'POLICY / 研究与制度',font=_font(24,bold=True),fill=_hex('#8F1D2C')); _fit_text(d,title,(170,215,830,360),62,36,fill=_hex('#1B2538'))
    d.line((170,388,780,388),fill=_hex('#C7A56A'),width=4); _fit_text(d,brief or '制度如何真正进入企业经营',(170,430,820,490),27,21,fill=_hex('#465268'))
    for i,row in enumerate(_policy_rows(title, brief)):
        y=520+i*64; d.text((170,y),f'0{i+1}',font=_font(22,bold=True),fill=_hex('#A47B39')); d.text((240,y-4),row,font=_font(30,bold=True),fill=_hex('#2F3A4D'))
    _label(d,(1030,122),'制度不是终点','#8F1D2C'); _fit_text(d,title,(1025,220,1490,500),52,34,fill=_hex('#152039')); d.text((1030,565),'确权 → 流通 → 融资 → 应用',font=_font(30,bold=True),fill=_hex('#6B5C44')); _footer(d,(100,94,84)); return img


def _cover_geometric(title,brief):
    img=_gradient('#0E1325','#1A1B46',horizontal=True); d=ImageDraw.Draw(img)
    for x,y,r,c,a in ((1150,220,250,'#57E0C2',70),(1360,520,320,'#6B5CFF',88),(1050,690,230,'#F6AF45',55),(860,360,170,'#3E93FF',58)): d.ellipse((x-r,y-r,x+r,y+r),fill=(*_hex(c),a))
    d.arc((930,180,1490,740),30,330,fill=_hex('#EAFBFF'),width=4); _label(d,(90,110),'CONCEPT / DATA','#F6AF45',dark=True); _fit_text(d,title,(90,220,850,500),72,44,fill=(247,250,255))
    if brief: _fit_text(d,brief[:50],(95,555,790,615),30,23,fill=(188,201,238),bold=False)
    d.line((95,625,520,625),fill=_hex('#57E0C2'),width=8); _footer(d,(156,171,210)); return img


def _cover_editorial(title,brief):
    img=_gradient('#FBF8F2','#F0F7FB',horizontal=True); d=ImageDraw.Draw(img); colors=['#F05C5C','#F4B740','#6E57E0','#1BB9A4']; rnd=random.Random(sum(ord(c) for c in title)%100000)
    for i in range(34):
        x=rnd.randint(90,600); y=rnd.randint(160,710); r=rnd.randint(10,26); d.ellipse((x-r,y-r,x+r,y+r),fill=(*_hex(colors[i%4]),180))
    d.polygon([(600,220),(860,330),(860,560),(600,690)],fill=_hex('#1C2A44'))
    for i,y in enumerate((230,365,500,635)):
        c=colors[(i+1)%4]; _shadow_card(img,(940,y,1410,y+95),20); d=ImageDraw.Draw(img); d.rounded_rectangle((965,y+20,1025,y+75),radius=14,fill=_hex(c)); d.text((1060,y+22),('标准化','可追踪','可复用','可经营')[i],font=_font(30,bold=True),fill=_hex('#1F2C43')); d.line((1235,y+50,1370,y+50),fill=_hex(c),width=7)
    _label(d,(85,85),'EDITORIAL VISUAL','#6E57E0'); _fit_text(d,title,(85,740,1450,860),48,30,fill=_hex('#172238')); return img


def _cover_number(title,brief):
    img=Image.new('RGBA',(W,H),_hex('#F8FAFC')+(255,)); d=ImageDraw.Draw(img); d.rectangle((0,0,440,H),fill=_hex('#173B7A')); d.rectangle((440,0,465,H),fill=_hex('#FFB23F'))
    big, unit = _number_focus(title, brief)
    d.text((78,100),'DATA × INSIGHT',font=_font(28,bold=True),fill=(214,231,255)); d.text((78,220),big[:4],font=_font(210,bold=True),fill=(255,255,255)); d.text((90,500),unit,font=_font(58,bold=True),fill=(255,255,255)); _label(d,(560,100),'数字焦点','#173B7A')
    _fit_text(d,title,(560,200,1480,470),80,46,fill=_hex('#172238')); _fit_text(d,brief or '关键数字值得被真正看见',(565,540,1450,610),32,23,fill=_hex('#256F6B'))
    pts=[(600,700),(760,650),(930,620),(1100,500),(1270,450),(1450,360)]; d.line(pts,fill=_hex('#FF8F3D'),width=9)
    for p in pts: d.ellipse((p[0]-10,p[1]-10,p[0]+10,p[1]+10),fill=_hex('#173B7A'))
    _footer(d); return img


def _cover_scene(title,brief):
    variant=_scene_variant(title,brief); img=Image.new('RGBA',(W,H),_hex('#F2F6F3')+(255,)); d=ImageDraw.Draw(img)
    # Left side is deliberately illustrative rather than pretending to be a
    # documentary photograph.  It changes by concrete scene so a hospital story
    # does not receive a warehouse silhouette.
    if variant in {'factory','warehouse'}:
        d.rectangle((0,0,920,H),fill=_hex('#E5EEE9')); d.rectangle((70,350,845,700),fill=_hex('#34495B'))
        for x in (130,285,445,610):
            d.rectangle((x,278,x+86,700),fill=_hex('#4F677A')); d.rectangle((x+13,315,x+73,365),fill=_hex('#DCE8EE'))
        d.polygon([(70,350),(250,232),(430,350)],fill=_hex('#283B4D')); d.polygon([(430,350),(615,220),(845,350)],fill=_hex('#283B4D'))
        if variant=='warehouse':
            for x in (145,315,485,655):
                d.rounded_rectangle((x,565,x+110,670),radius=8,fill=_hex('#D19A48')); d.line((x+12,610,x+98,610),fill=_hex('#9C6D31'),width=4)
    elif variant=='city':
        d.rectangle((0,0,920,H),fill=_hex('#E5EEF4')); baseline=700
        for i,(x,w,h) in enumerate(((60,95,300),(175,130,430),(325,95,355),(440,150,500),(610,105,390),(735,120,460))):
            c=('#355B75','#426C86','#2D5068')[i%3]; d.rounded_rectangle((x,baseline-h,x+w,baseline),radius=5,fill=_hex(c))
            for yy in range(baseline-h+35,baseline-20,55):
                for xx in range(x+22,x+w-15,42): d.rectangle((xx,yy,xx+15,yy+22),fill=_hex('#CFE2E9'))
        d.arc((110,170,820,760),200,340,fill=_hex('#2CB9A9'),width=4)
    else:  # health
        d.rectangle((0,0,920,H),fill=_hex('#E8F2F1')); d.rounded_rectangle((150,230,760,700),radius=24,fill=_hex('#FFFFFF'))
        d.rectangle((390,310,520,570),fill=_hex('#2FA79C')); d.rectangle((325,375,585,505),fill=_hex('#2FA79C'))
        for x in (205,620):
            for y in (315,430,545): d.rounded_rectangle((x,y,x+85,y+55),radius=8,fill=_hex('#CFE7E5'))
    d.rectangle((0,700,W,H),fill=_hex('#CBD8DB'))
    for x in range(135,850,125):
        d.line((x,205,x,720),fill=(*_hex('#2CB9A9'),130),width=3)
        for y in range(255,660,118): d.ellipse((x-8,y-8,x+8,y+8),fill=_hex('#FFB54A'))
    _shadow_card(img,(760,82,1500,818),34,(255,255,255,242),(26,53,82,26)); d=ImageDraw.Draw(img); _label(d,(830,135),'DATA IN THE REAL WORLD','#1E57D8'); _fit_text(d,title,(830,225,1450,500),64,40,fill=_hex('#162039'))
    if brief: _fit_text(d,brief[:58],(835,540,1430,640),30,24,fill=_hex('#297A70'))
    d.line((835,690,1375,690),fill=_hex('#D8E3EA'),width=2); d.text((835,720),{'warehouse':'仓储现场 · 库存数据 · 经营结果','city':'城市运行 · 数据联动 · 公共服务','health':'医疗场景 · 数据治理 · 服务质量'}.get(variant,'生产现场 · 数据层 · 经营结果'),font=_font(26,bold=True),fill=_hex('#536178')); _footer(d); return img


def _build_body(kind,slot,query):
    return {'flow':_body_flow,'causal':_body_causal,'compare':_body_compare,'layered':_body_layered,'network':_body_network,'timeline':_body_timeline,'kpi':_body_kpi,'matrix':_body_matrix}.get(kind,_body_concept)(slot)


def _body_flow(slot):
    title=_title(slot,'从数据到价值'); img,d=_body_base(_clean_label(title,30),'PROCESS','连续动作压成一条读者能顺着走完的价值链'); nodes=_flow_nodes(slot)[:6]; colors=_palette(slot); gap=28; bw=max(190,min(280,int((W-180-gap*(len(nodes)-1))/max(1,len(nodes))))); x=90; y=330
    for i,lab in enumerate(nodes):
        c=colors[i%len(colors)]; _shadow_card(img,(x,y,x+bw,y+260),28,(255,255,255,252),(24,46,80,24)); d=ImageDraw.Draw(img); d.rounded_rectangle((x+28,y+28,x+90,y+90),radius=18,fill=_hex(c)); d.text((x+48,y+42),str(i+1),font=_font(24,bold=True),fill=(255,255,255)); _fit_text(d,lab,(x+26,y+112,x+bw-24,y+190),31,21,fill=_hex('#1D2940')); d.text((x+27,y+215),('输入','处理','治理','组织','使用','反馈')[min(i,5)],font=_font(20),fill=_hex('#748197'))
        if i<len(nodes)-1: _arrow(d,(x+bw+5,y+132),(x+bw+gap-6,y+132),'#AAB8CF',4)
        x+=bw+gap
    anchor=_clean_label(slot.get('anchorText') or slot.get('purpose'),62)
    if anchor: _fit_text(d,anchor,(90,700,1510,805),29,22,fill=_hex('#42506B'),bold=False)
    return img


def _body_causal(slot):
    title=_title(slot,'为什么会发生'); img,d=_body_base(_clean_label(title,30),'CAUSE / EFFECT','把“为什么”拆成少量可验证的原因'); colors=_palette(slot); center=(800,500); d.ellipse((682,382,918,618),fill=_hex(colors[0])); core=_clean_label(_plan(slot).get('center') or slot.get('afterHeading') or '核心问题',9); _fit_text(d,core,(715,448,885,552),34,23,fill=(255,255,255),center=True); coords=[(280,330),(280,690),(1320,330),(1320,690)]
    for i,lab in enumerate(_causal_nodes(slot)[:4]):
        x,y=coords[i]; c=colors[(i+1)%len(colors)]; _shadow_card(img,(x-155,y-70,x+155,y+70),24,(255,255,255,252),(30,50,90,23)); d=ImageDraw.Draw(img); d.ellipse((x-122,y-19,x-84,y+19),fill=_hex(c)); _fit_text(d,lab,(x-62,y-31,x+125,y+35),30,21,fill=_hex('#1E2A43')); target=(center[0]+(-115 if x<center[0] else 115),center[1]+(-55 if y<center[1] else 55)); _arrow(d,(x+(155 if x<800 else -155),y),target,c,5)
    return img


def _body_compare(slot):
    plan=_plan(slot); title=_title(slot,'两种方式的差别'); img,d=_body_base(_clean_label(title,30),'COMPARE','两边只保留真正改变判断的差异'); colors=_palette(slot); left=_plan_list(slot,'left','before',max_items=4,label_limit=17); right=_plan_list(slot,'right','after',max_items=4,label_limit=17)
    if not left or not right:
        nodes=_plan_list(slot,'nodes','items',max_items=6,label_limit=17)
        if len(nodes)>=4: half=max(2,len(nodes)//2); left=left or nodes[:half]; right=right or nodes[half:half+half]
    left=left or ['依赖经验判断','大步试错','失败信息难复用']; right=right or ['数据先缩小范围','小步快速验证','结果持续回流']; rows=min(4,max(3,min(len(left),len(right)))); left=(left+['流程难沉淀']*rows)[:rows]; right=(right+['能力持续积累']*rows)[:rows]
    d.rounded_rectangle((90,270,760,340),radius=20,fill=_hex('#EEF2F7')); d.rounded_rectangle((840,270,1510,340),radius=20,fill=_hex('#E8F7F3')); d.text((120,287),_clean_label(plan.get('leftTitle') or '传统方式',12),font=_font(30,bold=True),fill=_hex('#4B5A6E')); d.text((870,287),_clean_label(plan.get('rightTitle') or '数据驱动方式',12),font=_font(30,bold=True),fill=_hex(colors[1]))
    for i in range(rows):
        y=380+i*115; _shadow_card(img,(90,y,760,y+88),22,(255,255,255,252),(20,36,65,18)); _shadow_card(img,(840,y,1510,y+88),22,(255,255,255,252),(20,36,65,18)); d=ImageDraw.Draw(img); d.rounded_rectangle((112,y+25,150,y+63),radius=12,fill=_hex('#D9E0EA')); d.rounded_rectangle((862,y+25,900,y+63),radius=12,fill=_hex(colors[1])); _fit_text(d,left[i],(170,y+16,730,y+72),27,20,fill=_hex('#263247')); _fit_text(d,right[i],(920,y+16,1480,y+72),27,20,fill=_hex('#263247'))
    return img


def _body_layered(slot):
    plan=_plan(slot); title=_title(slot,'数据价值层级'); img,d=_body_base(_clean_label(title,30),'LAYERS','越往上越接近业务价值，越往下越依赖稳定底座'); colors=_palette(slot); labels=_plan_list(slot,'layers','nodes','items',max_items=4,label_limit=14)
    if len(labels)<3:
        text=' '.join(str(slot.get(k) or '') for k in ('anchorText','purpose','query'))
        labels=['经营应用层','数据产品层','资产治理层','数据资源层'] if '资产' in text else (['模型应用层','数据产品层','质量安全层','数据资源层'] if ('AI' in text or '模型' in text) else ['业务应用层','数据产品层','治理能力层','数据资源层'])
    widths=[920,1030,1140,1250]; y=290; subs=_plan_list(slot,'subtitles',max_items=4,label_limit=22)
    for i,name in enumerate(labels[:4]):
        width=widths[i]; x=(W-width)//2; c=colors[i%len(colors)]; d.polygon([(x,y),(x+width,y),(x+width-80,y+105),(x+80,y+105)],fill=_hex(c)); d.text((x+60,y+17),name,font=_font(30,bold=True),fill=(255,255,255));
        if i<len(subs): d.text((x+60,y+60),subs[i],font=_font(20),fill=(245,250,255))
        y+=110
    return img


def _body_network(slot):
    plan=_plan(slot); title=_title(slot,'多方协同'); img,d=_body_base(_clean_label(title,30),'NETWORK','把主体、交换关系和协同中枢放在同一张图里'); colors=_palette(slot); center=(800,510); d.ellipse((675,385,925,635),fill=_hex(colors[0])); center_label=_clean_label(plan.get('center') or ('可信数据空间' if '可信数据空间' in str(slot) else '协同中枢'),10); _fit_text(d,center_label,(710,455,890,565),31,21,fill=(255,255,255),center=True); actors=_plan_list(slot,'nodes','actors','entities','items',max_items=5,label_limit=10) or ['政府/规则方','平台运营方','供数企业','用数企业','服务机构']; coords=[(300,315),(1300,315),(280,690),(1320,690),(800,775)]
    for i,lab in enumerate(actors[:5]):
        x,y=coords[i]; c=colors[(i+1)%len(colors)]; target=(center[0]+(105 if x<center[0] else -105 if x>center[0] else 0),center[1]+(92 if y<center[1] else -92)); _arrow(d,(x,y),target,c,4); _shadow_card(img,(x-125,y-52,x+125,y+52),22,(255,255,255,252),(20,35,60,20)); d=ImageDraw.Draw(img); _fit_text(d,lab,(x-95,y-24,x+98,y+30),27,19,fill=_hex('#233047'))
    relation=_clean_label(plan.get('relation') or '授权 · 规则 · 可信流通',28); d.text((585,655),relation,font=_font(25,bold=True),fill=_hex('#57667F')); return img


def _body_timeline(slot):
    plan=_plan(slot); title=_title(slot,'从提出到落地'); img,d=_body_base(_clean_label(title,30),'TIMELINE','只保留真正改变阶段判断的节点'); colors=_palette(slot); y=520; d.line((150,y,1450,y),fill=_hex('#A7B7CE'),width=6); events=[]; raw=plan.get('events') or plan.get('nodes')
    if isinstance(raw,list):
        for item in raw[:4]:
            if isinstance(item,dict): events.append((_clean_label(item.get('time') or item.get('date'),8),_clean_label(item.get('label') or item.get('title'),12)))
            else: events.append(('',_clean_label(item,12)))
    text=' '.join(str(slot.get(k) or '') for k in ('anchorText','purpose','query'))
    if not events:
        dates=re.findall(r'(20\d{2}(?:[./年-]\d{1,2}(?:[./月-]\d{1,2}日?)?)?)',text); clauses=_content_clauses(slot,max_items=4,label_limit=12)
        for i in range(min(4,max(len(dates),len(clauses)))): events.append((dates[i] if i<len(dates) else f'阶段{i+1}',clauses[i] if i<len(clauses) else f'关键节点{i+1}'))
    if len(events)<3: events=[('阶段1','提出问题'),('阶段2','试点探索'),('阶段3','规则形成'),('阶段4','推进落地')]
    xs=[220,600,980,1360]
    for i,(tm,lab) in enumerate(events[:4]):
        x=xs[i]; c=colors[i%len(colors)]; d.ellipse((x-18,y-18,x+18,y+18),fill=_hex(c),outline=(255,255,255),width=5); box=(x-140,275,x+140,430) if i%2==0 else (x-140,600,x+140,755); yy=294 if i%2==0 else 619; _shadow_card(img,box,24,(255,255,255,252),(20,35,60,20)); d=ImageDraw.Draw(img); d.text((box[0]+24,yy),tm or f'阶段{i+1}',font=_font(25,bold=True),fill=_hex(c)); _fit_text(d,lab,(box[0]+24,yy+44,box[2]-20,yy+110),25,19,fill=_hex('#223048')); _arrow(d,(x,box[3] if i%2==0 else box[1]),(x,y-22 if i%2==0 else y+22),c,4)
    return img


def _body_kpi(slot):
    title=_title(slot,'关键数字'); img,d=_body_base(_clean_label(title,30),'NUMBERS','只突出真正改变读者判断的数字；没有真实数字时不编造'); colors=_palette(slot); pairs=_metric_pairs(slot)
    if not pairs:
        labels=_plan_list(slot,'nodes','items',max_items=4,label_limit=13) or _content_clauses(slot,max_items=4,label_limit=13); pairs=[('',lab) for lab in labels[:4]]
    pairs = pairs[:4]
    gap=35; cw=int((W-180-gap*(len(pairs)-1))/max(1,len(pairs))); x=90
    for i,(value,label) in enumerate(pairs):
        c=colors[i%len(colors)]; _shadow_card(img,(x,315,x+cw,675),30,(255,255,255,252),(20,40,70,22)); d=ImageDraw.Draw(img); d.rounded_rectangle((x+25,340,x+95,410),radius=20,fill=_hex(c)); _fit_text(d,value,(x+28,440,x+cw-25,540),76,38,fill=_hex('#162139')); _fit_text(d,label,(x+30,580,x+cw-24,640),25,18,fill=_hex('#56647B')); x+=cw+gap
    return img


def _body_matrix(slot):
    plan=_plan(slot); title=_title(slot,'治理优先级'); img,d=_body_base(_clean_label(title,30),'MATRIX','适合价值 / 风险、影响 / 可行性等二维判断'); colors=_palette(slot); x0,y0,x1,y1=260,740,1370,290; d.line((x0,y0,x1,y0),fill=_hex('#6D7C92'),width=4); d.line((x0,y0,x0,y1),fill=_hex('#6D7C92'),width=4); _arrow(d,(x1-80,y0),(x1,y0),'#6D7C92',4); _arrow(d,(x0,y1+80),(x0,y1),'#6D7C92',4)
    d.rectangle((x0,515,815,y0),fill=_hex('#E9F0F8')); d.rectangle((815,515,x1,y0),fill=_hex('#E7F7F2')); d.rectangle((x0,y1,815,515),fill=_hex('#FFF1E2')); d.rectangle((815,y1,x1,515),fill=_hex('#FDECEF')); d.text((1180,770),_clean_label(plan.get('xLabel') or '价值 →',12),font=_font(24,bold=True),fill=_hex('#56647A')); d.text((110,300),_clean_label(plan.get('yLabel') or '风险 ↑',12),font=_font(24,bold=True),fill=_hex('#56647A'))
    qlabels=_plan_list(slot,'quadrants',max_items=4,label_limit=15) or ['低价值 / 低风险','高价值 / 低风险','低价值 / 高风险','高价值 / 高风险']; qcoords=[(340,580),(920,580),(340,360),(920,360)]
    for i,(x,y) in enumerate(qcoords): d.text((x,y),qlabels[i],font=_font(27,bold=True),fill=_hex(colors[i%len(colors)]))
    items=_plan_list(slot,'items','nodes',max_items=4,label_limit=12)
    for i,lab in enumerate(items[:4]):
        pts=[(560,660),(1080,660),(600,430),(1110,430)]; x,y=pts[i]; c=colors[i%len(colors)]; d.ellipse((x-12,y-12,x+12,y+12),fill=_hex(c)); d.text((x+22,y-17),lab,font=_font(22,bold=True),fill=_hex('#263247'))
    return img


def _body_concept(slot):
    title=_title(slot,'核心关系'); img,d=_body_base(_clean_label(title,30),'EDITORIAL','当一段话不适合真实照片时，用更克制的编辑视觉解释它'); colors=_palette(slot); nodes=_plan_list(slot,'nodes','items','concepts',max_items=4,label_limit=13) or _content_clauses(slot,max_items=4,label_limit=13)
    if not nodes: nodes=['事实输入','治理组织','可信使用','价值反馈']
    rnd=random.Random(sum(ord(c) for c in title)%100000)
    # left: rich but deterministic abstract field
    for i in range(34):
        x=rnd.randint(120,660); y=rnd.randint(310,760); r=rnd.randint(10,28); c=colors[i%len(colors)]; d.ellipse((x-r,y-r,x+r,y+r),fill=(*_hex(c),150))
    d.polygon([(640,320),(820,420),(820,650),(640,750)],fill=_hex('#17243A'))
    # right: editorial cards
    for i,lab in enumerate(nodes[:4]):
        y=300+i*120; c=colors[(i+1)%len(colors)]; _shadow_card(img,(900,y,1430,y+88),20,(255,255,255,252),(25,40,70,25)); d=ImageDraw.Draw(img); d.rounded_rectangle((925,y+18,982,y+70),radius=14,fill=_hex(c)); _fit_text(d,lab,(1015,y+18,1385,y+68),29,20,fill=_hex('#1F2C43')); d.line((1250,y+45,1390,y+45),fill=_hex(c),width=6)
    return img
