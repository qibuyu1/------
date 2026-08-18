from __future__ import annotations

"""Homepage recommendation feed with a seven-day persistent cache.

The homepage is a discovery surface, not a live-news terminal. Re-running five Tavily
lanes on every visit wastes quota without materially improving the user experience.
A successful feed is therefore reused for seven days, including across server restarts.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from time import time
from typing import Any

from .pipeline import research

_LOCK = Lock()
_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
_CACHE_SECONDS = 7 * 24 * 60 * 60
_ROOT = Path(__file__).resolve().parents[1]
_CACHE_FILE = Path(os.getenv("DEG_HOME_FEED_CACHE", str(_ROOT / "data" / "home_feed_cache.json")))


def _read_disk_cache() -> tuple[float, dict[str, Any] | None]:
    try:
        raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        at = float(raw.get("savedAt") or 0)
        data = raw.get("data") if isinstance(raw.get("data"), dict) else None
        return at, data
    except Exception:
        return 0.0, None


def _write_disk_cache(at: float, data: dict[str, Any]) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_FILE.with_suffix(_CACHE_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps({"savedAt": at, "data": data}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, _CACHE_FILE)
    except Exception:
        # Feed persistence must never break the homepage.
        return


def _cached(now: float) -> dict[str, Any] | None:
    with _LOCK:
        data = _CACHE.get("data")
        at = float(_CACHE.get("at") or 0)
        if data and now - at < _CACHE_SECONDS:
            return dict(data)
    disk_at, disk_data = _read_disk_cache()
    if disk_data and now - disk_at < _CACHE_SECONDS:
        with _LOCK:
            _CACHE["at"] = disk_at
            _CACHE["data"] = disk_data
        return dict(disk_data)
    return None


def home_feed(*, force: bool = False) -> dict[str, Any]:
    """Build or reuse a seven-day recommendation snapshot."""
    now = time()
    if not force:
        hit = _cached(now)
        if hit:
            hit["cacheTtlDays"] = 7
            hit["cached"] = True
            return hit

    from concurrent.futures import ThreadPoolExecutor, as_completed

    base_queries = [
        ("国内政策", "数据要素 国家数据局 数据基础制度 公共数据 授权运营", "policy", "domestic-only"),
        ("国内热点", "数据要素 市场化配置 数据流通 数据价值 最新动态", "news", "domestic-only"),
        ("产业实践", "数据资产入表 可信数据空间 数据交易 企业实践 案例", "news", "domestic-only"),
        ("中文研究", "数据要素市场化 数据治理 数据流通 中文论文", "paper", "domestic-only"),
        ("国际补充", "data governance data spaces data markets latest policy practice", "news", "global-only"),
    ]
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    try:
        with ThreadPoolExecutor(max_workers=5, thread_name_prefix="home-feed") as pool:
            futures = {
                pool.submit(
                    research,
                    {"query": q, "types": [kind], "timeRange": "latest", "maxResults": 8, "searchMode": "fast", "surface": "home", "regionPreference": region},
                ): name
                for name, q, kind, region in base_queries
            }
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    data = fut.result()
                    rows.extend([x for x in data.get("results") or [] if x.get("sourceUsable") or x.get("sourceVerified")])
                    warnings.extend(data.get("warnings") or [])
                except Exception as exc:
                    warnings.append(f"{name}刷新失败：{str(exc)[:140]}")
    except Exception as exc:
        warnings.append(f"首页推荐刷新失败：{str(exc)[:160]}")

    if not rows:
        # Even an expired disk snapshot is preferable to consuming more quota in a
        # refresh loop that keeps failing. Return it as stale and try again only on a
        # later explicit refresh/server call.
        disk_at, stale = _read_disk_cache()
        if stale and stale.get("items"):
            result = dict(stale)
            result.update({"stale": True, "cached": True, "warnings": warnings, "cacheTtlDays": 7, "savedAt": disk_at})
            return result
        return {"items": [], "demo": False, "warnings": warnings, "generatedAt": datetime.now(timezone.utc).isoformat(), "stale": False, "cached": False, "cacheTtlDays": 7}

    items = _balanced_feed(rows, limit=24)
    result = {
        "items": items,
        "demo": False,
        "warnings": warnings,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "stale": False,
        "cached": False,
        "cacheTtlDays": 7,
    }
    with _LOCK:
        _CACHE["at"] = now
        _CACHE["data"] = result
    _write_disk_cache(now, result)
    return result


def _balanced_feed(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: (int(row.get("score") or 0), int(row.get("queryMatchScore") or 0), int(row.get("freshnessScore") or 0)), reverse=True)
    if not ordered:
        return []
    top_score = int(ordered[0].get("score") or 0)
    threshold = max(55, top_score - 14)
    output: list[dict[str, Any]] = []
    seen = set()
    for kind in ("news", "paper", "policy"):
        hit = next((row for row in ordered if row.get("type") == kind and int(row.get("score") or 0) >= threshold), None)
        if hit:
            key = str(hit.get("id") or hit.get("url") or hit.get("title"))
            if key and key not in seen:
                output.append(hit); seen.add(key)
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
