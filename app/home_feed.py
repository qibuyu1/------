from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from typing import Any

from .pipeline import research

_LOCK = Lock()
_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
_CACHE_SECONDS = 15 * 60


def home_feed(*, force: bool = False) -> dict[str, Any]:
    """Build a current feed with Chinese sources first and global context second."""
    now = monotonic()
    with _LOCK:
        cached = _CACHE.get("data")
        if cached and not force and now - float(_CACHE.get("at") or 0) < _CACHE_SECONDS:
            return cached

    from concurrent.futures import ThreadPoolExecutor, as_completed
    jobs = []
    base_queries = [
        ("国内政策", "数据要素 国家数据局 数据基础制度 公共数据 授权运营", "policy", "domestic-only"),
        ("国内热点", "数据要素 市场化配置 数据流通 数据价值 最新动态", "news", "domestic-only"),
        ("产业实践", "数据资产入表 可信数据空间 数据交易 企业实践 案例", "news", "domestic-only"),
        ("中文研究", "数据要素市场化 数据治理 数据流通 中文论文", "paper", "domestic-only"),
        ("国际补充", "data governance data spaces data markets latest policy practice", "news", "global-only"),
    ]
    warnings=[]; rows=[]
    try:
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="home-feed") as pool:
            futures={pool.submit(research,{"query":q,"types":[kind],"timeRange":"latest","maxResults":8,"searchMode":"fast","surface":"home","regionPreference":region}): name for name,q,kind,region in base_queries}
            for fut in as_completed(futures):
                name=futures[fut]
                try:
                    data=fut.result(); rows.extend([x for x in data.get("results") or [] if x.get("sourceUsable") or x.get("sourceVerified")]); warnings.extend(data.get("warnings") or [])
                except Exception as exc:
                    warnings.append(f"{name}刷新失败：{str(exc)[:140]}")
    except Exception as exc:
        warnings.append(f"首页推荐刷新失败：{str(exc)[:160]}")

    if not rows:
        with _LOCK: stale=_CACHE.get("data")
        if stale and stale.get("items"):
            result=dict(stale); result["stale"]=True; result["warnings"]=warnings; return result
        return {"items":[],"demo":False,"warnings":warnings,"generatedAt":datetime.now(timezone.utc).isoformat(),"stale":False}

    items=_balanced_feed(rows,limit=24)
    result={"items":items,"demo":False,"warnings":warnings,"generatedAt":datetime.now(timezone.utc).isoformat(),"stale":False}
    with _LOCK:
        _CACHE["at"]=now; _CACHE["data"]=result
    return result


def _balanced_feed(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: (int(row.get("score") or 0), int(row.get("queryMatchScore") or 0), int(row.get("freshnessScore") or 0)), reverse=True)
    if not ordered:
        return []
    top_score = int(ordered[0].get("score") or 0)
    threshold = max(55, top_score - 14)
    output: list[dict[str, Any]] = []
    seen = set()
    # Seed diversity only when a candidate is genuinely competitive with the best result.
    for kind in ("news", "paper", "policy"):
        hit = next((row for row in ordered if row.get("type") == kind and int(row.get("score") or 0) >= threshold), None)
        if hit:
            key = str(hit.get("id") or hit.get("url") or hit.get("title"))
            if key and key not in seen:
                output.append(hit); seen.add(key)
    # Homepage ratio: at least three quarters domestic when enough candidates
    # exist. Global items are comparison/context, never the main feed.
    domestic = [row for row in ordered if row.get("originRegion") == "domestic" or ".cn" in str(row.get("url") or "").lower()]
    global_rows = [row for row in ordered if row not in domestic]
    for row in domestic[: max(0, (limit * 3 + 3) // 4)]:
        key = str(row.get("id") or row.get("url") or row.get("title"))
        if key and key not in seen:
            output.append(row); seen.add(key)
    for row in global_rows[: max(0, limit - len(output))]:
        key = str(row.get("id") or row.get("url") or row.get("title"))
        if key and key not in seen:
            output.append(row); seen.add(key)
    for row in ordered:
        if len(output) >= limit:
            break
        key = str(row.get("id") or row.get("url") or row.get("title"))
        if not key or key in seen:
            continue
        output.append(row); seen.add(key)
    return output[:limit]
