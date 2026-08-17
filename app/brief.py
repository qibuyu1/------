from __future__ import annotations

import json
import re
from typing import Any

from . import deepseek
from .cache import TTLCache

_CACHE = TTLCache(max_items=120, ttl_seconds=30 * 60)


def local_brief(angle: str, query: str) -> dict[str, Any]:
    text = str(angle or "").strip()
    # Split around punctuation but also separate positive instructions after an avoid clause.
    raw_parts = [x.strip() for x in re.split(r"[。；;\n]", text) if x.strip()]
    parts=[]
    for part in raw_parts:
        segments=re.split(r"(?=希望|重点|需要|最好|请|结尾|开头|读者|同时)", part)
        parts.extend([x.strip(" ，,、") for x in segments if x.strip(" ，,、")])
    must=[]; avoid=[]
    for part in parts:
        if any(m in part for m in ("不要", "避免", "别", "不想", "不能")) and not any(m in part for m in ("不要错过",)):
            avoid.append(part[:180])
        elif any(m in part for m in ("重点", "需要", "必须", "希望", "请", "要回答", "最好", "结尾", "开头", "读者")):
            must.append(part[:200])
        elif part:
            must.append(part[:160])
    objective = (parts[0][:240] if parts else f"围绕“{query}”写一篇有明确判断的公众号文章")
    return {
        "objective": objective,
        "mustInclude": must[:8],
        "avoid": avoid[:8],
        "readerNeed": "让普通读者看懂，并形成可复述的核心判断",
        "stance": "事实、解释与判断的边界清楚；判断必须有依据、有边界，不夸大，也不预设固定论证顺序。",
        "structureAdvice": "围绕一个中心判断自然推进；段落和小标题只在论证真正转折时出现，结构由证据之间的关系决定，不预设固定章节。",
        "usedModel": False,
    }



def _needs_model_brief(text: str) -> bool:
    """Reserve the extra brief-planner call for genuinely multi-constraint briefs.

    The main writing model always receives the user's raw angle, so a simple long
    sentence does not need a second model round-trip just to paraphrase it.
    """
    value = str(text or "").strip()
    if len(value) < 36:
        return False
    clauses = [x for x in re.split(r"[。；;\n]", value) if x.strip()]
    markers = ("不要", "避免", "必须", "重点", "希望", "结尾", "开头", "读者", "同时", "但", "优先")
    marker_count = sum(1 for marker in markers if marker in value)
    # A concise but clearly multi-control brief deserves planning; a single long
    # sentence with only one or two preferences does not. This keeps quality where
    # the planner matters without taxing ordinary prompts.
    return marker_count >= 3 or (len(value) >= 56 and len(clauses) >= 2) or (len(value) >= 42 and len(clauses) >= 3)

def understand_writing_brief(angle: str, query: str) -> dict[str, Any]:
    fallback = local_brief(angle, query)
    text = str(angle or "").strip()
    if not text:
        return fallback
    key = json.dumps({"q": query.strip(), "angle": text}, ensure_ascii=False, sort_keys=True)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    # Do not spend an extra model call on a one-line trivial brief; the main article
    # call already understands it. Use the cheap planner only for a materially detailed brief.
    if not deepseek.available() or not _needs_model_brief(text):
        _CACHE.put(key, fallback)
        return fallback
    try:
        result, _meta = deepseek.plan_writing_brief(query, text)
        if isinstance(result, dict):
            out = dict(fallback)
            for k, cap in (("objective",260),("readerNeed",220),("stance",220),("structureAdvice",260)):
                if result.get(k): out[k] = str(result[k]).strip()[:cap]
            for k in ("mustInclude", "avoid"):
                if isinstance(result.get(k), list):
                    out[k] = [str(x).strip()[:180] for x in result[k] if str(x).strip()][:10]
            out["usedModel"] = True
            _CACHE.put(key, out)
            return out
    except Exception:
        pass
    _CACHE.put(key, fallback)
    return fallback
