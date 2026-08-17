from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from .http_client import UpstreamError

BLOCKED_HOST_MARKERS = {
    "msn.com", "newsbreak.com", "flipboard.com", "smartnews.com", "yahoo.com", "bing.com",
    "google.com", "baidu.com", "toutiao.com", "flipboard.com",
}

@dataclass
class Verification:
    ok: bool
    url: str
    final_url: str
    title: str
    description: str
    status: int
    reason: str


def is_http_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(str(url or "").strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower().strip()
    except Exception:
        return ""


def is_aggregator_host(url: str) -> bool:
    h = host(url)
    return any(h == marker or h.endswith("." + marker) for marker in BLOCKED_HOST_MARKERS)


def _tokenize(text: str) -> set[str]:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(text or ""))).lower()
    tokens = set(re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,6}", text))
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for size in (2, 3, 4):
            for i in range(max(0, len(chunk) - size + 1)):
                tokens.add(chunk[i:i+size])
    return tokens


def title_similarity(expected: str, actual: str) -> float:
    a, b = _tokenize(expected), _tokenize(actual)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, min(len(a), len(b)))


def _extract_meta(text: str, final_url: str) -> tuple[str, str, str]:
    sample = text[:250_000]
    title = ""
    canonical = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", sample, re.I | re.S)
    if m:
        title = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1)))).strip()
    for pattern in (
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)',
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']',
        r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:url["\']',
        r'<meta[^>]+name=["\']twitter:url["\'][^>]+content=["\']([^"\']+)',
    ):
        m = re.search(pattern, sample, re.I)
        if m:
            canonical = urllib.parse.urljoin(final_url, html.unescape(m.group(1))).strip()
            if canonical:
                break
    description = ""
    for pattern in (
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']',
    ):
        mm = re.search(pattern, sample, re.I)
        if mm:
            description = html.unescape(re.sub(r"\s+", " ", mm.group(1))).strip()[:1200]
            break
    return title, canonical, description



def is_error_page(title: str, description: str) -> bool:
    text=(str(title or "")+" "+str(description or "")).lower()
    markers=("captcha", "验证", "验证码", "search too frequent", "搜索过于频繁", "access denied", "forbidden", "请稍后重试", "rate limit")
    return any(m in text for m in markers)


