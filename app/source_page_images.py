from __future__ import annotations

import json
import re
import urllib.request
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from .cache import TTLCache
from .image_fetch import _SafeRedirectHandler, _validate_public_http_url

_MAX_HTML_BYTES = 1_600_000
_PAGE_CACHE = TTLCache(max_items=180, ttl_seconds=30 * 60)
_BAD_HINTS = (
    "logo", "favicon", "avatar", "qrcode", "qr-code", "qr_code", "sprite", "icon", "banner",
    "advert", "ads/", "share", "wechat_qr", "wechat-qr", "weixin", "wx-code", "wxcode",
    "footer", "header-logo", "follow-us", "follow_qr",
)
_BAD_TEXT_HINTS = (
    "二维码", "扫码", "扫一扫", "关注公众号", "微信公众号", "关注我们", "微信扫码", "加微信",
    "qr code", "qrcode", "wechat", "weixin", "follow us",
)


class _ImageMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta_images: list[str] = []
        self.content_images: list[dict[str, Any]] = []
        self._in_script = False
        self._script_type = ""
        self._script_buf: list[str] = []
        self.json_ld: list[str] = []
        # Semantic containers are cheap but useful signals. A large image inside
        # <article>/<main>/<figure> is much more likely to be the report/news image
        # than a same-sized recommendation card in the footer.
        self._article_depth = 0
        self._main_depth = 0
        self._figure_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {str(k).lower(): str(v or "") for k, v in attrs}
        tag = tag.lower()
        if tag == "article":
            self._article_depth += 1
        elif tag == "main":
            self._main_depth += 1
        elif tag == "figure":
            self._figure_depth += 1

        if tag == "meta":
            key = (data.get("property") or data.get("name") or "").lower()
            if key in {"og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"}:
                if data.get("content"):
                    self.meta_images.append(data["content"])
        elif tag == "link":
            rel = data.get("rel", "").lower()
            if "image_src" in rel and data.get("href"):
                self.meta_images.append(data["href"])
        elif tag in {"img", "source"}:
            # Modern Chinese media pages frequently lazy-load the real image via
            # srcset/data-srcset while src is only a 1px placeholder. Prefer the
            # largest advertised srcset member before falling back to src/data-src.
            src = _best_srcset_url(data.get("srcset") or data.get("data-srcset") or "")
            if not src:
                src = (
                    data.get("src") or data.get("data-src") or data.get("data-original")
                    or data.get("data-original-src") or data.get("data-lazy-src") or data.get("data-url")
                )
            if not src:
                return
            try:
                width = int(re.sub(r"\D", "", data.get("width", "")) or 0)
            except ValueError:
                width = 0
            try:
                height = int(re.sub(r"\D", "", data.get("height", "")) or 0)
            except ValueError:
                height = 0
            alt = (data.get("alt") or data.get("title") or "").strip()
            cls = f"{data.get('class', '')} {data.get('id', '')}".lower()
            hygiene_text = f"{src} {alt} {cls}".lower()
            # Many Chinese media pages use opaque CDN filenames for follow/QR
            # cards. The alt/class/id often still says 扫码/公众号; reject those
            # before they are allowed to compete with the actual article photo.
            if any(token in hygiene_text for token in _BAD_HINTS + _BAD_TEXT_HINTS):
                return
            score = 0
            if width >= 600 or height >= 360:
                score += 3
            elif width >= 420 or height >= 260:
                score += 2
            if any(x in cls for x in ("article", "content", "figure", "photo", "news", "main", "editor", "rich_media")):
                score += 2
            if self._article_depth or self._main_depth:
                score += 2
            if self._figure_depth:
                score += 2
            if len(alt) >= 4:
                score += 1
            src_lower = src.lower()
            if any(x in src_lower for x in ("/upload", "/uploads", "/image", "/images", "/photo", "/news/", ".jpg", ".jpeg", ".png", ".webp")):
                score += 1
            if score >= 2:
                self.content_images.append({
                    "url": src, "alt": alt, "htmlScore": score,
                    "widthHint": width, "heightHint": height,
                })
        elif tag == "script":
            script_type = data.get("type", "").lower()
            if "ld+json" in script_type:
                self._in_script = True
                self._script_type = script_type
                self._script_buf = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "script" and self._in_script:
            value = "".join(self._script_buf).strip()
            if value:
                self.json_ld.append(value)
            self._in_script = False
            self._script_type = ""
            self._script_buf = []
        if lowered == "article" and self._article_depth:
            self._article_depth -= 1
        elif lowered == "main" and self._main_depth:
            self._main_depth -= 1
        elif lowered == "figure" and self._figure_depth:
            self._figure_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._script_buf.append(data)


