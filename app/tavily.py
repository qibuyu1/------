from __future__ import annotations

import hashlib
import json
from typing import Any

from .cache import TTLCache
from .config import settings
from .http_client import UpstreamError, request_json
from .scoring import authority_score, freshness_score, overall_score

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
POLICY_DOMAINS = [
    "gov.cn", "ndrc.gov.cn", "cac.gov.cn", "miit.gov.cn", "stats.gov.cn", "samr.gov.cn",
    "mof.gov.cn", "pbc.gov.cn", "mofcom.gov.cn", "data.gov.cn", "nfra.gov.cn",
]
DOMESTIC_NEWS_DOMAINS = [
    "xinhuanet.com", "people.com.cn", "cctv.com", "news.cn", "yicai.com", "cls.cn",
    "thepaper.cn", "caixin.com", "stcn.com", "21jingji.com", "cnstock.com", "jjckb.cn",
    "ce.cn", "gmw.cn", "chinanews.com.cn", "chinanews.com", "stdaily.com", "economicdaily.com.cn",
    "cnr.cn", "36kr.com", "finance.sina.com.cn", "sina.com.cn", "qq.com", "sohu.com",
    "mp.weixin.qq.com",
]
GLOBAL_NEWS_DOMAINS = [
    "reuters.com", "apnews.com", "bbc.com", "ft.com", "bloomberg.com", "wsj.com", "nytimes.com",
    "scmp.com", "nikkei.com",
]
GLOBAL_POLICY_DOMAINS = [
    "oecd.org", "europa.eu", "ec.europa.eu", "gov.uk", "whitehouse.gov", "congress.gov",
    "worldbank.org", "imf.org", "unctad.org", "weforum.org",
]

PAPER_DOMAINS = [
    "arxiv.org", "doi.org", "link.springer.com", "sciencedirect.com", "nature.com",
    "pmc.ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov", "ieeexplore.ieee.org", "mdpi.com",
    "frontiersin.org", "ssrn.com", "tandfonline.com", "onlinelibrary.wiley.com",
    "journals.sagepub.com", "cambridge.org", "oxfordacademic.com", "cnki.net",
    "wanfangdata.com.cn",
]
PAPER_GLOBAL_DOMAINS = [
    "arxiv.org", "doi.org", "link.springer.com", "sciencedirect.com", "nature.com",
    "pmc.ncbi.nlm.nih.gov", "ieeexplore.ieee.org", "mdpi.com", "frontiersin.org",
    "ssrn.com", "tandfonline.com", "onlinelibrary.wiley.com", "journals.sagepub.com",
    "cambridge.org", "oxfordacademic.com",
]
PAPER_DOMESTIC_DOMAINS = [
    "cnki.net", "wanfangdata.com.cn", "cqvip.com", "qikan.com", "cssn.cn", "cass.cn",
    "edu.cn", "caict.ac.cn", "ict.ac.cn", "amss.ac.cn",
]

DOMESTIC_AUTHORITY_DOMAINS = list(dict.fromkeys(POLICY_DOMAINS + DOMESTIC_NEWS_DOMAINS))

_SEARCH_CACHE = TTLCache(max_items=160, ttl_seconds=4 * 60)
_EXTRACT_CACHE = TTLCache(max_items=96, ttl_seconds=12 * 60)
_EXTRACT_DETAIL_CACHE = TTLCache(max_items=72, ttl_seconds=12 * 60)



def _stable_id(value: str) -> str:
    return hashlib.blake2s(str(value or "").encode("utf-8"), digest_size=8).hexdigest()

def available() -> bool:
    return bool(settings.tavily_api_key)


