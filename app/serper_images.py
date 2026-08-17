from __future__ import annotations

import json
from typing import Any

from .cache import TTLCache
from .config import settings
from .http_client import UpstreamError, request_json

SERPER_IMAGE_SEARCH_URL = "https://google.serper.dev/images"
_IMAGE_CACHE = TTLCache(max_items=220, ttl_seconds=20 * 60)


def available() -> bool:
    return bool(settings.serper_api_key)


def search_images(
    query: str, *, count: int = 10, gl: str = "cn", hl: str = "zh-cn"
) -> list[dict[str, Any]]:
    """Search Google Images through Serper and normalize image candidates.

    The article pipeline treats Serper as the *only* online image-search
    provider. News/policy retrieval can still use Tavily, but image discovery,
    source-page metadata, thumbnails and original-image URLs all come from
    Serper.
    """
    if not available():
        raise UpstreamError("SERPER_API_KEY is not configured")

    q = " ".join(str(query or "").split())[:380]
    if not q:
        return []
    count = max(8, min(int(count or 10), 20))
    cache_key = json.dumps({"q": q, "count": count, "gl": gl, "hl": hl}, ensure_ascii=False, sort_keys=True)
    cached = _IMAGE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    data = request_json(
        SERPER_IMAGE_SEARCH_URL,
        method="POST",
        headers={
            "X-API-KEY": settings.serper_api_key,
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
        },
        payload={
            "q": q,
            "gl": gl or "cn",
            "hl": hl or "zh-cn",
            "num": count,
        },
        timeout=7,
        retries=0,
    )

    output: list[dict[str, Any]] = []
    for index, row in enumerate(data.get("images") or [], start=1):
        if not isinstance(row, dict):
            continue
        original_url = str(row.get("imageUrl") or "").strip()
        thumbnail_url = str(row.get("thumbnailUrl") or "").strip()
        image_url = original_url or thumbnail_url
        if not image_url.startswith(("http://", "https://")):
            continue

        position = _as_int(row.get("position")) or index
        source_url = str(row.get("link") or "").strip()
        title = str(row.get("title") or "").strip()
        source = str(row.get("source") or row.get("domain") or "").strip()
        domain = str(row.get("domain") or "").strip()
        width = _as_int(row.get("imageWidth")) or _as_int(row.get("thumbnailWidth"))
        height = _as_int(row.get("imageHeight")) or _as_int(row.get("thumbnailHeight"))
        # Google Images' rank is a useful weak prior, but semantic/source checks
        # in visuals.py remain the dominant ranking signals.
        result_score = max(0.25, 1.0 - (max(1, position) - 1) * 0.025)

        output.append({
            "url": image_url,
            "fallbackUrl": thumbnail_url if thumbnail_url and thumbnail_url != image_url else "",
            "originalUrl": original_url,
            "description": title,
            "source": source,
            "sourceUrl": source_url,
            "sourceTitle": title,
            "sourceSnippet": " · ".join(x for x in (source, domain) if x),
            "resultScore": result_score,
            "position": position,
            "domain": domain,
            "width": width,
            "height": height,
            "provider": "serper",
            "searchQuery": q,
        })

    _IMAGE_CACHE.put(cache_key, output)
    return output


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