def _best_srcset_url(value: str) -> str:
    """Return the largest candidate from an HTML srcset string.

    Width descriptors (``1200w``) and density descriptors (``2x``) are both
    supported. Broken members are ignored; the last URL remains a safe fallback
    when descriptors are missing.
    """
    best_url = ""
    best_weight = -1.0
    for index, raw in enumerate(str(value or "").split(",")):
        part = raw.strip()
        if not part:
            continue
        bits = part.split()
        url = bits[0].strip()
        if not url:
            continue
        weight = float(index + 1)
        if len(bits) > 1:
            desc = bits[-1].lower()
            try:
                if desc.endswith("w"):
                    weight = float(desc[:-1])
                elif desc.endswith("x"):
                    weight = float(desc[:-1]) * 1000.0
            except ValueError:
                pass
        if weight >= best_weight:
            best_url, best_weight = url, weight
    return best_url


def discover_source_images(url: str, *, timeout: float = 4.5, limit: int = 10) -> list[dict[str, Any]]:
    """Discover likely article images from a cited source page without another search API call.

    We prioritize OpenGraph/Twitter/JSON-LD hero images and then a small set of
    content-like ``<img>`` elements. Every returned URL still goes through the
    normal semantic and download checks in ``visuals.py``.
    """
    value = str(url or "").strip()
    if not value:
        return []
    cached = _PAGE_CACHE.get(value)
    if cached is not None:
        return list(cached)
    _validate_public_http_url(value)
    req = urllib.request.Request(
        value,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.7",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            "Accept-Encoding": "identity",
        },
    )
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    with opener.open(req, timeout=timeout) as response:
        final_url = response.geturl()
        _validate_public_http_url(final_url)
        content_type = str(response.headers.get_content_type() or "").lower()
        if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
            _PAGE_CACHE.put(value, [])
            return []
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read(_MAX_HTML_BYTES + 1)
    if len(raw) > _MAX_HTML_BYTES:
        raw = raw[:_MAX_HTML_BYTES]
    html = raw.decode(charset, errors="replace")
    parser = _ImageMetaParser()
    try:
        parser.feed(html)
    except Exception:
        pass

    rows: list[dict[str, Any]] = []
    for img in parser.meta_images:
        rows.append({"url": img, "alt": "", "htmlScore": 8, "origin": "page-meta"})
    for blob in parser.json_ld[:8]:
        for img in _json_ld_images(blob):
            rows.append({"url": img, "alt": "", "htmlScore": 7, "origin": "json-ld"})
    for item in parser.content_images[:18]:
        rows.append({**item, "origin": "content-img"})

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    base = final_url or value
    for row in rows:
        absolute = urljoin(base, str(row.get("url") or "").strip())
        if not absolute.startswith(("http://", "https://")):
            continue
        lowered = absolute.lower()
        desc = str(row.get("alt") or "").lower()
        if absolute in seen or any(token in lowered for token in _BAD_HINTS) or any(token in desc for token in _BAD_TEXT_HINTS):
            continue
        seen.add(absolute)
        out.append({
            "url": absolute,
            "description": str(row.get("alt") or "")[:240],
            "htmlScore": int(row.get("htmlScore") or 0),
            "origin": str(row.get("origin") or "page"),
            "widthHint": int(row.get("widthHint") or 0),
            "heightHint": int(row.get("heightHint") or 0),
        })
        if len(out) >= max(1, min(int(limit or 10), 16)):
            break
    _PAGE_CACHE.put(value, out)
    return list(out)


def _json_ld_images(blob: str) -> list[str]:
    try:
        data = json.loads(blob)
    except Exception:
        # Some sites concatenate JSON-LD with comments/trailing text. A narrow
        # regex still recovers common image/imageUrl values without parsing HTML.
        return re.findall(r'"(?:image|imageUrl|thumbnailUrl)"\s*:\s*"(https?://[^"\\]+)', blob)[:8]
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"image", "imageUrl", "thumbnailUrl", "contentUrl"}:
                    collect(child)
                elif isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    def collect(value: Any) -> None:
        if isinstance(value, str):
            value = value.strip()
            if value and not value.startswith(("data:", "javascript:")):
                found.append(value)
        elif isinstance(value, dict):
            for key in ("url", "contentUrl", "thumbnailUrl"):
                collect(value.get(key))
        elif isinstance(value, list):
            for child in value:
                collect(child)

    walk(data)
    return list(dict.fromkeys(found))[:12]
