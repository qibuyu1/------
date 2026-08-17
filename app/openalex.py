from __future__ import annotations

import json
from typing import Any

from .cache import TTLCache
from .config import settings
from .http_client import request_json, with_query
from .scoring import authority_score, freshness_score, overall_score

OPENALEX_URL = "https://api.openalex.org/works"
_CACHE = TTLCache(max_items=160, ttl_seconds=10 * 60)


def search_papers(
    query: str,
    *,
    max_results: int = 8,
    from_year: int | None = None,
    start_date: str = "",
    end_date: str = "",
) -> list[dict[str, Any]]:
    cache_key = json.dumps(
        {"q": query.strip(), "n": max_results, "year": from_year, "start": start_date, "end": end_date},
        ensure_ascii=False,
        sort_keys=True,
    )
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    params: dict[str, Any] = {
        "search": query,
        "per-page": max(1, min(max_results, 20)),
        "sort": "relevance_score:desc",
        "select": "id,doi,title,display_name,publication_date,publication_year,authorships,primary_location,best_oa_location,open_access,cited_by_count,abstract_inverted_index",
    }
    filters: list[str] = []
    if start_date:
        filters.append(f"from_publication_date:{start_date[:10]}")
    elif from_year:
        filters.append(f"from_publication_date:{from_year}-01-01")
    if end_date:
        filters.append(f"to_publication_date:{end_date[:10]}")
    if filters:
        params["filter"] = ",".join(filters)
    if settings.openalex_mailto:
        params["mailto"] = settings.openalex_mailto

    data = request_json(with_query(OPENALEX_URL, params), timeout=8, retries=0)
    output: list[dict[str, Any]] = []
    for item in data.get("results", []) or []:
        title = str(item.get("display_name") or item.get("title") or "Untitled")
        doi = str(item.get("doi") or "").strip()
        primary = item.get("primary_location") or {}
        best_oa = item.get("best_oa_location") or {}
        landing = str(primary.get("landing_page_url") or best_oa.get("landing_page_url") or "").strip()
        pdf_url = str(best_oa.get("pdf_url") or primary.get("pdf_url") or "").strip()
        # Prefer the publisher/repository landing page when OpenAlex provides one.
        # DOI remains the canonical fallback and OpenAlex itself is the last resort.
        url = _public_url(landing) or _public_url(doi) or _public_url(str(item.get("id") or ""))
        pdf_url = _public_url(pdf_url)
        authors = []
        for authorship in item.get("authorships") or []:
            name = ((authorship.get("author") or {}).get("display_name"))
            if name:
                authors.append(name)
        published = item.get("publication_date")
        authority = authority_score(str(url), "paper")
        freshness = freshness_score(str(published) if published else None)
        citations = int(item.get("cited_by_count") or 0)
        relevance = float(item.get("relevance_score") or 0.78)
        score = overall_score(relevance=relevance, authority=authority, freshness=freshness, source_type="paper", citations=citations)
        abstract = _abstract(item.get("abstract_inverted_index"))
        output.append(
            {
                "id": f"paper-{str(item.get('id') or abs(hash(title))).split('/')[-1]}",
                "type": "paper",
                "title": title,
                "url": str(url),
                "pdfUrl": pdf_url,
                "source": "OpenAlex",
                "publishedAt": published,
                "snippet": abstract[:1100],
                "rawContent": abstract[:6500],
                "authors": authors[:8],
                "citations": citations,
                "readCount": None,
                "openAccess": bool((item.get("open_access") or {}).get("is_oa")),
                "relevance": relevance,
                "authorityScore": authority,
                "freshnessScore": freshness,
                "score": score,
                "images": [],
            }
        )
    _CACHE.put(cache_key, output)
    return output


def _abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, indices in index.items():
        for pos in indices:
            positions.append((int(pos), word))
    positions.sort(key=lambda pair: pair[0])
    return " ".join(word for _, word in positions)


def _public_url(value: str) -> str:
    value = str(value or "").strip()
    return value if value.startswith(("http://", "https://")) else ""
