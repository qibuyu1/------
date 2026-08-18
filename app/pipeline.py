from __future__ import annotations

import json
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4
from datetime import date, datetime, timedelta
from typing import Any

from . import deepseek, tavily, serper_search
from .article_store import article_store
from .cache import TTLCache
from .content_blocks import merge_visuals_into_blocks, plan_visual_slots
from .scoring import normalize_title
from .query_intent import understand, local_plan, DOMAIN_HINTS
from .brief import understand_writing_brief, local_brief
from .source_verify import verify_results
from .visuals import resolve_visuals


def _timed_call(fn, *args, **kwargs):
    started = time.perf_counter()
    value = fn(*args, **kwargs)
    return value, int((time.perf_counter() - started) * 1000)


def _classify_discovery_row(row: dict[str, Any], requested_types: set[str]) -> dict[str, Any]:
    """Classify a browser-like web result into the UI's source buckets."""
    out = dict(row)
    url = str(out.get("url") or "").lower()
    text = f"{out.get('title','')} {out.get('snippet','')} {out.get('source','')}".lower()
    if "policy" in requested_types and ("gov.cn" in url or any(x in text for x in ("国家数据局", "人民政府", "实施意见", "工作指引", "政策解读"))):
        out["type"] = "policy"
        out["authorityScore"] = max(88, int(out.get("authorityScore") or 0))
    elif "paper" in requested_types and (
        any(domain in url for domain in getattr(tavily, "PAPER_DOMESTIC_DOMAINS", []))
        or any(x in text for x in ("期刊", "学报", "论文", "研究发现", "实证研究", "doi", "journal", "abstract"))
    ):
        out["type"] = "paper"
    elif "news" in requested_types:
        out["type"] = "news"
    elif requested_types:
        out["type"] = next(iter(requested_types))
    return out


def _select_diverse_queries(queries: list[str], *, limit: int = 2) -> list[str]:
    """Keep a tiny set of complementary queries instead of near-duplicates.

    This is used for fallback search and variant lanes so breadth improves without
    increasing request count. The first query keeps the primary intent; later
    entries maximize new token coverage.
    """
    clean = list(dict.fromkeys(" ".join(str(q or "").split()) for q in queries if str(q or "").strip()))
    if not clean or limit <= 0:
        return []
    selected = [clean[0]]
    while len(selected) < min(limit, len(clean)):
        selected_tokens = set().union(*(_query_tokens(q) for q in selected))
        best = None
        best_score = -1.0
        for candidate in clean:
            if candidate in selected:
                continue
            tokens = _query_tokens(candidate)
            novelty = len(tokens - selected_tokens) / max(1, len(tokens))
            compact_bonus = 0.18 if 2 <= len(candidate.split()) <= 4 else 0.0
            score = novelty + compact_bonus
            if score > best_score:
                best, best_score = candidate, score
        if best is None:
            break
        selected.append(best)
    return selected


def _serper_recall(
    queries: list[str], *, requested_types: set[str], count: int = 8,
    max_queries: int = 2, include_news: bool = True,
) -> list[dict[str, Any]]:
    """Run browser-web and news fallbacks concurrently and dedupe by URL.

    The web lane is essential for Chinese conceptual queries because many policy
    explainers, industry articles and academic pages do not appear in a News
    vertical even though a normal browser search finds them immediately.
    """
    if not serper_search.available():
        return []
    clean = _select_diverse_queries(queries, limit=max(1, min(int(max_queries or 1), 2)))
    if not clean:
        return []
    jobs: dict[Any, tuple[str, str]] = {}
    rows: list[dict[str, Any]] = []
    # Two complementary Web searches + one News lane on the primary query give
    # better browser-like recall than six near-duplicate calls, with lower latency
    # and lower Serper consumption.
    with ThreadPoolExecutor(max_workers=min(3, len(clean) + 1), thread_name_prefix="serper-recall") as pool:
        for index, q in enumerate(clean):
            jobs[pool.submit(serper_search.search, q, kind="web", count=count, gl="cn", hl="zh-cn")] = (q, "web")
            if index == 0 and include_news and "news" in requested_types:
                jobs[pool.submit(serper_search.search, q, kind="news", count=max(5, min(count, 8)), gl="cn", hl="zh-cn")] = (q, "news")
        for fut in as_completed(jobs):
            q, lane = jobs[fut]
            try:
                for raw in fut.result() or []:
                    row = _classify_discovery_row(raw, requested_types)
                    row["fallbackQuery"] = q
                    row["discoveryLane"] = f"serper-{lane}"
                    rows.append(row)
            except Exception:
                continue
    return _dedupe(rows)


_RESEARCH_CACHE = TTLCache(max_items=120, ttl_seconds=4 * 60)
_VISUAL_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="visual-background")