def verify_url(url: str, expected_title: str = "", *, timeout: float = 2.6) -> Verification:
    url = str(url or "").strip()
    if not is_http_url(url):
        return Verification(False, url, "", "", "", 0, "不是有效网页地址")
    if is_aggregator_host(url):
        return Verification(False, url, "", "", "", 0, "聚合/导航站，不作为原始来源")
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; DataElementGovernance/1.1)",
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.7",
        "Accept-Language": "zh-CN,zh;q=0.8,en;q=0.5",
    }
    # Avoid a HEAD+GET round trip for ordinary pages: many Chinese sites reject
    # HEAD and it doubled verification latency. A cheap HEAD probe is useful only
    # for direct PDF URLs where HTML metadata is unavailable anyway.
    if url.lower().split("?", 1)[0].endswith(".pdf"):
        try:
            req = urllib.request.Request(url, headers=headers, method="HEAD")
            with urllib.request.urlopen(req, timeout=min(2.0, timeout)) as resp:
                final_url = str(resp.geturl() or url)
                status = int(getattr(resp, "status", 200) or 200)
                if status < 400:
                    return Verification(True, url, final_url, expected_title, "", status, "可访问文档")
        except Exception:
            pass
    try:
        req = urllib.request.Request(url, headers={**headers, "Range": "bytes=0-80000"}, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = str(resp.geturl() or url)
            status = int(getattr(resp, "status", 200) or 200)
            content = resp.read(80_000)
            ctype = str(resp.headers.get("Content-Type") or "").lower()
        if "text/html" not in ctype and "application/xhtml" not in ctype:
            if "pdf" in ctype or final_url.lower().endswith(".pdf"):
                return Verification(True, url, final_url, expected_title, "", status, "可访问文档")
            return Verification(False, url, final_url, "", "", status, "非可验证 HTML 来源")
        decoded = content.decode("utf-8", errors="replace")
        actual_title, canonical, description = _extract_meta(decoded, final_url)
        resolved = canonical or final_url
        if is_aggregator_host(resolved) or is_error_page(actual_title, description):
            return Verification(False, url, resolved, actual_title, description, status, "聚合页或系统错误页，不作为真实来源")
        if actual_title and expected_title:
            sim = title_similarity(expected_title, actual_title)
            exp = _tokenize(expected_title)
            act = _tokenize(actual_title)
            compact_exp = re.sub(r"\W+", "", expected_title.lower())
            compact_act = re.sub(r"\W+", "", actual_title.lower())
            if sim < 0.16 and compact_exp[:14] not in compact_act and compact_act[:14] not in compact_exp:
                return Verification(False, url, resolved, actual_title, "", status, f"网页标题与检索标题关联度过低({sim:.2f})")
        return Verification(True, url, resolved, actual_title, description, status, "网页可访问")
    except urllib.error.HTTPError as exc:
        return Verification(False, url, str(getattr(exc, 'url', '') or url), "", "", int(exc.code), f"HTTP {exc.code}")
    except Exception as exc:
        return Verification(False, url, "", "", "", 0, str(exc)[:160])


def _can_keep_indexed(row: dict[str, Any], verification: Verification | None = None) -> bool:
    """Keep a provider-indexed result when origin-page crawling is inconclusive.

    Chinese government, media and academic sites frequently reject automated
    HEAD/range requests even though the user can open the article normally. A
    connectivity/anti-bot failure must not erase a high-relevance real URL. We
    still reject aggregators, title mismatches, error pages and dead documents.
    """
    url = str(row.get("url") or "").strip()
    title = str(row.get("title") or "").strip()
    snippet = str(row.get("snippet") or "").strip()
    if not is_http_url(url) or is_aggregator_host(url) or len(title) < 5:
        return False
    if verification is not None:
        reason = str(verification.reason or "").lower()
        hard_fail = ("关联度过低", "聚合", "系统错误页", "无效", "http 404", "http 410")
        if any(marker in reason for marker in hard_fail):
            return False
    relevance = float(row.get("relevance") or 0)
    query_match = int(row.get("queryMatchScore") or 0)
    authority = int(row.get("authorityScore") or 0)
    provider = str(row.get("provider") or "")
    return provider in {"tavily", "serper"} and len(snippet) >= 16 and (
        (query_match >= 24 and relevance >= 0.20)
        or (query_match >= 20 and authority >= 78)
        or (query_match >= 18 and authority >= 92)
    )


def _indexed_copy(row: dict[str, Any], verification: Verification | None = None) -> dict[str, Any]:
    copy = dict(row)
    copy["sourceVerified"] = False
    copy["sourceUsable"] = True
    copy["sourceStatus"] = "indexed"
    copy["sourceConfidence"] = "search-index"
    copy["sourceUrl"] = str(row.get("url") or "")
    if verification is not None:
        copy["verificationNote"] = str(verification.reason or "")[:120]
    return copy


def verify_results(results: list[dict[str, Any]], *, limit: int = 14) -> tuple[list[dict[str, Any]], list[str]]:
    candidates = []
    for row in results:
        if not isinstance(row, dict):
            continue
        if row.get("type") == "upload":
            candidates.append(row)
            continue
        url = str(row.get("url") or "")
        if is_http_url(url) and not is_aggregator_host(url):
            candidates.append(row)
    if not candidates:
        return [], ["没有找到可验证的真实来源。"]

    to_verify = candidates[: max(1, limit)]
    remaining = candidates[max(1, limit):]
    accepted: list[tuple[int, dict[str, Any]]] = []
    warnings: list[str] = []
    positions = {id(row): index for index, row in enumerate(candidates)}
    with ThreadPoolExecutor(max_workers=min(10, len(to_verify)), thread_name_prefix="source-verify") as pool:
        jobs = {pool.submit(verify_url, str(row.get("url") or ""), str(row.get("title") or "")): row for row in to_verify if row.get("type") != "upload"}
        for row in to_verify:
            if row.get("type") == "upload":
                copy = dict(row)
                copy["sourceVerified"] = True
                copy["sourceUsable"] = True
                copy["sourceStatus"] = "local"
                accepted.append((positions[id(row)], copy))
        for fut in as_completed(jobs):
            row = jobs[fut]
            try:
                v = fut.result()
            except Exception as exc:
                if _can_keep_indexed(row):
                    accepted.append((positions[id(row)], _indexed_copy(row)))
                continue
            if not v.ok:
                if _can_keep_indexed(row, v):
                    accepted.append((positions[id(row)], _indexed_copy(row, v)))
                continue
            copy = dict(row)
            copy["url"] = v.final_url or v.url
            copy["sourceUrl"] = copy["url"]
            copy["sourceVerified"] = True
            copy["sourceUsable"] = True
            copy["sourceStatus"] = "verified"
            copy["sourceConfidence"] = "origin-page"
            if v.title:
                copy["verifiedTitle"] = v.title[:220]
            if v.description:
                copy["verifiedDescription"] = v.description[:1200]
            accepted.append((positions[id(row)], copy))
    for row in remaining:
        if row.get("type") == "upload":
            copy = dict(row)
            copy.update({"sourceVerified": True, "sourceUsable": True, "sourceStatus": "local"})
            accepted.append((positions[id(row)], copy))
        elif _can_keep_indexed(row):
            accepted.append((positions[id(row)], _indexed_copy(row)))
    accepted.sort(key=lambda item: item[0])
    rows = [row for _, row in accepted]
    if not rows:
        warnings.append("当前条件下没有找到主题匹配且可直接定位原文的资料。")
    return rows, warnings
