from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from .cache import TTLCache
from .config import settings
from .http_client import UpstreamError, request_json
from .scoring import authority_score, freshness_score, overall_score

SERPER_SEARCH_URL = "https://google.serper.dev/search"
SERPER_NEWS_URL = "https://google.serper.dev/news"
_CACHE = TTLCache(max_items=120, ttl_seconds=6 * 60)


def available() -> bool:
    return bool(settings.serper_api_key)


def search(query: str, *, kind: str = "news", count: int = 10, gl: str = "cn", hl: str = "zh-cn") -> list[dict[str, Any]]:
    """Small Google/Serper fallback used only when Tavily recall is too thin.

    The project still treats Tavily as the primary research provider. This lane
    exists because Chinese media, data-industry sites and WeChat public pages can
    be indexed differently by Google. It is intentionally called only when the
    Tavily candidate pool is empty or very small, so normal requests do not pay
    for duplicate searches.
    """
    if not available():
        return []
    q = " ".join(str(query or "").split())[:260]
    if not q:
        return []
    kind = "news" if kind == "news" else "web"
    count = max(5, min(int(count or 10), 20))
    key = json.dumps({"q": q, "kind": kind, "count": count, "gl": gl, "hl": hl}, ensure_ascii=False, sort_keys=True)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    endpoint = SERPER_NEWS_URL if kind == "news" else SERPER_SEARCH_URL
    data = request_json(
        endpoint,
        method="POST",
        headers={
            "X-API-KEY": settings.serper_api_key,
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
        },
        payload={"q": q, "gl": gl or "cn", "hl": hl or "zh-cn", "num": count},
        timeout=8,
        retries=0,
    )
    rows = data.get("news") if kind == "news" else data.get("organic")
    out: list[dict[str, Any]] = []
    for rank, item in enumerate(rows or [], start=1):
        if not isinstance(item, dict):
            continue
        url = str(item.get("link") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url.startswith(("http://", "https://")) or not title:
            continue
        source = str(item.get("source") or _host(url) or "网页")
        published = _normalize_date(str(item.get("date") or ""))
        source_type = "news" if kind == "news" else "web"
        authority = authority_score(url, source_type)
        fresh = freshness_score(published)
        # Serper has no Tavily relevance score. Position is used only as a weak
        # prior; the local intent matcher remains the dominant gate/ranker.
        relevance = max(0.42, 0.86 - (rank - 1) * 0.035)
        out.append({
            "id": f"serper-{_stable_id(url or title)}",
            "type": source_type,
            "title": title,
            "url": url,
            "source": source,
            "publishedAt": published,
            "snippet": str(item.get("snippet") or "")[:1100],
            "rawContent": "",
            "authors": [],
            "citations": None,
            "readCount": None,
            "openAccess": None,
            "relevance": round(relevance, 4),
            "authorityScore": authority,
            "freshnessScore": fresh,
            "score": overall_score(relevance=relevance, authority=authority, freshness=fresh, source_type=source_type),
            "images": [],
            "provider": "serper",
            "providerRank": rank,
            "originRegion": "domestic" if gl == "cn" else "global",
        })
    _CACHE.put(key, out)
    return out


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _stable_id(value: str) -> str:
    return hashlib.blake2s(str(value or "").encode("utf-8"), digest_size=8).hexdigest()


def _normalize_date(value: str) -> str | None:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    if not value:
        return None
    now = datetime.now(timezone.utc)
    if value in {"刚刚", "刚才", "今天"}:
        return now.date().isoformat()
    if value == "昨天":
        return (now - timedelta(days=1)).date().isoformat()
    if value == "前天":
        return (now - timedelta(days=2)).date().isoformat()
    m = re.match(r"(?i)(\d+)\s*(minute|hour|day|week|month|year)s?\s+ago", value)
    if m:
        n = int(m.group(1)); unit = m.group(2).lower()
        days = {"minute": 0, "hour": 0, "day": n, "week": n * 7, "month": n * 30, "year": n * 365}[unit]
        delta = timedelta(days=days, minutes=n if unit == "minute" else 0, hours=n if unit == "hour" else 0)
        return (now - delta).date().isoformat()
    # Google/Serper commonly localises relative dates for Chinese results.
    # Normalize them here instead of letting the local freshness filter treat
    # a fresh article as having an unknown date. This costs no provider calls.
    m = re.match(r"(\d+)\s*(分钟|小时|天|日|周|星期|个月|月|年)前", value)
    if m:
        n = int(m.group(1)); unit = m.group(2)
        if unit == "分钟":
            delta = timedelta(minutes=n)
        elif unit == "小时":
            delta = timedelta(hours=n)
        elif unit in {"天", "日"}:
            delta = timedelta(days=n)
        elif unit in {"周", "星期"}:
            delta = timedelta(days=n * 7)
        elif unit in {"个月", "月"}:
            delta = timedelta(days=n * 30)
        else:
            delta = timedelta(days=n * 365)
        return (now - delta).date().isoformat()
    cn = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日?", value)
    if cn:
        try:
            return datetime(int(cn.group(1)), int(cn.group(2)), int(cn.group(3))).date().isoformat()
        except ValueError:
            pass
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return value[:40]