def search(
    query: str,
    *,
    topic: str = "general",
    time_range: str = "latest",
    max_results: int = 8,
    include_images: bool = False,
    domains: list[str] | None = None,
    mode: str = "fast",
    start_date: str = "",
    end_date: str = "",
    country: str = "",
) -> dict[str, Any]:
    """Search Tavily with a speed-first default and an optional quality mode.

    ``fast`` is used for interactive result pages: Tavily fast-depth search,
    compact snippets, no LLM answer/raw-page extraction/images, plus one short
    transient-error retry. ``quality`` is reserved for flows that explicitly need richer source text.

    ``include_images`` is retained only for API compatibility with older code,
    but V9 intentionally ignores it: all user-facing article image discovery is
    routed exclusively through Serper / Google Images.
    """
    if not available():
        raise UpstreamError("TAVILY_API_KEY is not configured")

    mode = "quality" if mode == "quality" else "fast"
    topic = topic if topic in {"general", "news", "finance"} else "general"
    cache_key = json.dumps(
        {
            "q": query.strip(), "topic": topic, "range": time_range, "n": max_results,
            "images": False, "domains": domains or [], "mode": mode,
            "start": start_date, "end": end_date, "country": country,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    cached = _SEARCH_CACHE.get(cache_key)
    if cached is not None:
        cached["cacheHit"] = True
        return cached

    quality = mode == "quality"
    base_payload: dict[str, Any] = {
        "query": query,
        "topic": topic,
        "search_depth": "advanced" if quality else "fast",
        "max_results": max(1, min(max_results, 20)),
        "include_answer": "basic" if quality else False,
        "include_raw_content": bool(quality),
        "include_images": False,
        "include_image_descriptions": False,
        "include_usage": True,
    }
    if quality:
        base_payload["chunks_per_source"] = 2
    if time_range in {"day", "week", "month", "year"}:
        base_payload["time_range"] = time_range
    if start_date:
        base_payload["start_date"] = start_date[:10]
    if end_date:
        base_payload["end_date"] = end_date[:10]
    if domains:
        base_payload["include_domains"] = domains[:300]
    base_payload["exclude_domains"] = ["msn.com", "newsbreak.com", "flipboard.com", "smartnews.com", "yahoo.com"]
    if country:
        base_payload["country"] = country
    elif topic == "general" and not domains:
        base_payload["country"] = "china"

    # Fast mode makes one compact request first. A single retry absorbs common
    # transient 429/5xx/connection hiccups without putting normal requests on a slow
    # path. If Tavily answered successfully but found zero rows, retry once at the
    # balanced basic depth before the pipeline decides whether to show fallback data.
    try:
        data = request_json(
            TAVILY_SEARCH_URL,
            method="POST",
            headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
            payload=base_payload,
            timeout=24 if quality else 8,
            retries=1,
        )
    except UpstreamError:
        raise

    if not data.get("results") and not quality:
        balanced = dict(base_payload)
        balanced["search_depth"] = "basic"
        balanced.pop("chunks_per_source", None)
        try:
            data = request_json(
                TAVILY_SEARCH_URL,
                method="POST",
                headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
                payload=balanced,
                timeout=10,
                retries=0,
            )
        except UpstreamError:
            # Keep the valid zero-result response; caller can decide whether to
            # show an empty state or a clearly labelled fallback.
            pass

    results: list[dict[str, Any]] = []
    for provider_rank, item in enumerate(data.get("results", []) or [], start=1):
        url = str(item.get("url") or "")
        title = str(item.get("title") or "未命名来源")
        published = item.get("published_date") or item.get("published_at")
        result_type = "news" if topic == "news" else "web"
        authority = authority_score(url, result_type)
        fresh = freshness_score(str(published) if published else None)
        score = overall_score(relevance=item.get("score"), authority=authority, freshness=fresh, source_type=result_type)
        source_name = _source_name(url)
        read_count = _optional_count(item, "read_count", "view_count", "views", "page_views")
        raw_content = str(item.get("raw_content") or "") if quality else ""
        results.append(
            {
                "id": f"web-{_stable_id(url or title)}",
                "type": result_type,
                "title": title,
                "url": url,
                "source": source_name,
                "publishedAt": published,
                "snippet": str(item.get("content") or "")[:1100],
                "rawContent": raw_content[:12_000],
                "authors": [],
                "citations": None,
                "readCount": read_count,
                "openAccess": None,
                "relevance": round(float(item.get("score") or 0), 4),
                "authorityScore": authority,
                "freshnessScore": fresh,
                "score": score,
                "images": [],
                "provider": "tavily",
                "providerRank": provider_rank,
            }
        )

    output = {
        "answer": data.get("answer") or "",
        "results": results,
        "images": [],
        "usage": data.get("usage") or {},
        "responseTime": data.get("response_time"),
        "requestId": data.get("request_id"),
        "cacheHit": False,
    }
    _SEARCH_CACHE.put(cache_key, output)
    return output


def search_policy(
    query: str,
    *,
    time_range: str = "latest",
    max_results: int = 6,
    mode: str = "fast",
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    enriched = str(query).strip()
    data = search(
        enriched,
        topic="general",
        time_range=time_range,
        max_results=max_results,
        include_images=False,
        domains=POLICY_DOMAINS,
        mode=mode,
        start_date=start_date,
        end_date=end_date,
        country="china",
    )
    for result in data["results"]:
        result["type"] = "policy"
        result["originRegion"] = "domestic"
        result["authorityScore"] = max(95, int(result.get("authorityScore") or 0))
        result["score"] = overall_score(
            relevance=result.get("relevance"),
            authority=result["authorityScore"],
            freshness=result["freshnessScore"],
            source_type="policy",
        )
    return data


def search_domestic_news(
    query: str,
    *,
    time_range: str = "latest",
    max_results: int = 8,
    mode: str = "fast",
    trusted_only: bool = False,
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """Search Chinese sources first, with an optional authoritative-domain lane."""
    data = search(
        str(query).strip(),
        topic="news",
        time_range=time_range,
        max_results=max_results,
        include_images=False,
        domains=DOMESTIC_AUTHORITY_DOMAINS if trusted_only else None,
        mode=mode,
        start_date=start_date,
        end_date=end_date,
        country="",
    )
    for result in data["results"]:
        result["type"] = "news"
        result["originRegion"] = "domestic"
        if trusted_only:
            result["authorityScore"] = max(78, int(result.get("authorityScore") or 0))
    return data


def search_domestic_web(
    query: str,
    *,
    time_range: str = "latest",
    max_results: int = 8,
    mode: str = "fast",
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """Browser-like Chinese web discovery lane.

    Tavily's ``news`` topic is intentionally focused on current mainstream news.
    Conceptual Chinese queries often surface stronger policy explainers, trade-media
    pages and research pages through a normal web search, just like a browser does.
    This lane uses ``topic=general`` plus a China boost and is merged with the news
    lane locally.
    """
    data = search(
        str(query).strip(),
        topic="general",
        time_range=time_range,
        max_results=max_results,
        include_images=False,
        domains=None,
        mode=mode,
        start_date=start_date,
        end_date=end_date,
        country="china",
    )
    for result in data["results"]:
        result["originRegion"] = "domestic"
        result["discoveryLane"] = "general-web"
    return data


def search_global_policy(
    query: str,
    *,
    time_range: str = "latest",
    max_results: int = 5,
    mode: str = "fast",
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    data = search(
        str(query).strip(), topic="general", time_range=time_range, max_results=max_results,
        include_images=False, domains=GLOBAL_POLICY_DOMAINS, mode=mode,
        start_date=start_date, end_date=end_date,
    )
    for result in data["results"]:
        result["type"] = "policy"
        result["originRegion"] = "global"
        result["authorityScore"] = max(84, int(result.get("authorityScore") or 0))
    return data



def search_papers(
    query: str,
    *,
    max_results: int = 8,
    mode: str = "fast",
    start_date: str = "",
    end_date: str = "",
    domestic_query: str = "",
    global_query: str = "",
    region_preference: str = "domestic-first",
) -> list[dict[str, Any]]:
    """Search domestic and international academic sources in parallel.

    We deliberately do two small searches rather than one huge academic query:
    Chinese databases have very different indexing behavior from international
    journal repositories. The candidate pool is merged and deduplicated locally.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    base = str(query).strip()
    domestic_q = str(domestic_query or f'{base} 中文 论文 研究 期刊 学术').strip()
    global_q = str(global_query or f'{base} research paper study journal').strip()
    domestic_target = max_results
    global_target = 0
    if region_preference == "global-only":
        domestic_target, global_target = 0, max_results
    elif region_preference == "global-first":
        domestic_target = max(3, max_results // 3)
        global_target = max(5, max_results - domestic_target + 2)
    elif region_preference == "domestic+global":
        domestic_target = max(5, (max_results * 2) // 3 + 1)
        global_target = max(4, max_results - domestic_target + 3)
    elif region_preference == "domestic-first":
        domestic_target = max(6, (max_results * 3) // 4 + 2)
        global_target = max(3, max_results - domestic_target + 3)
    jobs = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="paper-search") as pool:
        if domestic_target:
            jobs[pool.submit(search, domestic_q, topic="general", time_range="latest", max_results=domestic_target, include_images=False, domains=PAPER_DOMESTIC_DOMAINS, mode=mode, start_date=start_date, end_date=end_date, country="china")] = "domestic"
        if global_target:
            jobs[pool.submit(search, global_q, topic="general", time_range="latest", max_results=global_target, include_images=False, domains=PAPER_GLOBAL_DOMAINS, mode=mode, start_date=start_date, end_date=end_date)] = "global"
        rows=[]
        for fut in as_completed(jobs):
            try:
                region = jobs[fut]
                for result in (fut.result() or {}).get("results") or []:
                    result = dict(result)
                    result["originRegion"] = region
                    rows.append(result)
            except Exception:
                continue
    out=[]
    seen=set()
    for row in rows:
        row=dict(row)
        url=str(row.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        row["type"]="paper"
        row["source"] = row.get("source") or "论文来源"
        row["openAccess"] = any(d in url.lower() for d in ("arxiv.org", "pmc.ncbi.nlm.nih.gov", "ssrn.com", "mdpi.com", "frontiersin.org"))
        row["citations"] = None
        row["readCount"] = None
        text=f"{row.get('title','')} {row.get('snippet','')} {row.get('source','')}".lower()
        markers=("doi", "journal", "abstract", "paper", "study", "research", "proceedings", "arxiv", "springer", "nature", "期刊", "论文", "研究", "学报", "课题")
        domestic_academic = row.get("originRegion") == "domestic" and any(domain in url.lower() for domain in PAPER_DOMESTIC_DOMAINS)
        if not domestic_academic and not any(m in text for m in markers):
            continue
        row["score"] = overall_score(
            relevance=row.get("relevance"), authority=max(int(row.get("authorityScore") or 0), 86),
            freshness=row.get("freshnessScore"), source_type="paper",
        )
        out.append(row)
    out.sort(key=lambda x:(x.get("originRegion") == "domestic" if region_preference.startswith("domestic") else x.get("originRegion") == "global", float(x.get("relevance") or 0), int(x.get("freshnessScore") or 0), int(x.get("authorityScore") or 0)), reverse=True)
    return out[:max_results]


def extract_url_details(
    urls: list[str], *, query: str = "", chunks_per_source: int = 1, include_images: bool = True
) -> dict[str, dict[str, Any]]:
    """Extract selected origin pages once, including their real page images.

    Article generation already hydrates the selected evidence URLs. Asking Tavily
    Extract for page images in the same batch gives the visual stage an origin-page
    image pool without spending a second image-search request. These are candidates,
    not automatically trusted: ``visuals.py`` still checks dimensions, duplicates
    and semantic/source alignment before placement.
    """
    if not available():
        return {}
    clean: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        url = str(raw or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url); clean.append(url)
        if len(clean) >= 10:
            break
    if not clean:
        return {}
    cache_key = json.dumps({"urls": clean, "q": query, "chunks": chunks_per_source, "images": bool(include_images)}, ensure_ascii=False, sort_keys=True)
    cached = _EXTRACT_DETAIL_CACHE.get(cache_key)
    if cached is not None:
        return cached
    payload = {
        "urls": clean,
        "query": query[:400] or None,
        "chunks_per_source": max(1, min(int(chunks_per_source), 5)),
        "extract_depth": "basic",
        "include_images": bool(include_images),
        "format": "markdown",
        "timeout": 8,
        "include_usage": False,
    }
    try:
        data = request_json(
            TAVILY_EXTRACT_URL, method="POST",
            headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
            payload=payload, timeout=13, retries=1,
        )
    except UpstreamError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in data.get("results") or []:
        url = str(row.get("url") or "")
        if not url:
            continue
        images: list[str] = []
        for raw_image in row.get("images") or []:
            image_url = str(raw_image.get("url") if isinstance(raw_image, dict) else raw_image or "").strip()
            if image_url.startswith(("http://", "https://")) and image_url not in images:
                images.append(image_url)
        out[url] = {
            "content": str(row.get("raw_content") or "")[:14_000],
            "images": images[:16],
        }
    _EXTRACT_DETAIL_CACHE.put(cache_key, out)
    return out


def extract_urls(urls: list[str], *, query: str = "", chunks_per_source: int = 3) -> dict[str, str]:
    """Batch-extract only the sources that are actually going into an article.

    This keeps interactive search fast while giving the writing stage richer
    evidence. Tavily supports multiple URLs in one Extract request, so the
    selected sources are hydrated together instead of one request per article.
    """
    if not available():
        return {}
    clean = []
    seen = set()
    for raw in urls:
        url = str(raw or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        clean.append(url)
        if len(clean) >= 12:
            break
    if not clean:
        return {}

    cache_key = json.dumps({"urls": clean, "q": query, "chunks": chunks_per_source}, ensure_ascii=False, sort_keys=True)
    cached = _EXTRACT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    payload = {
        "urls": clean,
        "query": query[:400] or None,
        "chunks_per_source": max(1, min(int(chunks_per_source), 5)),
        "extract_depth": "basic",
        "include_images": False,
        "format": "markdown",
        "timeout": 8,
        "include_usage": False,
    }
    try:
        data = request_json(
            TAVILY_EXTRACT_URL,
            method="POST",
            headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
            payload=payload,
            timeout=13,
            retries=1,
        )
    except UpstreamError:
        return {}

    extracted: dict[str, str] = {}
    for row in data.get("results") or []:
        url = str(row.get("url") or "")
        content = str(row.get("raw_content") or "").strip()
        if url and content:
            extracted[url] = content[:14_000]
    _EXTRACT_CACHE.put(cache_key, extracted)
    return extracted


def _optional_count(item: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return None


def _source_name(url: str) -> str:
    try:
        host = url.split("//", 1)[-1].split("/", 1)[0].replace("www.", "")
        return host or "网页"
    except Exception:
        return "网页"