def research(payload: dict[str, Any]) -> dict[str, Any]:
    """Parallel, cache-aware retrieval for the interactive research page.

    The default mode is intentionally fast: search result pages only need
    titles, snippets and ranking metadata. Full-page extraction is deferred
    until the user actually generates an article from selected sources.
    """
    started = time.perf_counter()
    query = str(payload.get("query") or "数据要素").strip()[:180]
    description = str(payload.get("description") or payload.get("searchDescription") or "").strip()[:1000]
    requested_types = set(payload.get("types") or ["news", "paper", "policy"]) & {"news", "paper", "policy"}
    if not requested_types:
        requested_types = {"news", "paper", "policy"}
    time_range = str(payload.get("timeRange") or "latest")
    date_from = str(payload.get("dateFrom") or "").strip()[:10]
    date_to = str(payload.get("dateTo") or "").strip()[:10]
    max_results = max(4, min(int(payload.get("maxResults") or 20), 40))
    surface = str(payload.get("surface") or "interactive")
    search_mode = "quality" if str(payload.get("searchMode") or "fast") == "quality" else "fast"
    region_preference = str(payload.get("regionPreference") or "domestic-first").strip()
    if region_preference not in {"domestic-only", "domestic-first", "domestic+global", "global-first", "global-only"}:
        region_preference = "domestic-first"
    upstream_range = {"latest": "latest", "quarter": "year", "custom": "latest", "all": "latest"}.get(time_range, time_range)

    cache_key = json.dumps({
        "q": query, "description": description, "types": sorted(requested_types), "range": time_range,
        "from": date_from, "to": date_to, "max": max_results, "mode": search_mode,
        "region": region_preference,
        "tavily": tavily.available(),
        "serperSearchFallback": serper_search.available(),
    }, ensure_ascii=False, sort_keys=True)
    cached = _RESEARCH_CACHE.get(cache_key)
    if cached is not None:
        cached.setdefault("meta", {})["cacheHit"] = True
        cached["meta"]["elapsedMs"] = int((time.perf_counter() - started) * 1000)
        return cached

    warnings: list[str] = []
    failed_kinds: set[str] = set()
    intent = local_plan(query, description, region_preference) if surface == "home" else understand(query, description, region_preference)
    match_threshold = max(8, min(20, int(intent.get("matchThreshold") or 16)))
    results: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    answers: list[str] = []
    provider_ms: dict[str, int] = {}

    kind_count = max(1, len(requested_types))
    per_kind = min(24, max(8, int(max_results / kind_count * 1.75) + 2))
    jobs = {}
    pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="governance-search")
    try:
        if tavily.available() and "news" in requested_types:
            domestic_q = str(intent.get("domesticNewsQuery") or query).strip()
            global_q = str(intent.get("globalNewsQuery") or query).strip()
            if region_preference != "global-only":
                domestic_budget = max(8, (per_kind * 3) // 4 + 2) if region_preference.startswith("domestic") else max(5, per_kind // 3)
                if not intent.get("isConceptualQuery"):
                    jobs[pool.submit(
                        _timed_call, tavily.search_domestic_news, domestic_q, time_range=upstream_range,
                        max_results=max(5, domestic_budget // 2 + 1), mode=search_mode, trusted_only=True,
                        start_date=date_from if time_range == "custom" else "",
                        end_date=date_to if time_range == "custom" else "",
                    )] = "news-domestic-authority"
                jobs[pool.submit(
                    _timed_call, tavily.search_domestic_news, domestic_q, time_range=upstream_range,
                    max_results=max(6, domestic_budget // 2 + 2), mode=search_mode, trusted_only=False,
                    start_date=date_from if time_range == "custom" else "",
                    end_date=date_to if time_range == "custom" else "",
                )] = "news-domestic-open"
                # Umbrella topics such as “数据要素治理” rarely appear verbatim in
                # every headline. Run two small concrete subtopic queries instead
                # of stuffing year/media/style instructions into one long query.
                raw_news_variants = [str(x).strip() for x in (intent.get("newsQueryVariants") or []) if str(x).strip() and str(x).strip() != domestic_q]
                variant_cap = 3 if intent.get("topicFamilyTerms") else (1 if description else 0)
                selected_news = _select_diverse_queries([domestic_q, *raw_news_variants], limit=1 + variant_cap)[1:] if variant_cap else []
                for variant_index, variant_query in enumerate(selected_news, start=1):
                    jobs[pool.submit(
                        _timed_call, tavily.search_domestic_news, variant_query, time_range=upstream_range,
                        max_results=min(6, max(4, per_kind // 3)), mode=search_mode, trusted_only=False,
                        start_date=date_from if time_range == "custom" else "",
                        end_date=date_to if time_range == "custom" else "",
                    )] = f"news-domestic-concrete-{variant_index}"
                # A normal browser/web lane is deliberately run for conceptual or
                # relationship-style Chinese queries. Tavily's news topic is good
                # for current mainstream stories, but it can miss explainers,
                # industry media, papers and government interpretation pages that
                # ordinary web search surfaces immediately.
                if intent.get("isConceptualQuery"):
                    general_q = str(intent.get("generalDiscoveryQuery") or domestic_q).strip()
                    jobs[pool.submit(
                        _timed_call, tavily.search_domestic_web, general_q, time_range=upstream_range,
                        max_results=min(10, max(6, per_kind // 2 + 2)), mode=search_mode,
                        start_date=date_from if time_range == "custom" else "",
                        end_date=date_to if time_range == "custom" else "",
                    )] = "news-domestic-web"
            if region_preference != "domestic-only":
                global_budget = max(4, per_kind // 4 + 2) if region_preference.startswith("domestic") else max(7, (per_kind * 2) // 3)
                jobs[pool.submit(
                    _timed_call, tavily.search, global_q, topic="news", time_range=upstream_range,
                    max_results=global_budget, include_images=False, domains=tavily.GLOBAL_NEWS_DOMAINS, mode=search_mode,
                    start_date=date_from if time_range == "custom" else "",
                    end_date=date_to if time_range == "custom" else "",
                )] = "news-global"
        if tavily.available() and "policy" in requested_types:
            policy_q = str(intent.get("policyQuery") or query).strip()
            if region_preference != "global-only":
                jobs[pool.submit(
                    _timed_call, tavily.search_policy, policy_q, time_range=upstream_range,
                    max_results=min(18, per_kind if region_preference.startswith("domestic") else max(5, per_kind // 3)), mode=search_mode,
                    start_date=date_from if time_range == "custom" else "",
                    end_date=date_to if time_range == "custom" else "",
                )] = "policy-domestic"
                policy_variants = [str(x).strip() for x in (intent.get("policyQueryVariants") or []) if str(x).strip() and str(x).strip() != policy_q]
                diverse_policy = _select_diverse_queries([policy_q, *policy_variants], limit=2)[1:]
                if diverse_policy and intent.get("topicFamilyTerms"):
                    jobs[pool.submit(
                        _timed_call, tavily.search_policy, diverse_policy[0], time_range=upstream_range,
                        max_results=min(7, max(4, per_kind // 2)), mode=search_mode,
                        start_date=date_from if time_range == "custom" else "",
                        end_date=date_to if time_range == "custom" else "",
                    )] = "policy-domestic-concrete"
            if region_preference in {"domestic+global", "global-first", "global-only"}:
                global_policy_q = f'{str(intent.get("globalNewsQuery") or query).strip()} policy regulation governance'
                global_policy_budget = max(3, per_kind // 4 + 1) if region_preference.startswith("domestic") else max(6, (per_kind * 2) // 3)
                jobs[pool.submit(
                    _timed_call, tavily.search_global_policy, global_policy_q, time_range=upstream_range,
                    max_results=global_policy_budget, mode=search_mode,
                    start_date=date_from if time_range == "custom" else "",
                    end_date=date_to if time_range == "custom" else "",
                )] = "policy-global"
        if "paper" in requested_types and tavily.available():
            paper_q = str(intent.get("paperQuery") or query).strip()
            jobs[pool.submit(
                _timed_call, tavily.search_papers, paper_q, max_results=per_kind, mode=search_mode,
                start_date=date_from if time_range == "custom" else "",
                end_date=date_to if time_range == "custom" else "",
                domestic_query=str(intent.get("domesticPaperQuery") or paper_q),
                global_query=str(intent.get("globalPaperQuery") or query),
                region_preference=region_preference,
            )] = "paper"

        # If the user's description produced genuinely distinct query variants,
        # add at most one extra variant for the *most constrained* selected type.
        # This improves recall without multiplying cost across every provider.
        variants = [str(x).strip() for x in (intent.get("queryVariants") or []) if str(x).strip()]
        primary_queries = {str(intent.get("domesticNewsQuery") or ""), str(intent.get("globalNewsQuery") or ""), str(intent.get("policyQuery") or ""), str(intent.get("paperQuery") or "")}
        extra_variant = next((v for v in variants if v not in primary_queries and v != query), "")
        if extra_variant and description and search_mode == "quality" and tavily.available() and requested_types and region_preference != "global-only":
            # Search the variant as a general-news query only when news is selected;
            # otherwise it can reinforce the broad paper/policy pool.
            if "news" in requested_types:
                jobs[pool.submit(
                    _timed_call, tavily.search_domestic_news, extra_variant, time_range=upstream_range,
                    max_results=min(7, per_kind // 2 + 2), mode=search_mode, trusted_only=False,
                    start_date=date_from if time_range == "custom" else "",
                    end_date=date_to if time_range == "custom" else "",
                )] = "news-domestic-variant"
        for future in as_completed(jobs):
            kind = jobs[future]
            try:
                data, duration_ms = future.result()
                provider_ms[kind] = duration_ms
                if kind == "paper":
                    results.extend(data)
                else:
                    if kind == "news-global":
                        for row in data.get("results") or []:
                            row["originRegion"] = "global"
                    provider_rows = list(data.get("results") or [])
                    if kind == "news-domestic-web":
                        provider_rows = [_classify_discovery_row(row, requested_types) for row in provider_rows]
                    results.extend(provider_rows)
                    images.extend(data.get("images") or [])
                    for row in data.get("results") or []:
                        images.extend(row.get("images") or [])
                    # Provider-generated answer text is intentionally not shown to users;
                    # it can be a search-engine summary unrelated to the exact query.
                    pass
            except Exception as exc:
                failed_kinds.add(kind)
                warnings.append(f"{kind} 检索暂不可用，其他来源仍会继续：{str(exc)[:180]}")
    finally:
        pool.shutdown(wait=True, cancel_futures=False)

    # No synthetic/demo records are ever injected. First apply a strict user-intent
    # gate, then verify a wider candidate pool. This prevents a provider relevance
    # score from promoting unrelated articles before we inspect the source.
    for row in results:
        row["queryMatchScore"] = _query_match_score(query, row, intent=intent)
    results = [r for r in results if int(r.get("queryMatchScore") or 0) >= match_threshold]

    # Tavily remains primary. If its Chinese candidate pool is still extremely
    # thin, use the already-configured Serper key as a *conditional* Google
    # fallback. This is especially useful for media/data-industry sites and
    # WeChat public pages that are indexed differently across search providers.
    fallback_added = 0
    serper_fallback_ran = False
    serper_queries_used: set[str] = set()
    serper_policy_fallback_ran = False
    if len(results) < 4 and serper_search.available() and region_preference != "global-only":
        # Browser-style web search and News search run together. The web lane is
        # deliberately first-class for long conceptual queries; this mirrors the
        # user's browser experience instead of assuming every useful page is in a
        # dedicated News index.
        fallback_queries = list(intent.get("webQueryVariants") or intent.get("newsQueryVariants") or []) or [query]
        first_serper_queries = _select_diverse_queries(fallback_queries, limit=2)
        serper_queries_used.update(first_serper_queries)
        serper_fallback_ran = True
        fallback_rows = _serper_recall(first_serper_queries, requested_types=requested_types, count=8)
        if "policy" in requested_types and not any(r.get("type") == "policy" for r in results):
            try:
                policy_rows = serper_search.search(f"{str(intent.get('policyQuery') or query)} site:gov.cn", kind="web", count=8, gl="cn", hl="zh-cn")
                serper_policy_fallback_ran = True
                fallback_rows.extend(_classify_discovery_row(row, requested_types) for row in policy_rows)
            except Exception as exc:
                warnings.append(f"Serper 政策补充检索暂不可用：{str(exc)[:120]}")
        for row in _dedupe(fallback_rows):
            row["queryMatchScore"] = _query_match_score(query, row, intent=intent)
            if int(row.get("queryMatchScore") or 0) >= match_threshold:
                results.append(row); fallback_added += 1
        if fallback_added:
            warnings.append(f"Tavily 候选较少，已通过 Google/Serper 网页+新闻双通道补充 {fallback_added} 条可核验候选。")

    results.sort(key=lambda x: (int(x.get("queryMatchScore") or 0), float(x.get("relevance") or 0)), reverse=True)
    # Verify the best origin pages eagerly; retain high-confidence search-index
    # records for sites that reject automated crawling. This is both faster and
    # much friendlier to domestic government/media sites.
    verify_limit = min(18, max_results + 4) if surface != "home" else min(14, max_results + 4)
    if results:
        verified, verify_warnings = verify_results(results, limit=verify_limit)
        results = verified
        warnings.extend(verify_warnings)

    # A provider can return many candidates that later fail origin-page checks.
    # Re-evaluate *after* verification as well; otherwise a search can still end
    # at zero even though the optional Google/Serper fallback was never triggered.
    usable_after_verify = [r for r in results if r.get("sourceUsable") or r.get("sourceVerified")]
    if len(usable_after_verify) < 4 and serper_search.available() and region_preference != "global-only":
        existing_urls = {str(r.get("url") or "") for r in results}
        fallback_queries = list(intent.get("webQueryVariants") or intent.get("newsQueryVariants") or []) or [query]
        if serper_fallback_ran:
            # Do not pay for the same Google queries twice after origin-page
            # verification. One unused Web variant is a better second chance than
            # repeating Web+News with identical terms.
            remaining = [q for q in fallback_queries if q not in serper_queries_used]
            second_seed = _select_diverse_queries(remaining, limit=1)
            second_raw = _serper_recall(
                second_seed, requested_types=requested_types, count=8,
                max_queries=1, include_news=False,
            ) if second_seed else []
        else:
            second_seed = _select_diverse_queries(fallback_queries, limit=2)
            second_raw = _serper_recall(second_seed, requested_types=requested_types, count=8)
        second_rows = [row for row in second_raw if str(row.get("url") or "") not in existing_urls]
        if (
            "policy" in requested_types
            and not serper_policy_fallback_ran
            and not any(r.get("type") == "policy" and (r.get("sourceUsable") or r.get("sourceVerified")) for r in results)
        ):
            try:
                for raw in serper_search.search(f"{str(intent.get('policyQuery') or query)} site:gov.cn", kind="web", count=8, gl="cn", hl="zh-cn"):
                    row = _classify_discovery_row(raw, requested_types)
                    if str(row.get("url") or "") not in existing_urls:
                        second_rows.append(row)
            except Exception as exc:
                warnings.append(f"Serper 政策二次补充暂不可用：{str(exc)[:120]}")
        gated_second = []
        for row in second_rows:
            row["queryMatchScore"] = _query_match_score(query, row, intent=intent)
            if int(row.get("queryMatchScore") or 0) >= match_threshold:
                gated_second.append(row)
        if gated_second:
            verified_second, verify_warnings = verify_results(gated_second, limit=min(12, max_results))
            warnings.extend(verify_warnings)
            before = len(results)
            results.extend(verified_second)
            results = _dedupe(results)
            added = len(results) - before
            if added > 0:
                warnings.append(f"来源核验后候选仍偏少，已通过 Google/Serper 再补充 {added} 条结果。")

    for kind in requested_types:
        if not any(r.get("type") == kind and r.get("sourceUsable") for r in results):
            if kind == "paper" and not tavily.available():
                warnings.append("论文检索需要 TAVILY_API_KEY。")
            elif kind in {"news", "policy"} and not tavily.available():
                warnings.append(f"{kind} 检索需要 TAVILY_API_KEY。")
            else:
                warnings.append(f"本次没有找到主题匹配且可定位原文的{ {'news':'新闻','policy':'政策','paper':'论文'}[kind] }。")

    results = [r for r in _dedupe(results) if r.get("sourceUsable") or r.get("sourceVerified")]
    results = _filter_results_by_date(results, time_range=time_range, date_from=date_from, date_to=date_to)
    for row in results:
        verified_desc = str(row.get("verifiedDescription") or "").strip()
        original_snippet = str(row.get("snippet") or "").strip()
        row["snippet"] = _clean_result_snippet(verified_desc or original_snippet, row)
    for row in results:
        row["queryMatchScore"] = _query_match_score(query, row, intent=intent)
        provider_rel = int(float(row.get("relevance") or 0) * 100)
        authority = int(row.get("authorityScore") or 0)
        fresh = int(row.get("freshnessScore") or 0)
        region_bonus = _region_bonus(row, str(intent.get("regionPreference") or region_preference))
        row["regionBonus"] = region_bonus
        # Relevance is dominant; region priority, authority and freshness refine it.
        type_bonus = 2 if row.get("type") in requested_types else 0
        row["score"] = max(1, min(100, round(row["queryMatchScore"] * 0.66 + provider_rel * 0.09 + authority * 0.11 + fresh * 0.07 + region_bonus + type_bonus)))
        row["matchReason"] = _match_reason(row, query, intent)
    results.sort(key=lambda x: (int(x.get("queryMatchScore") or 0), int(x.get("score") or 0), int(x.get("freshnessScore") or 0), int(x.get("authorityScore") or 0)), reverse=True)
    results = _apply_region_quota(results, max_results, str(intent.get("regionPreference") or region_preference))
    images = _dedupe_images(images)[:18]

    for row in results:
        citations = row.get("citations")
        reads = row.get("readCount")
        row["impactCount"] = int(citations or reads or 0)
        row["impactKind"] = "citations" if citations is not None else ("reads" if reads is not None else "unavailable")

    output = {
        "query": query,
        "description": description,
        "understanding": {k: intent.get(k) for k in ("intentSummary","normalizedTopic","mustTerms","conceptGroups","anchorTerms","relatedTerms","topicFamilyTerms","descriptionTerms","excludeTerms","sourcePreference","regionPreference","timeIntent","usedModel")},
        "answer": "",
        "results": results,
        "images": images,
        "warnings": list(dict.fromkeys(warnings)),
        "demo": False,
        "meta": {
            "count": len(results),
            "news": sum(1 for r in results if r.get("type") == "news"),
            "papers": sum(1 for r in results if r.get("type") == "paper"),
            "policies": sum(1 for r in results if r.get("type") == "policy"),
            "tavilyConfigured": tavily.available(),
            "deepseekConfigured": deepseek.available(),
            "verifiedCount": sum(1 for r in results if r.get("sourceVerified")),
            "indexedCount": sum(1 for r in results if r.get("sourceStatus") == "indexed"),
            "domesticCount": sum(1 for r in results if _is_domestic_result(r)),
            "globalCount": sum(1 for r in results if not _is_domestic_result(r)),
            "partial": bool(warnings),
            "cacheHit": False,
            "searchMode": search_mode,
            "understandingSource": "deepseek" if intent.get("usedModel") else "local",
            "providerMs": provider_ms,
            "regionPreference": str(intent.get("regionPreference") or region_preference),
            "elapsedMs": int((time.perf_counter() - started) * 1000),
        },
    }
    _RESEARCH_CACHE.put(cache_key, output)
    return output

def _query_tokens(text: str) -> set[str]:
    text = str(text or "").lower()
    tokens = set(re.findall(r"[a-z0-9][a-z0-9_-]{2,}|[\u4e00-\u9fff]{2,8}", text))
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for size in (2, 3):
            for i in range(max(0, len(chunk) - size + 1)):
                tokens.add(chunk[i:i+size])
    return tokens


def _query_terms(text: str) -> list[str]:
    text = str(text or "").lower()
    raw = re.findall(r"[a-z][a-z0-9_-]{2,}|[\u4e00-\u9fff]{2,10}", text)
    out=[]
    for x in raw:
        x=x.strip()
        if x and x not in out:
            out.append(x)
    return out[:50]


def _is_domestic_result(row: dict[str, Any]) -> bool:
    region = str(row.get("originRegion") or "").lower()
    if region == "domestic":
        return True
    if region == "global":
        return False
    url = str(row.get("url") or "").lower()
    source = str(row.get("source") or "").lower()
    domestic_markers = (
        ".cn", "gov.cn", "xinhuanet", "people.com", "news.cn", "cctv", "thepaper",
        "caixin", "yicai", "stcn", "cls.cn", "cnstock", "21jingji", "cnki", "wanfang",
        "cqvip", "cssn", "中国", "国家数据局", "新华社", "人民网", "央视",
    )
    return any(marker in url or marker in source for marker in domestic_markers)


def _region_bonus(row: dict[str, Any], preference: str) -> int:
    domestic = _is_domestic_result(row)
    if preference == "domestic-only":
        return 10 if domestic else -40
    if preference == "domestic-first":
        return 9 if domestic else 0
    if preference == "domestic+global":
        return 5 if domestic else 2
    if preference == "global-first":
        return 9 if not domestic else 1
    if preference == "global-only":
        return 10 if not domestic else -40
    return 0


def _apply_region_quota(rows: list[dict[str, Any]], limit: int, preference: str) -> list[dict[str, Any]]:
    """Apply a transparent region mix after relevance scoring.

    Missing candidates never create empty slots: the opposite pool fills any
    remainder. The default guarantees that domestic material is primary while
    retaining a small international comparison set when available.
    """
    if preference == "domestic-only":
        return [row for row in rows if _is_domestic_result(row)][:limit]
    if preference == "global-only":
        return [row for row in rows if not _is_domestic_result(row)][:limit]
    domestic = [row for row in rows if _is_domestic_result(row)]
    global_rows = [row for row in rows if not _is_domestic_result(row)]
    if preference == "global-first":
        domestic_target = max(1, limit // 4)
    elif preference == "domestic+global":
        domestic_target = (limit * 3 + 4) // 5
    else:
        domestic_target = (limit * 3 + 3) // 4
    global_target = max(0, limit - domestic_target)
    selected = domestic[:domestic_target] + global_rows[:global_target]
    selected_ids = {str(row.get("id") or row.get("url") or row.get("title")) for row in selected}
    for row in rows:
        if len(selected) >= limit:
            break
        key = str(row.get("id") or row.get("url") or row.get("title"))
        if key and key not in selected_ids:
            selected.append(row)
            selected_ids.add(key)
    selected.sort(key=lambda x: (int(x.get("queryMatchScore") or 0), int(x.get("score") or 0), int(x.get("freshnessScore") or 0)), reverse=True)
    return selected[:limit]


def _concept_group_hit(group: list[str], haystack: str) -> bool:
    """Match a semantic OR-group while keeping bare“数据”from matching hardware pages."""
    text = str(haystack or "").lower()
    for term in group:
        t = str(term or "").lower()
        if not t or t not in text:
            continue
        if t != "数据":
            return True
        # Bare 数据 is meaningful when it participates in a data-use relation,
        # but not merely because the page says 数据中心 / 数据线 / 数据盘.
        data_markers = (
            "数据要素", "数据驱动", "数据赋能", "数据分析", "数据治理", "数据利用",
            "数据共享", "数据流通", "数据集", "数据库", "实验数据", "研发数据",
            "数据模型", "数据资源", "数据资产", "公共数据", "训练数据",
        )
        relation_markers = ("赋能", "驱动", "促进", "支持", "助力", "提升", "优化", "决策", "研发", "创新", "模型", "算法", "实验")
        if any(marker in text for marker in data_markers):
            return True
        if "数据" in text and any(marker in text for marker in relation_markers):
            return True
    return False


def _query_match_score(query: str, row: dict[str, Any], *, intent: dict[str, Any] | None = None) -> int:
    """Score user-intent match without requiring an umbrella phrase verbatim.

    A site about data-element governance must recognize that a headline about
    “数据产权登记”“数据要素×大赛” or “AI 数据与安全” can be strongly relevant
    even when the exact phrase “数据要素治理” never appears. Exact matches are
    still valuable, but topic-family matches now provide an independent path.
    """
    intent = intent or {}
    title = str(row.get("verifiedTitle") or row.get("title") or "")
    snippet = str(row.get("verifiedDescription") or row.get("snippet") or "")
    source = str(row.get("source") or "")
    text = f"{title} {snippet} {source}".lower()
    q = str(query or "").strip().lower()
    if not q or not text:
        return 0

    must = [str(x).strip().lower() for x in intent.get("mustTerms") or [] if str(x).strip()]
    anchors = [str(x).strip().lower() for x in intent.get("anchorTerms") or [] if str(x).strip()]
    related = [str(x).strip().lower() for x in intent.get("relatedTerms") or [] if str(x).strip()]
    concept_groups = [
        [str(x).strip().lower() for x in group if str(x).strip()]
        for group in (intent.get("conceptGroups") or []) if isinstance(group, list)
    ]
    family = [str(x).strip().lower() for x in intent.get("topicFamilyTerms") or [] if str(x).strip()]
    desc_terms = [str(x).strip().lower() for x in intent.get("descriptionTerms") or [] if str(x).strip()]
    excludes = [str(x).strip().lower() for x in intent.get("excludeTerms") or [] if str(x).strip()]
    source_pref = [str(x).strip().lower() for x in intent.get("sourcePreference") or [] if str(x).strip()]

    score = 0
    title_l = title.lower()
    generic_query = q in {"数据", "治理", "政策", "新闻", "论文", "市场", "价值"} or len(q) <= 2

    # Exact phrase is a strong signal for specific topics, but generic one-word
    # queries do not earn points just because every page contains “数据”.
    if not generic_query:
        if q in title_l:
            score += 40
        elif q in text:
            score += 20

    group_title_hits = sum(1 for group in concept_groups if _concept_group_hit(group, title_l))
    group_text_hits = sum(1 for group in concept_groups if _concept_group_hit(group, text))
    relation_incomplete = False
    if concept_groups:
        score += min(38, group_title_hits * 15 + max(0, group_text_hits - group_title_hits) * 7)
        # Relationship-style queries must cover both sides somewhere in title or
        # snippet. This blocks false positives such as“数据中心液冷技术突破”while
        # still accepting“数据要素赋能关键核心技术突破”.
        if intent.get("isConceptualQuery") and len(concept_groups) >= 2 and group_text_hits < 2 and q not in text:
            relation_incomplete = True
        if group_text_hits == 0 and intent.get("isConceptualQuery"):
            return 0

    must_title = sum(1 for t in must if t and t in title_l)
    must_text = sum(1 for t in must if t and t in text)
    anchor_title = sum(1 for t in anchors if t and t in title_l)
    anchor_text = sum(1 for t in anchors if t and t in text)
    family_title = sum(1 for t in family if t and t in title_l)
    family_text = sum(1 for t in family if t and t in text)
    desc_text = sum(1 for t in desc_terms if t and t in text)
    related_text = sum(1 for t in related if t and t in text)

    # The original user phrase still matters, but it is no longer an implicit
    # hard AND-condition. Concrete family terms carry enough weight to survive.
    score += min(24, must_title * 18 + max(0, must_text - must_title) * 8)
    score += min(32, anchor_title * 11 + max(0, anchor_text - anchor_title) * 5)
    score += min(34, family_title * 16 + max(0, family_text - family_title) * 7)
    score += min(16, desc_text * 5)
    score += min(10, related_text * 2)
    if any(t in title_l or t in source.lower() for t in source_pref):
        score += 3
    for term in excludes:
        if term and term in text:
            score -= 60

    # Generic “数据” searches need at least one governance/data-element anchor.
    if generic_query:
        domain_anchors = tuple(x.lower() for x in (
            "数据要素", "数据治理", "数据资产", "数据流通", "数据交易", "公共数据",
            "可信数据空间", "数据授权运营", "数据基础制度", "数据产权", "数据确权",
            "数据安全", "数据合规", "数据跨境", "AI数据", "训练数据", "语料",
            "data governance", "data asset", "data space"
        ))
        if not any(a in text for a in domain_anchors):
            return 0
        score += 10

    q_tokens = _query_tokens(q)
    text_tokens = _query_tokens(text)
    overlap = len(q_tokens & text_tokens)
    score += min(10, overlap)

    if row.get("type") == "paper":
        paper_markers = ("doi", "journal", "abstract", "paper", "study", "research", "proceedings", "arxiv", "springer", "nature", "期刊", "论文", "研究", "学报", "课题")
        if not any(m in text for m in paper_markers):
            score -= 18

    if relation_incomplete:
        return min(8, max(0, int(round(score))))
    return max(0, min(100, int(round(score))))


def _match_reason(row: dict[str, Any], query: str, intent: dict[str, Any]) -> str:
    title = str(row.get("verifiedTitle") or row.get("title") or "")
    anchors = [str(x) for x in intent.get("anchorTerms") or [] if str(x).strip()]
    matched = [x for x in anchors if x.lower() in title.lower()]
    prefix = "国内权威来源；" if _is_domestic_result(row) and int(row.get("authorityScore") or 0) >= 70 else ("国内来源；" if _is_domestic_result(row) else "国际补充；")
    if matched:
        return f"{prefix}标题直接命中“{matched[0]}”"
    if str(query).lower() in title.lower():
        return f"{prefix}标题直接匹配用户关键词"
    return f"{prefix}摘要与用户主题存在明确关联"


def _clean_result_snippet(text: str, row: dict[str, Any]) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    lower = text.lower()
    bad = ("验证码", "搜索过于频繁", "captcha", "access denied", "rate limit", "please verify", "请输入验证码")
    if any(x in lower for x in bad):
        return str(row.get("verifiedTitle") or row.get("title") or "打开原文查看完整内容")
    if not text:
        return str(row.get("verifiedTitle") or row.get("title") or "打开原文查看完整内容")
    return text[:520]


def _parse_result_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _filter_results_by_date(results: list[dict[str, Any]], *, time_range: str, date_from: str = "", date_to: str = "") -> list[dict[str, Any]]:
    if time_range in {"latest", "all"}:
        return results
    today = date.today()
    lower: date | None = None
    upper: date | None = today
    if time_range == "day":
        lower = today - timedelta(days=1)
    elif time_range == "week":
        lower = today - timedelta(days=7)
    elif time_range == "month":
        lower = today - timedelta(days=30)
    elif time_range == "quarter":
        lower = today - timedelta(days=90)
    elif time_range == "year":
        lower = today - timedelta(days=365)
    elif time_range == "custom":
        lower = _parse_result_date(date_from)
        upper = _parse_result_date(date_to) or today
        if lower is None and date_from:
            return results
    else:
        return results

    filtered: list[dict[str, Any]] = []
    for row in results:
        published = _parse_result_date(row.get("publishedAt"))
        if published is None:
            # Tavily/Serper have already received the upstream date constraint.
            # Some Chinese government pages and general-search results do not
            # expose a parsable publication timestamp. Dropping every undated
            # result here was a major cause of “7 天内 = 0 条”. Retain it and
            # mark the date as upstream-filtered/unknown instead.
            row = dict(row)
            row["dateUnverified"] = True
            filtered.append(row)
            continue
        if lower and published < lower:
            continue
        if upper and published > upper:
            continue
        filtered.append(row)
    return filtered


def generate_article(payload: dict[str, Any]) -> dict[str, Any]:
    """Generate a publication-ready article with explicit API/length guarantees.

    Unlike earlier demo-oriented versions, this function never silently replaces a
    failed DeepSeek call with a local template. If the user asked for AI writing,
    either DeepSeek was actually called or the request fails with an actionable
    error. This makes every writing control meaningful and auditable in the UI.
    """
    query = str(payload.get("query") or "数据要素").strip()[:180]
    description = str(payload.get("description") or payload.get("searchDescription") or "").strip()[:1000]
    sources = [dict(x) for x in list(payload.get("sources") or [])[:16] if isinstance(x, dict)]
    options = payload.get("options") or {}
    style = str(options.get("style") or "行业观察").strip()[:80]
    audience = str(options.get("audience") or "产业与政策关注者").strip()[:80]
    length = str(options.get("length") or "中篇 · 1800—2400字").strip()[:80]
    length_spec = _length_spec(length)
    factual = bool(options.get("factCheck", True))
    citations = bool(options.get("citations", False))
    auto_evidence = bool(options.get("autoEvidence", True))
    angle = str(options.get("angle") or "").strip()[:300]
    if not description and angle:
        description = angle[:1000]
    tone = str(options.get("tone") or "理性、清晰").strip()[:80]
    title_mode = str(options.get("titleMode") or "默认 · 自然起题").strip()[:80]
    structure = str(options.get("structure") or "默认 · 按内容自然组织").strip()[:120]
    closing_mode = str(options.get("closingMode") or "默认 · 自然收束").strip()[:80]
    raw_image_count = options.get("bodyImageCount", options.get("imageCount", 3))
    try:
        image_count = max(0, min(int(raw_image_count), 8))
    except (TypeError, ValueError):
        image_count = 3
    image_preference = str(options.get("imagePreference") or "混合").strip()[:100]
    image_strategy = str(options.get("imageStrategy") or "smart").strip().lower()[:30]
    if image_strategy not in {"smart", "real_first", "diagram_first", "all_diagram", "real_only"}:
        image_strategy = "smart"
    image_match_mode = str(options.get("imageMatchMode") or "precise").strip()[:30]
    image_source_policy = str(options.get("imageSourcePolicy") or "balanced").strip()[:40]
    quality_mode = str(options.get("qualityMode") or "auto").strip()[:30]
    opener = str(options.get("opener") or "默认 · 选择最自然切口").strip()[:80]
    paragraph_rhythm = str(options.get("paragraphRhythm") or "默认 · 随内容调整").strip()[:80]
    evidence_style = str(options.get("evidenceStyle") or "默认 · 自然融入证据").strip()[:80]
    ai_cliche_guard = bool(options.get("aiClicheGuard", True))
    smart_sections = bool(options.get("smartSections", True))

    if not deepseek.available():
        raise RuntimeError(
            "DeepSeek API 未配置：请在 .env 中填写 DEEPSEEK_API_KEY 并重启服务。"
            "本版本不会再用本地模板冒充 AI 生成。"
        )

    # Understand the user's actual writing request once. For a detailed brief this
    # uses a tiny non-thinking planner; its result is cached and then embedded in the
    # main writing prompt. When evidence must also be searched, the two operations run
    # in parallel so semantic understanding does not add wall-clock delay.
    brief_plan = local_brief(angle, query)
    auto_evidence_added = 0
    if angle:
        if not sources and auto_evidence:
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="prewrite") as prep:
                f_brief = prep.submit(understand_writing_brief, angle, query)
                f_research = prep.submit(_research_for_article_evidence, query, description)
                brief_plan = f_brief.result()
                pack = f_research.result()
        else:
            brief_plan = understand_writing_brief(angle, query)
    elif not sources and auto_evidence:
        pack = _research_for_article_evidence(query, description)
    else:
        pack = {"results": []}

    if not sources and auto_evidence:
        candidates = [x for x in pack.get("results") or [] if x.get("sourceVerified") or x.get("sourceUsable")]
        candidates.sort(key=lambda x: (int(x.get("queryMatchScore") or 0), int(x.get("score") or 0), int(x.get("freshnessScore") or 0)), reverse=True)
        sources = _balanced_sources(candidates, limit=8)
        for source in sources:
            source["autoEvidenceSelected"] = True
            source.setdefault("origin", "auto")
        auto_evidence_added = len(sources)

    # Automatic evidence may use a high-confidence search-index result when the origin
    # page blocks automated verification. It is hydrated before writing whenever
    # possible; titles/URLs/snippets remain traceable and no synthetic evidence is
    # introduced.
    sources = [
        s for s in sources
        if s.get("sourceVerified")
        or s.get("type") == "upload"
        or ((s.get("selectedByUser") or s.get("autoEvidenceSelected")) and s.get("sourceUsable") and s.get("sourceStatus") == "indexed")
    ]
    evidence_hydrated = 0
    if sources and tavily.available():
        sources, evidence_hydrated = _hydrate_sources_for_writing(query, sources)

    # Keep writing and visual concerns separate.  The writing specification is
    # reused by editorial/length-repair prompts, so image-routing settings must
    # not leak into those LLM calls.  Visuals are planned only after the article
    # exists and are stored under a separate visualSpec for audit/export UI.
    writing_spec = {
        "style": style,
        "audience": audience,
        "lengthLabel": length,
        "targetChars": length_spec["target"],
        "minChars": length_spec["min"],
        "maxChars": length_spec["max"],
        "tone": tone,
        "titleMode": title_mode,
        "structure": structure,
        "opener": opener,
        "paragraphRhythm": paragraph_rhythm,
        "evidenceStyle": evidence_style,
        "closingMode": closing_mode,
        "angle": angle,
        "understoodBriefPlan": brief_plan,
        "aiClicheGuard": ai_cliche_guard,
        "citations": citations and bool(sources),
        "factCheck": factual,
        "autoEvidence": auto_evidence,
        "smartSections": smart_sections,
    }
    visual_spec = {
        "coverImage": True,
        "bodyImageCount": image_count,
        "imageStrategy": image_strategy,
        "imagePreference": image_preference,
        "imageMatchMode": image_match_mode,
        "imageSourcePolicy": image_source_policy,
    }
    calls: list[dict[str, Any]] = []

    article, meta = _llm_article(
        query, sources, style, audience, length, factual, citations and bool(sources),
        angle=angle, tone=tone, title_mode=title_mode, structure=structure,
        closing_mode=closing_mode, image_count=image_count, image_preference=image_preference,
        opener=opener, paragraph_rhythm=paragraph_rhythm, evidence_style=evidence_style,
        ai_cliche_guard=ai_cliche_guard, length_spec=length_spec, smart_sections=smart_sections,
        brief_plan=brief_plan,
        reasoning_effort="high" if quality_mode == "deep" else "off",
    )
    calls.append({"stage": "draft", **meta})
    article["demo"] = False
    if angle:
        article.setdefault("understoodBrief", str(brief_plan.get("objective") or angle[:600]))
    article["understoodBriefPlan"] = brief_plan
    article["titleCandidates"] = _rank_wechat_titles(article.get("titleCandidates") or [], query)
    article["recommendedTitle"] = (article.get("titleCandidates") or [str(article.get("recommendedTitle") or query)])[0]
    article["titleCandidates"] = _rank_wechat_titles([article["recommendedTitle"], *(article.get("titleCandidates") or [])], query)

    if quality_mode == "deep" or _needs_editorial_repair(article, length_spec=length_spec, smart_sections=smart_sections, ai_cliche_guard=ai_cliche_guard, structure=structure, require_brief=bool(angle)):
        article, meta = _llm_polish_article(
            article, query, sources, style=style, audience=audience, tone=tone,
            paragraph_rhythm=paragraph_rhythm, evidence_style=evidence_style,
            ai_cliche_guard=ai_cliche_guard, length_spec=length_spec,
            title_mode=title_mode, structure=structure, opener=opener,
            closing_mode=closing_mode, angle=angle, smart_sections=smart_sections,
            reasoning_effort="high" if quality_mode == "deep" else "off",
        )
        calls.append({"stage": "editorial", **meta})
        article["qualityMode"] = "targeted-editorial-pass" if quality_mode != "deep" else "deep-editorial-pass"
    else:
        article["qualityMode"] = "single-pass"

    # Enforce the selected length after the editorial pass. A second repair is
    # allowed only when the first repair remains outside the requested range.
    for repair_index in range(1):
        actual = _article_char_count(str(article.get("markdown") or ""))
        if length_spec["min"] <= actual <= length_spec["max"]:
            break
        article, meta = _llm_length_repair(
            article, query, sources, writing_spec=writing_spec, length_spec=length_spec
        )
        calls.append({"stage": f"length-repair-{repair_index + 1}", **meta})

    article["titleCandidates"] = _rank_wechat_titles(article.get("titleCandidates") or [], query)
    article["recommendedTitle"] = (article.get("titleCandidates") or [query])[0]
    article["markdown"] = _naturalize_default_structure(str(article.get("markdown") or ""), structure)

    actual_chars = _article_char_count(str(article.get("markdown") or ""))
    within_target = length_spec["min"] <= actual_chars <= length_spec["max"]
    if not within_target:
        article.setdefault("warnings", []).append(
            f"正文当前约 {actual_chars} 字，未完全进入所选 {length_spec['min']}—{length_spec['max']} 字区间；"
            "已执行长度校正，建议在‘再次修改’中继续指定压缩/扩写。"
        )

    if factual and sources:
        _enforce_evidence_binding(article, len(sources))
    article["markdown"] = _strip_invalid_citations(str(article.get("markdown") or ""), len(sources) if citations else 0)

    _attach_sources(article, sources)
    if auto_evidence and not sources:
        article.setdefault("warnings", []).append("已开启自动补充资料，但本次没有检索到通过相关性与来源校验的可用材料；正文未把无来源事实当作真实案例写入。")
    visual_token = uuid4().hex
    article["visualJobToken"] = visual_token
    article["visualStatus"] = "pending"
    article["visualReport"] = {"planned": image_count + 1, "coverPlanned": 1, "placed": 0, "coverPlaced": 0, "bodyPlanned": image_count, "bodyPlaced": 0, "provider": "pending", "fallback": 0, "strategy": image_strategy}
    article["visuals"] = []
    article["coverImage"] = None
    article["images"] = []
    article["blocks"] = merge_visuals_into_blocks(str(article.get("markdown") or ""), [])
    article["model"] = settings_model_name()
    article["evidenceHydrated"] = evidence_hydrated
    article["generationMeta"] = _generation_meta(
        calls,
        writing_spec=writing_spec,
        visual_spec=visual_spec,
        actual_chars=actual_chars,
        within_target=within_target,
        source_count=len(sources),
        auto_evidence_added=auto_evidence_added,
    )

    article_id = article_store.put(article, sources=sources, query=query)
    article["articleId"] = article_id
    article["historyDepth"] = 0
    # Persist the id/token before background enrichment so the first UI render can be immediate.
    article_store.update(article_id, article, save_history=False)
    _start_visual_job(article_id, query=query, image_count=image_count, image_preference=image_preference, image_strategy=image_strategy, image_match_mode=image_match_mode, image_source_policy=image_source_policy, visual_token=visual_token)
    return article


def _start_visual_job(article_id: str, *, query: str, image_count: int, image_preference: str, image_strategy: str = "smart", image_match_mode: str, image_source_policy: str, visual_token: str) -> None:
    def job() -> None:
        try:
            record = article_store.get(article_id)
            if not record:
                return
            current = record.get("article") or {}
            if current.get("visualJobToken") != visual_token:
                return
            _apply_visual_layout(current, query, image_count=image_count, image_preference=image_preference, image_strategy=image_strategy, image_match_mode=image_match_mode, image_source_policy=image_source_policy)
            current["visualStatus"] = "ready"
            article_store.update(article_id, current, save_history=False)
        except Exception as exc:
            record = article_store.get(article_id)
            if not record:
                return
            current = record.get("article") or {}
            if current.get("visualJobToken") != visual_token:
                return
            current["visualStatus"] = "error"
            current.setdefault("warnings", []).append(f"自动配图失败：{str(exc)[:180]}")
            current["visualReport"] = {**(current.get("visualReport") or {}), "provider": "error"}
            article_store.update(article_id, current, save_history=False)
    # Give the article response / generation-job result a short head start before
    # CPU-heavy PNG rendering and network image probing begin.  Starting the
    # visual worker immediately could contend with JSON serialization on small
    # deployments and made the newly added image system appear to break writing.
    timer = threading.Timer(0.8, lambda: _VISUAL_POOL.submit(job))
    timer.daemon = True
    timer.start()


def _needs_editorial_repair(article: dict[str, Any], *, length_spec: dict[str, int], smart_sections: bool, ai_cliche_guard: bool, structure: str = "", require_brief: bool = False) -> bool:
    """Cheap local quality gate. Only spend a second DeepSeek call when the draft is visibly deficient."""
    md = str(article.get("markdown") or "")
    if not md.strip():
        return True
    count = _article_char_count(md)
    if count < int(length_spec["min"] * 0.88) or count > int(length_spec["max"] * 1.10):
        return True
    if require_brief and article.get("understoodBrief") is None:
        return True
    contamination = ["titleCandidates", "recommendedTitle", "editorialNotes", "sourceNotes", "imageSlots", "generationMeta", "json {", "```json", "用户写作规格", "写作切口", "promptTokens", "completionTokens", "reasoningTokens"]
    if any(token.lower() in md[:700].lower() for token in contamination):
        return True
    candidates = [str(x or "").strip() for x in (article.get("titleCandidates") or [])]
    recommended = str(article.get("recommendedTitle") or "").strip()
    if not recommended or not (12 <= len(recommended) <= 36) or re.match(r"^(?:数据要素治理|数据要素)\s*[:：—|-]", recommended):
        return True
    generic_title_formulas = ("一文读懂", "深度解析", "全景观察", "全面解读", "全解析", "新逻辑", "新范式", "正在重塑")
    if any(phrase in recommended for phrase in generic_title_formulas):
        return True
    if len([x for x in candidates if 12 <= len(x) <= 36]) < 2:
        return True
    headings = re.findall(r"^##\s+(.+)$", md, flags=re.M)
    if smart_sections and len(headings) > 5:
        return True
    # “默认”不是一个四段式模板。普通公众号文章默认 0—3 个小标题就够了；
    # 如果模型仍然整齐地切成 4 个以上段落，送入一次编辑修复，让它合并能靠
    # 过渡句自然连接的部分。用户主动选择了明确结构时不套这个限制。
    if structure.startswith("默认") and len(headings) > 3:
        return True
    outline_prefixes = ("问题", "做法", "机制", "原因", "影响", "条件", "判断", "趋势", "建议", "结论", "路径", "价值", "风险")
    templated = [h for h in headings if re.match(r"^(?:" + "|".join(outline_prefixes) + r")\s*[：:]", h.strip())]
    if len(headings) >= 4 and len(templated) / max(1, len(headings)) >= 0.6:
        return True
    if len({re.split(r"[：:]", h, maxsplit=1)[0].strip() for h in templated}) >= 4:
        return True
    # Detect obvious template contamination without trying to judge prose with regex alone.
    clichés = [
        "随着数字化浪潮", "在当今时代", "值得注意的是", "首先，", "其次，", "此外，",
        "综上所述", "本文将从", "这意味着", "更重要的是", "换句话说", "从这个角度看",
    ]
    if ai_cliche_guard and sum(md.count(x) for x in clichés) >= 4:
        return True
    # Guard against very short paragraph spam or one giant wall of text.
    paras = [x.strip() for x in re.split(r"\n\s*\n", md) if x.strip() and not x.lstrip().startswith("#")]
    if len(paras) >= 6:
        short_ratio = sum(1 for x in paras if len(re.sub(r"\s+", "", x)) < 45) / len(paras)
        if short_ratio > 0.55:
            return True
    return False


def _clean_title(raw: Any, query: str) -> str:
    title = " ".join(str(raw or "").replace("“", "").replace("”", "").split()).strip("。 ")
    if not title:
        return ""
    # The site topic is an editorial scope, not a mandatory title prefix.
    # Strip only the generic umbrella prefix; specific policy/event names are kept.
    if query.strip() in {"数据要素治理", "数据要素"}:
        title = re.sub(r"^(?:数据要素治理|数据要素)\s*(?:[:：|｜]|—{1,2}|-)+\s*", "", title).strip()
    title = re.sub(r"^围绕主题展开的研究与判断\s*", "", title).strip()
    title = re.sub(r"^(?:数治周报|数据要素治理周报|本周观察)\s*(?:[:：|｜]|—{1,2}|-)+\s*", "", title).strip()
    return title[:70]


def _rank_wechat_titles(titles: list[Any], query: str) -> list[str]:
    cleaned: list[str] = []
    for raw in titles:
        title = _clean_title(raw, query)
        if not title or title in cleaned or len(title) < 10 or len(title) > 38:
            continue
        cleaned.append(title)
    if not cleaned:
        topic = query.strip() or "这个议题"
        return [topic[:38]]

    umbrella = query.strip() in {"数据要素治理", "数据要素"}

    def score(title: str) -> float:
        value = 0.0
        # Specificity and reading motivation matter more than repeating the site theme.
        if not umbrella and query and any(k for k in query.split() if k and k in title):
            value += 2.0
        if any(ch in title for ch in ("？", "意味着", "为何", "为什么", "背后", "关键", "到底")):
            value += 1.4
        if any(phrase in title for phrase in ("真正值得关注", "真正变化在于", "正在发生", "背后发生了什么")):
            value -= 1.8
        if any(phrase in title for phrase in ("一文读懂", "深度解析", "全景观察", "新逻辑", "新范式", "正在重塑", "全面解读", "全解析")):
            value -= 2.6
        if re.match(r"^(?:从.{1,12}到.{1,12}|为什么说|如何看待)", title):
            value -= 0.8
        if 15 <= len(title) <= 30:
            value += 2.0
        if any(w in title for w in ("政策", "企业", "平台", "数据", "AI", "产权", "公共", "交易", "安全")):
            value += 1.0
        if re.match(r"^(?:数据要素治理|数据要素)\s*[:：—|-]", title):
            value -= 6.0
        if any(w in title for w in ("重大", "颠覆", "惊天", "必看", "震撼", "疯了", "暴涨")):
            value -= 5.0
        if title.count("！") > 1:
            value -= 3.0
        return value

    return sorted(cleaned, key=score, reverse=True)[:5]



def _research_for_article_evidence(query: str, description: str = "") -> dict[str, Any]:
    """Collect evidence for writing with relevance-first recency behavior.

    Auto-evidence is different from the interactive news page: when a user is
    writing an evergreen explainer/case article, useful papers and policy pages
    may be older than the latest-news window. We therefore use the user's writing
    brief as retrieval context and broaden once only when the first pass has too
    few usable sources.
    """
    context = f"{query} {description}"
    wants_recent = bool(re.search(r"最新|近期|最近|本周|本月|今年|近\s*\d+", context))
    primary_range = "latest" if wants_recent else "all"
    base_payload = {
        "query": query, "description": description, "types": ["news", "policy", "paper"],
        "timeRange": primary_range, "maxResults": 20, "searchMode": "fast",
        "regionPreference": "domestic-first", "surface": "home",
    }
    first = research(base_payload)
    rows = list(first.get("results") or [])
    usable = [x for x in rows if x.get("sourceVerified") or x.get("sourceUsable")]
    if len(usable) >= 4:
        return first

    plan = local_plan(query, description, "domestic-first")
    fallback_query = str(plan.get("generalDiscoveryQuery") or "").strip() or str((plan.get("webQueryVariants") or [""])[0]).strip() or query
    second = research({**base_payload, "query": fallback_query, "timeRange": "all", "maxResults": 24})
    merged = _dedupe([*rows, *(second.get("results") or [])])
    out = dict(first)
    out["results"] = merged
    out["warnings"] = list(dict.fromkeys([*(first.get("warnings") or []), *(second.get("warnings") or [])]))
    out.setdefault("meta", {})["autoEvidenceBroadened"] = True
    return out

def _balanced_sources(rows: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda x: (int(x.get("score") or 0), int(x.get("queryMatchScore") or 0), int(x.get("freshnessScore") or 0)), reverse=True)
    if not ordered:
        return []
    top_score = int(ordered[0].get("score") or 0)
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Prefer diversity only among sources that are still close to the best relevance/value score.
    threshold = max(55, top_score - 14)
    for kind in ("policy", "news", "paper"):
        hit = next((x for x in ordered if x.get("type") == kind and int(x.get("score") or 0) >= threshold), None)
        if hit:
            key = str(hit.get("id") or hit.get("url") or hit.get("title"))
            if key and key not in seen:
                output.append(dict(hit)); seen.add(key)
    for row in ordered:
        if len(output) >= limit:
            break
        key = str(row.get("id") or row.get("url") or row.get("title"))
        if not key or key in seen:
            continue
        output.append(dict(row)); seen.add(key)
    return output[:limit]


def _hydrate_sources_for_writing(query: str, sources: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    copied = [dict(src) for src in sources[:6]]
    urls: list[str] = []
    for src in copied:
        if src.get("type") == "upload":
            continue
        url = str(src.get("url") or "")
        # Even when search already supplied enough text for writing, extract the
        # selected origin page once so the visual stage can reuse its real images.
        if url.startswith(("http://", "https://")):
            urls.append(url)
    details = tavily.extract_url_details(urls[:6], query=query, chunks_per_source=1, include_images=True) if urls else {}
    hydrated = 0
    for src in copied:
        url = str(src.get("url") or "")
        detail = details.get(url) or {}
        content = str(detail.get("content") or "")
        if content:
            src["rawContent"] = content
            src["contentOrigin"] = "tavily-extract"
            hydrated += 1
        elif not src.get("rawContent"):
            src["rawContent"] = str(src.get("snippet") or "")
        origin_images = [str(x) for x in (detail.get("images") or []) if str(x).startswith(("http://", "https://"))]
        if origin_images:
            src["sourceImages"] = origin_images[:12]
    return copied, hydrated

def revise_article(payload: dict[str, Any]) -> dict[str, Any]:
    article_id = str(payload.get("articleId") or "").strip()
    instruction = str(payload.get("instruction") or "").strip()[:2000]
    scope = str(payload.get("scope") or "whole").strip()[:30]
    target_text = str(payload.get("targetText") or "").strip()[:6000]
    target_heading = str(payload.get("targetHeading") or "").strip()[:300]
    refresh_images = bool(payload.get("refreshImages", True))
    if not article_id:
        raise ValueError("articleId is required")
    if not instruction:
        raise ValueError("请填写修改要求")
    record = article_store.get(article_id)
    if not record:
        raise ValueError("文章草稿已过期，请重新生成")
    if not deepseek.available():
        raise RuntimeError("再次修改需要配置 DEEPSEEK_API_KEY")

    current = record.get("article") or {}
    sources = record.get("sources") or []
    query = str(record.get("query") or "数据要素")
    revised = _llm_revise_article(
        current,
        sources,
        query=query,
        scope=scope,
        target_text=target_text,
        target_heading=target_heading,
        instruction=instruction,
    )
    revision_meta = revised.pop("_revisionMeta", {})
    # Keep layout metadata when the editor did not intentionally change it.
    revised.setdefault("coverBrief", current.get("coverBrief"))
    revised.setdefault("imageQueries", current.get("imageQueries") or [])
    revised.setdefault("imageSlots", current.get("imageSlots") or [])
    revised.setdefault("keyClaims", current.get("keyClaims") or [])
    revised.setdefault("riskNotes", current.get("riskNotes") or [])
    revised.setdefault("sourceNotes", current.get("sourceNotes") or [])
    revised.setdefault("socialSummary", current.get("socialSummary") or revised.get("deck") or "")
    revised["demo"] = False
    revised["model"] = settings_model_name()
    revised["revisionSummary"] = str(revised.get("revisionSummary") or instruction)[:500]
    previous_meta = dict(current.get("generationMeta") or {})
    calls = list(previous_meta.get("calls") or [])
    if revision_meta:
        calls.append({"stage": "revision", **revision_meta})
    spec = dict(previous_meta.get("writingSpec") or {})
    revised["markdown"] = _strip_invalid_citations(str(revised.get("markdown") or ""), len(sources) if bool(spec.get("citations")) else 0)
    actual_chars = _article_char_count(str(revised.get("markdown") or ""))
    min_chars = int(spec.get("minChars") or 0)
    max_chars = int(spec.get("maxChars") or 0)
    within_target = (min_chars <= actual_chars <= max_chars) if min_chars and max_chars else True
    revised["generationMeta"] = _generation_meta(
        calls, writing_spec=spec, actual_chars=actual_chars, within_target=within_target,
        source_count=len(sources), auto_evidence_added=int(previous_meta.get("autoEvidenceAdded") or 0),
    )

    _attach_sources(revised, sources)
    if refresh_images:
        body_count = max(0, int(spec.get("bodyImageCount") or 3))
        revised["visualJobToken"] = uuid4().hex
        revised["visualStatus"] = "pending"
        image_strategy = str(spec.get("imageStrategy") or "smart")
        image_preference = str(spec.get("imagePreference") or "混合")
        image_match_mode = str(spec.get("imageMatchMode") or "precise")
        image_source_policy = str(spec.get("imageSourcePolicy") or "balanced")
        revised["visualReport"] = {"planned": body_count + 1, "coverPlanned": 1, "placed": 0, "coverPlaced": 0, "bodyPlanned": body_count, "bodyPlaced": 0, "provider": "pending", "fallback": 0, "strategy": image_strategy}
        revised["visuals"] = []
        revised["coverImage"] = None
        revised["images"] = []
        revised["blocks"] = merge_visuals_into_blocks(str(revised.get("markdown") or ""), [])
        article_store.update(article_id, revised, save_history=True)
        _start_visual_job(article_id, query=query, image_count=body_count, image_preference=image_preference, image_strategy=image_strategy, image_match_mode=image_match_mode, image_source_policy=image_source_policy, visual_token=revised["visualJobToken"])
    else:
        revised["visuals"] = current.get("visuals") or []
        revised["coverImage"] = current.get("coverImage")
        revised["images"] = current.get("images") or []
        revised["blocks"] = merge_visuals_into_blocks(str(revised.get("markdown") or ""), revised["visuals"])
        revised["visualReport"] = current.get("visualReport") or {}
        revised["visualStatus"] = current.get("visualStatus") or "ready"
        article_store.update(article_id, revised, save_history=True)
    revised["articleId"] = article_id
    revised["historyDepth"] = article_store.history_depth(article_id)
    return revised


def undo_revision(article_id: str) -> dict[str, Any]:
    record = article_store.undo(article_id)
    if not record:
        raise ValueError("没有可以撤回的修改")
    article = record.get("article") or {}
    article["articleId"] = article_id
    article["historyDepth"] = article_store.history_depth(article_id)
    return article


def restore_original(article_id: str) -> dict[str, Any]:
    record = article_store.restore_original(article_id)
    if not record:
        raise ValueError("文章草稿已过期，请重新生成")
    article = record.get("article") or {}
    article["articleId"] = article_id
    article["historyDepth"] = article_store.history_depth(article_id)
    return article


def _apply_visual_layout(
    article: dict[str, Any],
    query: str,
    *,
    image_count: int,
    image_preference: str,
    image_strategy: str = "smart",
    image_source_policy: str = "balanced",
    image_match_mode: str = "precise",
) -> None:
    slots = plan_visual_slots(article, query, max_body=image_count)
    # If the article cites concrete news/policy sources, give image search an
    # exact-title route as well. This lets Serper find the original report's
    # own image instead of forcing every slot through generic stock-like terms.
    source_rows = [x for x in (article.get("sourceList") or []) if x.get("url") and x.get("origin") != "upload"]
    source_by_n = {}
    for row in source_rows:
        try:
            source_by_n[int(row.get("n") or 0)] = row
        except (TypeError, ValueError):
            pass
    source_note_map = {}
    for note in (article.get("sourceNotes") or []):
        if not isinstance(note, dict):
            continue
        try:
            note_id = int(note.get("sourceId") or 0)
        except (TypeError, ValueError):
            continue
        if note_id > 0:
            source_note_map[note_id] = str(note.get("whyUsed") or "")[:500]

    def bind_source(slot: dict[str, Any], row: dict[str, Any], *, explicit: bool = False) -> None:
        slot["sourceHint"] = str(row.get("title") or "").strip()[:220]
        slot["sourceName"] = str(row.get("source") or "").strip()[:120]
        slot["sourceHintUrl"] = str(row.get("url") or "")[:1200]
        slot["sourceSnippet"] = str(row.get("snippet") or "")[:700]
        slot["sourceImages"] = [str(x) for x in (row.get("sourceImages") or []) if str(x).startswith(("http://", "https://"))][:12]
        if explicit:
            slot["sourceExplicit"] = True

    for slot in slots:
        if slot.get("kind") != "body":
            continue
        try:
            requested_source_id = int(slot.get("sourceId") or 0)
        except (TypeError, ValueError):
            requested_source_id = 0
        # Writing and visual planning are intentionally decoupled in V31.  When
        # the visible paragraph already carries a citation marker, that is a
        # stronger provenance signal than asking the LLM to emit a separate
        # image-slot sourceId.  Bind it locally before semantic fallback.
        if requested_source_id <= 0:
            cited = [int(x) for x in re.findall(r"\[(\d+)\]", str(slot.get("anchorText") or "")) if int(x) in source_by_n]
            if cited:
                requested_source_id = cited[0]
        exact_row = source_by_n.get(requested_source_id)
        if exact_row:
            bind_source(slot, exact_row, explicit=True)
            continue

        desired = " ".join(str(slot.get(k) or "") for k in ("query", "afterHeading", "anchorText"))
        desired_tokens = _query_tokens(desired)
        ranked_hints = []
        for row in source_rows:
            title = str(row.get("title") or "").strip()
            snippet = str(row.get("snippet") or "").strip()
            if not title:
                continue
            try:
                row_n = int(row.get("n") or 0)
            except (TypeError, ValueError):
                row_n = 0
            why_used = source_note_map.get(row_n, "")
            source_text = f"{title} {snippet} {why_used}"
            source_tokens = _query_tokens(source_text)
            overlap_terms = {
                token for token in (desired_tokens & source_tokens)
                if token not in {"数据", "要素", "治理", "政策", "新闻", "中国", "相关", "行业", "研究"}
                and len(token) >= 2
            }
            # Long concept overlaps are much more meaningful than generic bigrams.
            overlap_score = sum(3 if len(token) >= 4 else 1 for token in overlap_terms)
            if str(row.get("title") or "") and str(row.get("title") or "") in desired:
                overlap_score += 8
            if why_used and any(token in why_used for token in overlap_terms):
                overlap_score += 2
            ranked_hints.append((overlap_score, len(overlap_terms), -len(title), row))
        ranked_hints.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        # Do not force an unrelated cited report into every body-image search. When
        # there is a real match, pass both the origin URL and images already
        # extracted from that page. The image resolver will test those images before
        # falling back to Google Images.
        if ranked_hints and ranked_hints[0][0] >= 2:
            bind_source(slot, ranked_hints[0][3])
    visuals, visual_warnings = resolve_visuals(
        slots, query, preference=image_preference, strategy=image_strategy, match_mode=image_match_mode, source_policy=image_source_policy
    )
    article.setdefault("warnings", []).extend(visual_warnings)
    article["visuals"] = visuals
    cover_visual = next((v for v in visuals if v.get("kind") == "cover" and v.get("image")), None)
    article["coverImage"] = dict(cover_visual["image"]) if cover_visual else None
    if article.get("coverImage"):
        article["coverImage"]["caption"] = _visual_caption(cover_visual)
    article["blocks"] = merge_visuals_into_blocks(str(article.get("markdown") or ""), visuals)
    article["images"] = [dict(v["image"]) for v in visuals if v.get("image")]
    provider_counts: dict[str, int] = {}
    for visual in visuals:
        provider = str((visual.get("image") or {}).get("provider") or str(visual.get("matchedBy") or "").split("-", 1)[0] or "unknown")
        provider_counts[provider] = provider_counts.get(provider, 0) + 1
    article["visualReport"] = {
        "planned": len(slots),
        "coverPlanned": 1,
        "bodyPlanned": image_count,
        "placed": sum(1 for v in visuals if v.get("image")),
        "coverPlaced": sum(1 for v in visuals if v.get("kind") == "cover" and v.get("image")),
        "bodyPlaced": sum(1 for v in visuals if v.get("kind") == "body" and v.get("image")),
        "realPlaced": sum(1 for v in visuals if v.get("image") and str((v.get("image") or {}).get("provider") or "") in {"serper", "source-origin", "source-meta"}),
        "providerCounts": provider_counts,
        "strategy": image_strategy,
        "serper": provider_counts.get("serper", 0),
        "generatedCover": provider_counts.get("generated-cover", 0),
        "generatedDiagram": provider_counts.get("generated-diagram", 0),
        "fallback": max(0, len(slots) - sum(1 for v in visuals if v.get("image"))),
        "provider": "serper" if provider_counts.get("serper") else (("source-origin" if provider_counts.get("source-origin") else "source-meta") if (provider_counts.get("source-origin") or provider_counts.get("source-meta")) else (("generated-diagram" if provider_counts.get("generated-diagram") else ("generated-cover" if provider_counts.get("generated-cover") else "none")))),
    }



def _strip_invalid_citations(markdown: str, source_count: int) -> str:
    if source_count <= 0:
        return re.sub(r"\[(?:[0-9]+(?:\s*[,，]\s*[0-9]+)*)\]", "", markdown)
    def repl(match):
        nums = []
        for raw in re.findall(r"\d+", match.group(0)):
            n = int(raw)
            if 1 <= n <= source_count and n not in nums:
                nums.append(n)
        return "[" + ",".join(str(n) for n in nums) + "]" if nums else ""
    return re.sub(r"\[(?:[0-9]+(?:\s*[,，]\s*[0-9]+)*)\]", repl, markdown)

def _enforce_evidence_binding(article: dict[str, Any], source_count: int) -> None:
    """Sanity-check that claim objects cannot cite nonexistent source ids."""
    clean_claims = []
    for claim in article.get("keyClaims") or []:
        if not isinstance(claim, dict):
            continue
        ids = []
        for raw in claim.get("sourceIds") or []:
            try:
                n = int(raw)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= source_count and n not in ids:
                ids.append(n)
        item = dict(claim)
        item["sourceIds"] = ids
        if article.get("sourceCount"):
            item["confidence"] = str(item.get("confidence") or "medium")
        clean_claims.append(item)
    article["keyClaims"] = clean_claims[:12]
    if source_count and not any(c.get("sourceIds") for c in clean_claims):
        article.setdefault("riskNotes", []).append("关键判断未绑定到具体来源编号，请在发布前核对原文。")

def _attach_sources(article: dict[str, Any], sources: list[dict[str, Any]]) -> None:
    article["sourceCount"] = len(sources)
    article["sourceList"] = [
        {
            "n": idx,
            "id": str(src.get("id") or f"source-{idx}"),
            "type": src.get("type"),
            "title": str(src.get("title") or "")[:500],
            "source": str(src.get("source") or "")[:180],
            "publishedAt": src.get("publishedAt"),
            "url": str(src.get("url") or src.get("sourceUrl") or "")[:3000],
            "sourceUrl": str(src.get("sourceUrl") or src.get("url") or "")[:3000],
            "pdfUrl": str(src.get("pdfUrl") or "")[:3000],
            "origin": src.get("origin") or ("upload" if src.get("type") == "upload" else "search"),
            "sourceVerified": bool(src.get("sourceVerified")),
            "sourceUsable": bool(src.get("sourceUsable") or src.get("sourceVerified")),
            "sourceStatus": str(src.get("sourceStatus") or ""),
            "sourceConfidence": str(src.get("sourceConfidence") or ""),
            "snippet": str(src.get("snippet") or "")[:1200],
            "sourceImages": [str(x)[:3000] for x in (src.get("sourceImages") or []) if str(x).startswith(("http://", "https://"))][:12],
        }
        for idx, src in enumerate(sources, start=1)
    ]

def settings_model_name() -> str:
    from .config import settings
    return settings.deepseek_model


def _length_spec(label: str) -> dict[str, int]:
    text = str(label or "")
    numbers = [int(x) for x in __import__('re').findall(r"(\d{3,5})", text)]
    if len(numbers) >= 2:
        low, high = sorted(numbers[:2])
        return {"min": low, "max": high, "target": int((low + high) / 2)}
    if numbers:
        target = numbers[0]
        tolerance = max(180, int(target * 0.08))
        return {"min": max(500, target - tolerance), "max": target + tolerance, "target": target}
    return {"min": 1800, "max": 2400, "target": 2100}


def _article_char_count(markdown: str) -> int:
    import re
    text = re.sub(r"```.*?```", "", str(markdown or ""), flags=re.S)
    text = re.sub(r"^#{1,6}\s+.*$", "", text, flags=re.M)
    text = re.sub(r"\[(\d+)\]", "", text)
    text = re.sub(r"[*_>`~\-]+", "", text)
    text = re.sub(r"\s+", "", text)
    return len(text)


def _style_instruction(style: str, tone: str) -> str:
    style_rules = {
        "行业观察": "以产业链、商业机制、参与方利益和落地难点为主线；判断要克制但明确，避免写成政策摘抄。",
        "政策解读": "先交代政策解决什么问题，再解释条款/机制及对企业和行业的具体影响；禁止过度解读政策原意。",
        "学术科普": "围绕读者真正需要理解的因果链展开，关键术语随文解释；研究问题、方法、证据与局限只在材料确实需要时出现，不按论文小节顺序机械展开。",
        "热点评论": "观点可以鲜明，开头要快，论证必须有证据；允许有节奏感和短句，但不做情绪化煽动。",
        "研究简报": "结论先行、信息密度高、结构清楚；少修辞，多事实、机制、对比和可执行观察点。",
        "趋势分析": "区分已发生事实、正在形成的信号和未来情景；用时间线、驱动因素和不确定性组织文章。",
        "案例拆解": "抓住真实案例里最关键的决策与转折，解释为什么这样做、实际改变了什么以及适用边界；不要求逐栏拆成‘场景/问题/做法/机制/结果/条件’固定六段。",
    }
    tone_rules = {
        "理性、清晰": "句子清楚、解释充分，少形容词，避免晦涩术语堆叠。",
        "专业、克制": "用专业词但必须准确，判断留边界，不夸大，不喊口号。",
        "通俗、易读": "优先短句和日常表达，必要术语第一次出现时顺手解释，不幼稚化。",
        "锐利、有判断": "核心观点明确，允许短句落锤和对比，但所有强判断都要有依据。",
        "学术、严谨": "术语统一、因果谨慎、明确研究局限和证据强弱，减少口语修辞。",
        "叙事感更强": "用真实场景/变化推动阅读，但不得虚构人物、对话或细节；事实仍优先。",
    }
    return f"风格执行：{style_rules.get(style, '严格按照所选文章类型的常见专业写法执行。')} 语气执行：{tone_rules.get(tone, '严格遵守用户选择的语气。')} 写法参考：让事实、解释和判断按材料关系自然穿插，关键处给出边界；学习成熟中文编辑的语言节奏，不复刻周报版式、固定栏目、编号章节或栏目口吻。"


def _control_instruction(
    *, audience: str, title_mode: str, structure: str, opener: str,
    paragraph_rhythm: str, evidence_style: str, closing_mode: str,
) -> str:
    """Translate every UI writing selector into an observable editorial rule.

    This keeps controls from becoming decorative prompt labels: each option has a
    concrete behavior the model must implement and the editorial pass must keep.
    """
    audience_rules = {
        "产业与政策关注者": "默认读者理解基本产业概念，但不假设其熟悉技术细节；重点回答政策与产业变化意味着什么。",
        "企业管理者": "强调决策影响、成本收益、风险边界和可执行动作；技术细节只保留与决策有关的部分。",
        "数据从业者": "可以使用治理、授权、数据空间等专业词，但要讲清机制、实现约束和工程/运营边界。",
        "研究人员": "明确概念边界、证据强弱、研究局限与尚未验证的推断，减少营销式表达。",
        "高校学生": "重要概念首次出现要解释，先建立因果链再给结论，避免默认行业背景知识。",
        "普通公众": "把术语翻译成日常语言，多解释‘这和普通人/企业有什么关系’，但不编造故事来凑可读性。",
    }
    title_rules = {
        "默认 · 自然起题": "不要套任何固定标题句式，由文章最具体、最值得读的变化/冲突/影响自然起题。除非主题本身就是概念解释，否则不要机械把‘数据要素治理’放在标题开头，更禁止‘数据要素治理：——…’式前缀。",
        "公众号吸引力型": "像成熟微信公众号标题：对象明确、变化明确、有一处值得追问或产生反差的地方；优先使用‘为什么/意味着什么/真正变化在于/背后发生了什么’这类自然钩子，但不夸张、不造势、不用营销词。",
        "信息密度型": "标题包含主题对象 + 最重要变化/判断，不堆情绪词，不做悬念党。",
        "问题型": "至少两个标题候选使用真实问题句，正文必须在前 1/3 明确回答问题。",
        "趋势判断型": "标题突出正在形成的趋势和方向，但避免把不确定趋势写成既成事实。",
        "政策解读型": "标题点出政策/制度变化及其影响对象，避免泛泛写‘重磅’‘重大利好’。",
        "克制的吸引力": "标题要有明确对象、变化和冲突/反差中的至少两项，优先让读者产生‘为什么’或‘这意味着什么’的阅读动力；可以有判断，不靠夸张词。",
    }
    structure_rules = {
        "默认 · 按内容自然组织": "把它当成成熟编辑的自然写法，而不是结构模板：先判断全文真正需要几次转折，再决定是否使用小标题。多数文章应为 0—3 个小标题，内容本来连贯时完全可以不用；只有篇幅和证据真的需要才到 4 个。不要把正文机械拆成‘问题/做法/机制/影响/条件/判断’六段式，不要用这些抽象词加冒号充当标题，也不要让所有小标题都保持相同句式或相同长度。能用一两句过渡自然连接的地方，就合并而不是另起标题。",
        "问题—机制—案例—趋势": "正文依次完成：提出真实问题 → 拆解形成原因/机制 → 用来源支持的案例或场景验证 → 给出趋势与不确定性。没有案例来源时改为‘场景/条件’，不得虚构案例。",
        "现象—原因—影响—建议": "正文依次完成：描述现象 → 解释原因 → 分对象分析影响 → 给出有条件的建议，不把建议写成口号。",
        "政策—产业—技术—风险": "正文分别回答政策规则、产业参与方、技术实现和风险边界四层，并说明它们如何相互约束。",
        "结论先行—证据展开—观察": "开头 15% 内给出核心结论，主体用证据逐项展开，最后列出仍需观察的变量。",
        "由模型按证据自动组织": "根据证据密度选择最自然的 3—6 个章节，但章节之间必须有明确逻辑链，不按固定模板凑段。",
    }
    opener_rules = {
        "默认 · 选择最自然切口": "像编辑自然落笔：从材料中最值得读的一处直接进入，可以是事实、变化、场景、判断或问题；不要为了证明用了某种开头而刻意制造反问、冲突或背景铺垫。",
        "变化/矛盾切入": "第一段直接写一个正在发生的变化或矛盾，第二段解释为什么值得关注；不要用‘随着时代发展’开头。",
        "关键事实切入": "第一段使用来源支持的关键事实/研究结论切入；没有来源时不得伪造事实，改用可验证的概念性事实。",
        "真实场景切入": "第一段从来源中已有的真实场景切入；来源没有场景时用一般业务场景描述，明确不是具体案例。",
        "问题切入": "开头提出一个具体可回答的问题，并在随后两段给出初步答案，避免连续堆三个以上反问。",
        "结论先行": "第一段直接给出全篇核心判断，后文负责解释‘为什么’和‘边界在哪里’。",
    }
    rhythm_rules = {
        "默认 · 随内容调整": "不执行固定段长方案。让事实、解释、转折和判断自然形成段落：有的段可以很短，有的段需要完整展开；避免连续出现长度近似、句式近似的‘AI 方块段’。",
        "短中段交替": "关键判断用 1—3 句短段落，解释和证据用中等段落，避免连续五段长度几乎一样。",
        "紧凑短段": "多数段落控制在 2—4 句，信息密度高，适合手机阅读；不要把一句话拆成无意义碎段。",
        "深度长段": "允许 5—8 句完整论证段，但每段必须围绕一个中心问题，并用连接句保持可读性。",
        "由内容自动调整": "按论证需要决定段长；结论短、解释长、转折适中，避免机械统一段落长度。",
    }
    evidence_rules = {
        "默认 · 自然融入证据": "来源要像成熟媒体文章那样融入叙述：必要时点明机构、政策、研究或案例，不把每一段都写成‘据某某显示’；事实与判断边界清楚即可。默认不写论文式参考文献，只有开启来源编号时才使用 [1][2]。",
        "事实与判断分开": "先陈述来源支持的事实，再单独给出解释/判断；读者能看清哪里是事实、哪里是作者分析。",
        "证据紧跟结论": "每个重要判断后尽快给出对应事实/来源，不要在数段之后才补证据。",
        "学术引用更明显": "涉及论文时写明研究对象、方法/样本或结论边界（来源有提供时），引用编号紧跟相关句。",
        "弱化编号、增强可读性": "仍保留必要来源编号，但一段最多集中出现少量编号，优先自然叙述来源主体和结论。",
    }
    closing_rules = {
        "默认 · 自然收束": "顺着全文最后一层意思收住即可：可以落到边界、影响、判断或仍待验证的变量；不要求总结全文，不要求列清单，也不要使用‘综上所述/未来可期/值得期待’式标准结尾。",
        "观察清单": "最后形成 3—5 个具体观察变量/检查点，每一点可在未来被验证，不重复正文标题。",
        "未来趋势": "结尾区分高概率方向与不确定因素，避免无依据预测具体时间/规模。",
        "行动建议": "按读者身份给出 3—5 条可执行建议，并写清建议成立的前提。",
        "开放问题": "用 2—4 个真正尚未解决的问题收束，不用空泛‘让我们拭目以待’。",
        "简短总结": "用一个紧凑段落收束核心判断，不新增事实，不重复整篇摘要。",
    }
    return "\n".join([
        f"读者适配：{audience_rules.get(audience, '按用户指定读者控制术语密度和解释深度。')}",
        f"标题执行：{title_rules.get(title_mode, '严格执行标题偏好。')}",
        f"结构执行：{structure_rules.get(structure, '严格执行结构偏好。')}",
        f"开头执行：{opener_rules.get(opener, '严格执行开头方式。')}",
        f"段落执行：{rhythm_rules.get(paragraph_rhythm, '严格执行段落节奏。')}",
        f"证据执行：{evidence_rules.get(evidence_style, '严格执行证据表达方式。')}",
        f"结尾执行：{closing_rules.get(closing_mode, '严格执行结尾方式。')}",
    ])


def _pop_meta(result: dict[str, Any]) -> dict[str, Any]:
    meta = result.pop("_deepseekMeta", {}) if isinstance(result, dict) else {}
    return meta if isinstance(meta, dict) else {}


def _generation_meta(
    calls: list[dict[str, Any]], *, writing_spec: dict[str, Any], visual_spec: dict[str, Any] | None = None, actual_chars: int,
    within_target: bool, source_count: int, auto_evidence_added: int,
) -> dict[str, Any]:
    prompt_tokens = sum(int(x.get("promptTokens") or 0) for x in calls)
    completion_tokens = sum(int(x.get("completionTokens") or 0) for x in calls)
    reasoning_tokens = sum(int(x.get("reasoningTokens") or 0) for x in calls)
    total_tokens = sum(int(x.get("totalTokens") or 0) for x in calls)
    return {
        "apiCalled": bool(calls) and all(bool(x.get("apiCalled")) for x in calls),
        "provider": "DeepSeek",
        "model": next((x.get("model") for x in reversed(calls) if x.get("model")), settings_model_name()),
        "callCount": len(calls),
        "calls": calls,
        "promptTokens": prompt_tokens,
        "completionTokens": completion_tokens,
        "reasoningTokens": reasoning_tokens,
        "totalTokens": total_tokens,
        "writingSpec": writing_spec,
        "visualSpec": dict(visual_spec or {}),
        "actualChars": actual_chars,
        "withinTarget": within_target,
        "sourceCount": source_count,
        "autoEvidenceAdded": auto_evidence_added,
    }


def _evidence_excerpt(source: dict[str, Any], query: str, *, limit: int = 1700) -> str:
    """Select high-signal source sentences without another model call.

    Navigation/advertising boilerplate is excluded before ranking, and sentence
    boundaries are preserved so the model sees fewer truncated or misleading
    fragments. This lowers prompt tokens while improving evidence quality.
    """
    raw = str(source.get("rawContent") or source.get("verifiedDescription") or source.get("snippet") or "")
    raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) <= limit:
        return raw
    title = str(source.get("title") or "")
    wanted = _query_tokens(f"{query} {title}")
    parts = [x.strip() for x in re.split(r"(?<=[。！？；!?])\s*|\s{2,}", raw) if len(x.strip()) >= 12]
    if not parts:
        return raw[:limit].rsplit("。", 1)[0] + ("。" if "。" in raw[:limit] else "")

    junk_markers = ("广告", "版权", "免责声明", "责任编辑", "扫码", "登录", "上一篇", "下一篇", "相关阅读", "友情链接")
    clean_parts = [(i, part) for i, part in enumerate(parts) if not any(x in part for x in junk_markers)]
    if not clean_parts:
        clean_parts = list(enumerate(parts))

    scored: list[tuple[float, int, str]] = []
    first_content_indexes = {i for i, _ in clean_parts[:2]}
    for i, part in clean_parts:
        tokens = _query_tokens(part)
        overlap = len(tokens & wanted)
        signal = overlap * 3.0
        if i in first_content_indexes:
            signal += 2.8
        if re.search(r"\d|《[^》]{2,40}》|发布|印发|提出|显示|研究|发现|试点|落地|上线|成立|同比|增长|下降", part):
            signal += 1.5
        scored.append((signal, i, part))

    # Keep the best facts, then restore source order so the excerpt remains
    # coherent. Add complete sentences only; never spend the final tokens on a
    # chopped half-sentence.
    selected: set[int] = set()
    ranked = sorted(scored, key=lambda x: (x[0], -x[1]), reverse=True)
    for _, i, _ in ranked:
        candidate = [part for idx, part in clean_parts if idx in selected or idx == i]
        if len(" ".join(candidate)) <= limit:
            selected.add(i)
        if len(" ".join(part for idx, part in clean_parts if idx in selected)) >= limit * 0.86:
            break
    if not selected:
        return clean_parts[0][1][:limit]
    return " ".join(part for i, part in clean_parts if i in selected)[:limit].strip()


def _naturalize_default_structure(markdown: str, structure: str) -> str:
    """Low-risk de-templating for default-mode headings, with no extra API call."""
    if not str(structure or "").startswith("默认"):
        return str(markdown or "")
    generic = ("问题", "做法", "机制", "原因", "影响", "条件", "判断", "趋势", "建议", "结论", "路径", "价值", "风险", "启示")
    lines: list[str] = []
    for line in str(markdown or "").splitlines():
        m = re.match(r"^(##\s+)(.+)$", line.strip())
        if not m:
            lines.append(line)
            continue
        heading = m.group(2).strip()
        stripped = re.sub(r"^(?:" + "|".join(generic) + r")\s*[：:]\s*", "", heading)
        if stripped != heading and len(re.sub(r"\s+", "", stripped)) >= 4:
            lines.append("## " + stripped)
            continue
        if heading.rstrip("：:") in generic:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _llm_article(
    query: str, sources: list[dict[str, Any]], style: str, audience: str, length: str,
    factual: bool, citations: bool, *, angle: str = "", tone: str = "理性、清晰",
    title_mode: str = "默认 · 自然起题", structure: str = "默认 · 按内容自然组织",
    closing_mode: str = "默认 · 自然收束", image_count: int = 3, image_preference: str = "混合",
    opener: str = "默认 · 选择最自然切口", paragraph_rhythm: str = "默认 · 随内容调整",
    evidence_style: str = "默认 · 自然融入证据", ai_cliche_guard: bool = True,
    length_spec: dict[str, int] | None = None, reasoning_effort: str = "low", smart_sections: bool = True,
    brief_plan: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    length_spec = length_spec or _length_spec(length)
    source_pack = []
    for idx, src in enumerate(sources[:6], start=1):
        source_pack.append({
            "n": idx, "type": src.get("type"), "title": src.get("title"),
            "source": src.get("source"), "date": src.get("publishedAt"), "url": src.get("url"),
            "content": _evidence_excerpt(src, query, limit=1700), "score": src.get("score"),
        })
    evidence_rule = (
        "已提供来源。所有具体数字、政策名称、机构动作、论文结论和案例事实必须能对应来源编号。"
        if source_pack else
        "用户没有提供也没有要求自动检索佐证材料。不得虚构具体数字、政策名称、机构表态、论文结论或案例细节；"
        "文章应以概念解释、机制分析、常识性判断和明确标注的不确定性为主，且不要伪造 [1][2] 引用。"
    )
    style_rule = _style_instruction(style, tone)
    control_rule = _control_instruction(
        audience=audience, title_mode=title_mode, structure=structure, opener=opener,
        paragraph_rhythm=paragraph_rhythm, evidence_style=evidence_style, closing_mode=closing_mode,
    )
    system_prompt = f"""你是一名成熟的中文科技、产业与数字经济主编。你的任务不是套模板，而是严格执行用户给定的写作规格。
{style_rule}
{control_rule}

写作底线：
- 用户的“写作切口”是最高优先级编辑简报：必须决定标题角度、开头、信息取舍、论证顺序和结论；不要把切口原文机械复制进正文，也不要把它当作普通风格标签。
- {evidence_rule}
- 段落之间必须有因果、转折、递进或解释关系，不写互不相干的信息块。先在内部确定一句中心判断和证据之间的关系，再落笔；不要在正文里宣布“本文将从几个方面展开”。
- 每段优先使用具体主体和动作来推进（谁做了什么、数据改变了什么、为什么会这样），少用连续抽象名词堆叠；同一个判断不要换词重复三遍。
- “默认”选项代表把结构判断交给成熟编辑直觉，不代表套一个默认模板。不要为了显得结构清晰而把文章切成整齐的六块；段落起句也不要连续复制“这意味着/更重要的是/从…来看”这一类编辑腔。
- 避免典型 AI 套话和空泛口号；不要机械使用“首先、其次、最后”，也不要连续使用“问题：… / 做法：… / 机制：… / 影响：… / 判断：…”这种教科书提纲式小标题。
- 微信公众号标题必须有阅读动力但不能标题党：优先把“具体变化/冲突/影响/疑问”中的至少一种放进标题；不得使用用户规格、生成参数、内部字段或搜索词堆砌成标题。
- 站点主题“数据要素治理”只是选题范围，不是标题模板。除非文章就是解释这个概念，否则标题优先写具体事件、变化、对象和影响，不要机械使用“数据要素治理：…”“数据要素治理——…”“数治周报”“本周观察”等固定前缀。
- 用户提供的示例文档只用于学习成熟中文写作的语言节奏：事实清楚、机制解释具体、判断有边界。严禁复刻其“周报/本周导读/数治观察/01 02 03/资料来源”等版式或栏目结构。
- 句式有长短变化，关键判断允许短句落地；不要每段同样长度。
- 输出严格 JSON，不使用 Markdown 代码围栏。正文使用 Markdown。
- 文章正文（不含标题、导语、来源和配图说明）的目标长度必须落在 {length_spec['min']}—{length_spec['max']} 个中文字符附近，目标约 {length_spec['target']} 字。不能用缩短文章来省 token。"""
    user_prompt = f"""围绕主题「{query}」生成一篇可直接进入微信公众号发布流程的图文文章。

【用户写作规格 - 每一项都必须真正影响正文】
你必须先理解“写作切口/用户实际要求”隐含的真实目标，并在返回中用一句话写进 understoodBrief；不要逐字复述用户原话。
文章类型：{style}
目标读者：{audience}
篇幅：{length}；硬性范围 {length_spec['min']}—{length_spec['max']} 字，目标约 {length_spec['target']} 字
写作角度 / 用户实际要求：{angle or '由主题与证据判断最有信息量的切口'}
系统对用户写作要求的结构化理解（只作为编辑简报，绝不能原样写进正文）：
{json.dumps(brief_plan or {}, ensure_ascii=False)}
如果用户填写了写作角度，把它视为编辑简报而不是“可选标签”：必须落实到标题、开头、主体取舍、章节顺序和结尾判断中。
语言语气：{tone}
标题偏好：{title_mode}
结构偏好：{structure}
开头方式：{opener}
段落节奏：{paragraph_rhythm}
证据表达：{evidence_style}
结尾方式：{closing_mode}
AI 套话抑制：{'严格' if ai_cliche_guard else '常规'}
事实核验：{'开启' if factual else '常规'}
保留来源编号：{'是' if citations else '否（不要生成引用编号）'}
最终正文末尾不要自行添加“参考文献 / 参考来源 / 资料来源”章节；来源清单由系统内部管理，只有用户显式开启来源编号时正文内才出现 [1][2]。
智能小节：{"开启" if smart_sections else "关闭"}；开启也不等于必须分点。允许 0—4 个 ## 小标题，只有当一次明显的论证转折确实值得给读者路标时才使用；小标题必须写本节的具体内容，不得使用“问题/做法/机制/影响/条件/判断”这类抽象栏目名加冒号，不得为配图或凑结构额外造标题。

来源资料：
{json.dumps(source_pack, ensure_ascii=False)}

返回 JSON：
{{
  "understoodBrief": "一句话概括你真正理解的用户写作目标，而不是复述用户原话",
  "titleCandidates": ["标题1", "标题2", "标题3", "标题4", "标题5"],
  "recommendedTitle": "从标题候选中选出最适合微信公众号发布的一条",
  "deck": "80字以内导语",
  "markdown": "完整正文；默认以自然文章为准。智能小节开启时仍允许 0—4 个 ## 二级标题，只有确实帮助阅读时才用；禁止固定六段式和抽象栏目式小标题。",
  "socialSummary": "120字以内简介",
  "keyClaims": [{{"claim":"核心事实或判断","sourceIds":[1],"confidence":"high|medium|low"}}],
  "riskNotes": ["需要复核的点"],
  "sourceNotes": [{{"sourceId":1,"whyUsed":"来源作用"}}]
}}

必须自检后再输出：
1. 正文长度是否真正落在 {length_spec['min']}—{length_spec['max']} 字；如果明显过短，继续展开机制、例子（仅限来源支持）、影响和边界，而不是提前结束。
2. {style} 与 {tone} 是否从开头到结尾一致，而不是只在第一段体现。
3. 小标题数量是否真的有必要；若去掉小标题文章更顺，就减少或取消。凡保留的小标题都要具体、有信息量，且下面必须有完整论证。
4. 没有来源时绝不编造具体事实；有来源时具体事实尽量紧跟编号。
5. 不要为了配图改变正文结构或制造小标题。封面、配图位置、源新闻回图、网络找图和代码绘图全部由文章完成后的本地视觉路由处理；本次写作只需要把正文和来源关系写清楚。"""
    max_tokens = max(3600, int(length_spec["target"] * 1.38) + 420)
    result = deepseek.generate_json(system_prompt, user_prompt, max_tokens=max_tokens, temperature=0.58, reasoning_effort=reasoning_effort)
    meta = _pop_meta(result)
    return _sanitize_article(result, query), meta


def _llm_polish_article(
    article: dict[str, Any], query: str, sources: list[dict[str, Any]], *, style: str,
    audience: str, tone: str, paragraph_rhythm: str, evidence_style: str,
    ai_cliche_guard: bool, length_spec: dict[str, int], title_mode: str, structure: str,
    opener: str, closing_mode: str, angle: str, smart_sections: bool = True,
    reasoning_effort: str = "off",
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_digest = [
        {"n": i, "title": src.get("title"), "source": src.get("source"), "date": src.get("publishedAt"),
         "content": _evidence_excerpt(src, query, limit=1250)}
        for i, src in enumerate(sources, start=1)
    ]
    style_rule = _style_instruction(style, tone)
    control_rule = _control_instruction(
        audience=audience, title_mode=title_mode, structure=structure, opener=opener,
        paragraph_rhythm=paragraph_rhythm, evidence_style=evidence_style, closing_mode=closing_mode,
    )
    prompt = f"""你现在担任终审主编。请编辑首稿，但不得把用户选择的写作规格“磨平”。
主题：{query}
文章类型：{style}；读者：{audience}；语气：{tone}；标题偏好：{title_mode}；结构：{structure}；开头：{opener}；结尾：{closing_mode}；切口：{angle or '自动'}。
段落节奏：{paragraph_rhythm}；证据表达：{evidence_style}；套话抑制：{'严格' if ai_cliche_guard else '常规'}。
长度硬约束：正文必须保持在 {length_spec['min']}—{length_spec['max']} 字，目标约 {length_spec['target']} 字。编辑时不允许因为“精炼”把文章压缩成短稿。
风格细则：{style_rule}
逐项控件执行细则：
{control_rule}

首稿 JSON：
{json.dumps(article, ensure_ascii=False)}

可核对来源：
{json.dumps(source_digest, ensure_ascii=False)}

重点检查：删除空话和重复，但用更具体的解释、机制、对比、证据和边界替代；保持所选风格和语气；事实与判断分开；不增加来源外事实；尤其检查“AI 提纲味”：若出现“问题/做法/机制/影响/条件/判断”等整齐栏目，合并章节、改成具体内容标题，或直接去掉小标题。智能小节开启时也只保留真正必要的 0—4 个，不为凑数量硬拆。
返回与首稿同结构的完整 JSON，并增加 "editorialNotes"。"""
    result = deepseek.generate_json(
        "你是中文科技与产业内容的终审主编。严格执行用户的风格、语气、结构和字数要求，只返回 JSON。",
        prompt, max_tokens=max(5200, int(length_spec["target"] * 1.8) + 700), temperature=0.42, reasoning_effort=reasoning_effort,
    )
    meta = _pop_meta(result)
    polished = _sanitize_article(result, query)
    polished["editorialNotes"] = [str(x)[:300] for x in (result.get("editorialNotes") or [])[:8]]
    return polished, meta


def _llm_length_repair(
    article: dict[str, Any], query: str, sources: list[dict[str, Any]], *, writing_spec: dict[str, Any], length_spec: dict[str, int]
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_count = _article_char_count(str(article.get("markdown") or ""))
    source_digest = [
        {"n": i, "title": src.get("title"), "content": _evidence_excerpt(src, query, limit=1350)}
        for i, src in enumerate(sources, start=1)
    ]
    direction = "扩写" if current_count < length_spec["min"] else "压缩"
    prompt = f"""对下面文章执行一次“长度校正”，不是重写成另一种风格。
当前正文约 {current_count} 字；用户要求 {length_spec['min']}—{length_spec['max']} 字，目标约 {length_spec['target']} 字。请{direction}到目标区间。
用户写作规格：{json.dumps(writing_spec, ensure_ascii=False)}
主题：{query}
当前文章：{json.dumps(article, ensure_ascii=False)}
来源：{json.dumps(source_digest, ensure_ascii=False)}
规则：
- 保持原文章类型、语气、结构、标题偏好、开头方式和结尾方式。
- 扩写时增加解释、机制、影响、对比、边界和来源已支持的例子；禁止注水和重复。
- 压缩时删重复和空话，不删关键事实、论证链和来源关系。
- 不新增来源外事实；若来源为空，绝不虚构具体数据/政策/案例。
- 返回完整 JSON，字段与原文一致。"""
    result = deepseek.generate_json(
        "你是严格执行篇幅约束的中文主编。目标是让正文进入指定字数区间，同时保持风格和事实边界。只返回 JSON。",
        prompt, max_tokens=max(5200, int(length_spec["target"] * 1.7) + 600), temperature=0.35, reasoning_effort="high",
    )
    meta = _pop_meta(result)
    repaired = _sanitize_article(result, query)
    repaired.setdefault("editorialNotes", article.get("editorialNotes") or [])
    repaired.setdefault("imageQueries", article.get("imageQueries") or [])
    repaired.setdefault("imageSlots", article.get("imageSlots") or [])
    return repaired, meta


def _llm_revise_article(
    current: dict[str, Any], sources: list[dict[str, Any]], *, query: str, scope: str,
    target_text: str, target_heading: str, instruction: str,
) -> dict[str, Any]:
    scope_map = {
        "sentence": "只修改用户选中的句子，其他文字尽可能逐字保留",
        "paragraph": "只修改用户选中的段落，其他段落尽可能逐字保留",
        "section": "只修改指定二级标题对应的完整章节，其他章节尽可能逐字保留",
        "whole": "允许在原稿基础上整体修改，但必须保留原稿已确认的事实边界、来源关系和原始写作规格，除非用户明确要求改变",
    }
    scope_rule = scope_map.get(scope, scope_map["whole"])
    source_pack = [
        {"n": i, "type": src.get("type"), "title": src.get("title"), "source": src.get("source"),
         "date": src.get("publishedAt"), "content": _evidence_excerpt(src, query, limit=1800)}
        for i, src in enumerate(sources, start=1)
    ]
    writing_spec = dict((current.get("generationMeta") or {}).get("writingSpec") or {})
    prompt = f"""请在当前文章基础上执行一次可控修改。
主题：{query}
修改范围：{scope}
范围约束：{scope_rule}
指定标题：{target_heading or '未指定'}
用户选中文字：{target_text or '未指定'}
用户修改要求：{instruction}
原始写作规格：{json.dumps(writing_spec, ensure_ascii=False)}

当前文章：
{json.dumps({k: current.get(k) for k in ['titleCandidates','deck','markdown','coverBrief','imageQueries','imageSlots','socialSummary','keyClaims','riskNotes','sourceNotes']}, ensure_ascii=False)}

来源证据：
{json.dumps(source_pack, ensure_ascii=False)}

要求：
- 必须返回完整文章 JSON，而不是只返回改动部分。
- 没被指定修改的部分不要顺手重写；局部修改保持原文风格和上下文。
- 除非用户本次明确要求改变，否则继续遵守原始文章类型、语言语气、目标读者、结构和篇幅。
- 若用户要求与来源冲突，以来源事实为准，并在 riskNotes 中说明。
- 没有来源时不得新增具体数据、政策、机构表态、论文结论或虚构案例。
- 如果修改二级标题，同步修正 imageSlots.afterHeading。
- revisionSummary 用 1-3 句话概括实际改动。
返回字段：titleCandidates, deck, markdown, coverBrief, imageQueries, imageSlots, socialSummary, keyClaims, riskNotes, sourceNotes, revisionSummary。"""
    result = deepseek.generate_json(
        "你是擅长局部改稿并保持上下文稳定的中文主编。严格遵守修改范围和原始写作规格，只返回 JSON。",
        prompt, max_tokens=12000, temperature=0.42, reasoning_effort="high",
    )
    meta = _pop_meta(result)
    sanitized = _sanitize_article(result, query)
    sanitized["revisionSummary"] = str(result.get("revisionSummary") or instruction)[:500]
    sanitized["_revisionMeta"] = meta
    return sanitized


def _recover_embedded_article_payload(text: str) -> dict[str, Any] | None:
    """Recover structured article JSON accidentally returned inside markdown.

    Some compatible model gateways prefix valid JSON with a literal ``json`` token
    instead of a fenced block. That payload is internal transport data and must
    never be shown to the reader.
    """
    raw = str(text or "").strip().lstrip("\ufeff")
    if not raw:
        return None
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    raw = re.sub(r"^json\s*:?[\s\r\n]*", "", raw, flags=re.I)
    candidates = [raw]
    left, right = raw.find("{"), raw.rfind("}")
    if 0 <= left < right:
        candidates.append(raw[left:right + 1])
    for candidate in candidates:
        try:
            value: Any = json.loads(candidate)
            if isinstance(value, str):
                value = json.loads(value)
            if isinstance(value, dict) and isinstance(value.get("markdown"), str):
                return value
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return None


def _sanitize_article(article: dict[str, Any], query: str) -> dict[str, Any]:
    article.pop("_deepseekMeta", None)
    markdown = str(article.get("markdown") or "").strip()
    recovered = None
    if markdown and (
        '"markdown"' in markdown
        or '"titleCandidates"' in markdown
        or re.match(r"^\s*(?:```json|json\s*[{:]|\{)", markdown, flags=re.I)
    ):
        recovered = _recover_embedded_article_payload(markdown)
    if recovered:
        # Recover the whole structured response when useful, while keeping any
        # already-valid outer fields returned by the API wrapper.
        for key in (
            "understoodBrief", "titleCandidates", "recommendedTitle", "deck", "markdown",
            "coverBrief", "imageQueries", "imageSlots", "socialSummary", "keyClaims",
            "riskNotes", "sourceNotes",
        ):
            value = recovered.get(key)
            if value not in (None, "", [], {}):
                if key == "markdown" or not article.get(key):
                    article[key] = value
        markdown = str(recovered.get("markdown") or "").strip()

    titles = article.get("titleCandidates")
    if not isinstance(titles, list):
        titles = []
    recommended = _clean_title(article.get("recommendedTitle"), query)
    title_pool = [recommended, *titles] if recommended else list(titles)
    article["titleCandidates"] = _rank_wechat_titles(title_pool, query)
    article["recommendedTitle"] = article["titleCandidates"][0]
    article["understoodBrief"] = str(article.get("understoodBrief") or "")[:600]
    if isinstance(article.get("understoodBriefPlan"), dict):
        article["understoodBriefPlan"] = {k: article["understoodBriefPlan"].get(k) for k in ("objective", "mustInclude", "avoid", "readerNeed", "stance", "structureAdvice", "usedModel")}
    article["deck"] = str(article.get("deck") or "")[:220]

    # A defensive second pass keeps transport artefacts out of the visible article
    # even when a gateway returned an imperfect JSON wrapper.
    markdown = _strip_internal_artifacts(str(article.get("markdown") or markdown).strip())
    article["markdown"] = _strip_reference_section(markdown)
    article["coverBrief"] = str(article.get("coverBrief") or f"{query} 真实产业、政策或研究场景")[:500]
    article["imageQueries"] = [str(x)[:140] for x in (article.get("imageQueries") or [query])[:9]]
    article["imageSlots"] = _sanitize_image_slots(article.get("imageSlots"))
    article["socialSummary"] = str(article.get("socialSummary") or article["deck"])[:240]
    article["keyClaims"] = list(article.get("keyClaims") or [])[:12]
    article["riskNotes"] = list(article.get("riskNotes") or [])[:10]
    article["sourceNotes"] = list(article.get("sourceNotes") or [])[:12]
    return article


def _sanitize_image_slots(raw: Any) -> list[dict[str, Any]]:
    allowed_types = {"flow", "causal", "compare", "layered", "network", "timeline", "kpi", "matrix", "concept"}
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        intent = str(item.get("visualIntent") or "auto").strip().lower()
        if intent not in {"auto", "real", "diagram"}:
            intent = "auto"
        visual_type = str(item.get("visualType") or "").strip().lower()
        if visual_type not in allowed_types:
            visual_type = ""
        plan = item.get("visualPlan") if isinstance(item.get("visualPlan"), dict) else {}
        clean_plan: dict[str, Any] = {}
        for key in ("title", "center", "relation", "leftTitle", "rightTitle", "xLabel", "yLabel"):
            if plan.get(key) not in (None, ""):
                clean_plan[key] = str(plan.get(key))[:80]
        for key in ("nodes", "steps", "items", "causes", "layers", "subtitles", "actors", "entities", "left", "right", "before", "after", "quadrants"):
            value = plan.get(key)
            if isinstance(value, list):
                clean_plan[key] = [str(x.get("label") or x.get("title") or x.get("name") or x)[:80] if isinstance(x, dict) else str(x)[:80] for x in value[:6]]
        for key in ("events", "metrics", "numbers"):
            value = plan.get(key)
            if isinstance(value, list):
                rows = []
                for row in value[:6]:
                    if isinstance(row, dict):
                        rows.append({str(k)[:24]: str(v)[:80] for k, v in list(row.items())[:5]})
                    else:
                        rows.append(str(row)[:80])
                clean_plan[key] = rows
        try:
            source_id = max(0, int(item.get("sourceId") or 0))
        except (TypeError, ValueError):
            source_id = 0
        out.append({
            "afterHeading": str(item.get("afterHeading") or "")[:180],
            "purpose": str(item.get("purpose") or "解释本节核心信息")[:180],
            "query": str(item.get("query") or "")[:140],
            "sourceId": source_id,
            "visualIntent": intent,
            "visualType": visual_type,
            "visualPlan": clean_plan,
        })
    return out


def _strip_internal_artifacts(markdown: str) -> str:
    lines = str(markdown or "").replace("\r", "").split("\n")
    blocked = ("titleCandidates", "recommendedTitle", "editorialNotes", "generationMeta", "imageSlots", "sourceNotes", "sourceIds", "写作规格", "用户写作规格", "Token")
    kept = []
    for line in lines:
        low = line.lower().replace(" ", "")
        if low.startswith("json{") or low.startswith("json {"):
            continue
        if any(token.lower() in low for token in blocked) and ("{" in line or ":" in line):
            continue
        if line.strip().startswith("```json") or line.strip() == "```":
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _strip_reference_section(markdown: str) -> str:
    lines = str(markdown or "").replace("\r", "").split("\n")
    kept: list[str] = []
    for line in lines:
        stripped = line.strip().lower().replace(" ", "")
        if stripped.startswith("#"):
            heading = stripped.lstrip("#")
            if heading in {"参考来源", "参考资料", "references", "sources", "资料来源"}:
                break
        kept.append(line)
    return "\n".join(kept).strip()


def _visual_caption(visual: dict[str, Any]) -> str:
    image = visual.get("image") or {}
    # The generated cover already contains the title/visual brief inside the image;
    # repeating the title as a caption makes both preview and PDF look redundant.
    # Provenance remains visible in the image-audit panel.
    if str(image.get("provider") or "") == "generated-cover":
        return ""
    description = " ".join(str(image.get("description") or visual.get("purpose") or "文章配图").split()).strip()
    if len(description) > 82:
        description = description[:80].rstrip("，。；：、 ") + "…"
    source = str(image.get("source") or "").strip()
    if source:
        return f"{description} · 来源：{source}"[:180]
    return description[:120]


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_urls: set[str] = set()
    seen_titles: list[str] = []
    output: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("url") or "").strip().lower()
        title_key = normalize_title(str(item.get("verifiedTitle") or item.get("title") or ""))
        if not key and not title_key:
            continue
        if key and key in seen_urls:
            continue
        if title_key:
            if title_key in seen_titles:
                continue
            # Remove near-identical syndicated reports even if their headlines differ slightly.
            duplicate = False
            for prev in seen_titles[-40:]:
                a=set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{3,}", title_key))
                b=set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{3,}", prev))
                if a and b and len(a & b) / max(1, min(len(a), len(b))) >= 0.82:
                    duplicate = True
                    break
            if duplicate:
                continue
        if key: seen_urls.add(key)
        if title_key: seen_titles.append(title_key)
        output.append(item)
    return output


def _dedupe_images(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output = []
    for image in images:
        if not isinstance(image, dict):
            continue
        url = str(image.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        output.append(
            {
                "url": url,
                "description": str(image.get("description") or "")[:320],
                "source": str(image.get("source") or "")[:160],
                "sourceUrl": str(image.get("sourceUrl") or "")[:3000],
            }
        )
    return output
