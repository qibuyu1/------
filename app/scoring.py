from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from urllib.parse import urlparse


OFFICIAL_HINTS = (
    ".gov.cn",
    "gov.cn",
    "ndrc.gov.cn",
    "cac.gov.cn",
    "miit.gov.cn",
    "stats.gov.cn",
    "samr.gov.cn",
    "pbc.gov.cn",
)
ACADEMIC_HINTS = ("doi.org", "arxiv.org", ".edu", ".ac.cn")
MAJOR_MEDIA_HINTS = (
    "xinhuanet.com",
    "people.com.cn",
    "cnr.cn",
    "cctv.com",
    "caixin.com",
    "yicai.com",
    "thepaper.cn",
    "36kr.com",
)


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def authority_score(url: str, source_type: str) -> int:
    domain = _domain(url)
    if source_type == "paper" or any(h in domain for h in ACADEMIC_HINTS):
        return 94
    if any(h in domain for h in OFFICIAL_HINTS):
        return 98
    if any(h in domain for h in MAJOR_MEDIA_HINTS):
        return 88
    if domain:
        return 72
    return 60


def freshness_score(published_at: str | None) -> int:
    if not published_at:
        return 58
    text = published_at.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return 58
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = max(0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).days)
    if days <= 2:
        return 100
    if days <= 7:
        return 94
    if days <= 30:
        return 86
    if days <= 90:
        return 76
    if days <= 365:
        return 64
    return 48


def citation_score(citations: int | None) -> int:
    if not citations:
        return 50
    return min(100, int(45 + 12 * math.log10(max(1, citations))))


def overall_score(*, relevance: float | int | None, authority: int, freshness: int, source_type: str, citations: int | None = None) -> int:
    rel = 70
    if relevance is not None:
        rel = int(float(relevance) * 100) if float(relevance) <= 1 else int(float(relevance))
    if source_type == "paper":
        cit = citation_score(citations)
        score = rel * 0.43 + authority * 0.29 + cit * 0.28
    else:
        score = rel * 0.46 + authority * 0.31 + freshness * 0.23
    return max(1, min(100, round(score)))


def normalize_title(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", (value or "").lower())
