from __future__ import annotations

import base64
import io
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image

from .cache import TTLCache


MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp"}
_IMAGE_CACHE = TTLCache(max_items=96, ttl_seconds=30 * 60)


class ImageFetchError(RuntimeError):
    pass


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass
class FetchedImage:
    data: bytes
    content_type: str
    final_url: str


def fetch_image(url: str, *, timeout: float = 12.0) -> FetchedImage:
    """Fetch an image defensively and reuse validated downloads for export."""
    value = str(url or "").strip()
    cached = _IMAGE_CACHE.get(value) if value else None
    if cached is not None:
        return cached
    if value.startswith("data:image/"):
        image = _decode_data_uri(value)
        _IMAGE_CACHE.put(value, image)
        return image
    _validate_public_http_url(value)
    request = Request(
        value,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; DataElementGovernance/8.0; +https://localhost)",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        },
    )
    opener = build_opener(_SafeRedirectHandler())
    with opener.open(request, timeout=timeout) as response:
        final_url = response.geturl()
        _validate_public_http_url(final_url)
        content_type = str(response.headers.get_content_type() or "").lower()
        data = response.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageFetchError("image exceeds size limit")
    if content_type == "image/svg+xml":
        raise ImageFetchError("SVG 图片不作为真实文章配图，避免用伪造转换结果替代原图")
    if content_type not in ALLOWED_CONTENT_TYPES:
        # Some CDNs return octet-stream; verify with Pillow before accepting.
        try:
            with Image.open(io.BytesIO(data)) as im:
                fmt = (im.format or "PNG").upper()
                content_type = Image.MIME.get(fmt, "image/png")
        except Exception as exc:
            raise ImageFetchError(f"remote resource is not a supported image ({content_type or 'unknown'})") from exc
    image = FetchedImage(_normalize_raster(data), "image/png", final_url)
    _IMAGE_CACHE.put(value, image)
    return image


def image_bytes_for_document(url: str, *, label: str = "文章配图") -> bytes:
    """Return real image bytes only; never synthesize a fake export image."""
    return fetch_image(url).data


def _validate_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ImageFetchError("only public http/https image URLs are allowed")
    host = parsed.hostname
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ImageFetchError("image host could not be resolved") from exc
    if not infos:
        raise ImageFetchError("image host could not be resolved")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ImageFetchError("private or reserved network image URL rejected")


def _decode_data_uri(uri: str) -> FetchedImage:
    header, sep, payload = uri.partition(",")
    if not sep:
        raise ImageFetchError("invalid data URI")
    media = header[5:].split(";", 1)[0].lower()
    if media == "image/svg+xml":
        raise ImageFetchError("SVG 数据图片不作为文章配图")
    try:
        raw = base64.b64decode(payload) if ";base64" in header else unquote(payload).encode("latin1")
    except Exception as exc:
        raise ImageFetchError("invalid image data URI") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageFetchError("image exceeds size limit")
    return FetchedImage(_normalize_raster(raw), "image/png", uri)


def _normalize_raster(data: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            image = image.convert("RGB")
            # Keep exports reasonably small while preserving a crisp 16:9-ish visual.
            max_width = 1800
            if image.width > max_width:
                height = max(1, int(image.height * max_width / image.width))
                image = image.resize((max_width, height), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True)
            return output.getvalue()
    except Exception as exc:
        raise ImageFetchError("image bytes could not be decoded") from exc



@dataclass
class ImageProfile:
    width: int
    height: int
    aspect: float
    fingerprint: str
    usable: bool
    # Non-editorial assets can be large enough to pass ordinary dimension checks.
    # Keep the reason so ranking/debug UIs can explain why an image was rejected.
    artifact: str = ""


def image_profile(url: str, *, timeout: float = 7.0) -> ImageProfile:
    """Inspect a candidate image before it is selected for an article.

    The profile is intentionally lightweight: dimensions reject tiny icons and
    extreme banners, while a small difference-hash keeps visually identical
    images from being reused under different CDN URLs.
    """
    value = str(url or "").strip()
    lowered = value.lower()
    if not value or any(token in lowered for token in ("favicon", "sprite", "avatar", "logo", "icon", "placeholder", "default-image")):
        return ImageProfile(0, 0, 0.0, "", False)
    if lowered.endswith(".svg") or "image/svg+xml" in lowered:
        return ImageProfile(0, 0, 0.0, "", False)
    try:
        fetched = fetch_image(value, timeout=timeout)
        with Image.open(io.BytesIO(fetched.data)) as image:
            image.load()
            width, height = image.size
            aspect = width / max(1, height)
            # Reject icon-like assets and pathological strips. Editorial images
            # are usually landscape, but portrait figures remain allowed.
            usable = width >= 420 and height >= 230 and 0.55 <= aspect <= 3.2
            artifact = _visual_artifact_reason(image) if usable else ""
            if artifact:
                usable = False
            gray = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            pixels = list(gray.get_flattened_data()) if hasattr(gray, "get_flattened_data") else list(gray.getdata())
            bits = []
            for y in range(8):
                row = pixels[y * 9:(y + 1) * 9]
                bits.extend(1 if row[x] > row[x + 1] else 0 for x in range(8))
            number = 0
            for bit in bits:
                number = (number << 1) | bit
            fingerprint = f"{number:016x}"
            return ImageProfile(width, height, aspect, fingerprint, usable, artifact)
    except Exception:
        return ImageProfile(0, 0, 0.0, "", False)

def _visual_artifact_reason(image: Image.Image) -> str:
    """Detect large non-editorial assets that filename/metadata filters miss.

    News pages often expose a WeChat/QR follow card as ``og:image`` or as one of
    the first large content images. Its URL can be a meaningless CDN hash, so text
    filters cannot protect the article. QR finder patterns have a very distinctive
    1:1:3:1:1 black/white run-length signature; detecting that signature is cheap
    (Pillow only) and avoids adding OCR, OpenCV, API calls or model tokens.
    """
    try:
        width, height = image.size
        aspect = width / max(1, height)
        # QR codes and follow cards are overwhelmingly close to square. Do not run
        # the stronger binary-pattern test on normal editorial landscape photos.
        if not (0.72 <= aspect <= 1.38):
            return ""
        gray = image.convert("L")
        scale = min(1.0, 288.0 / max(gray.size))
        if scale < 1.0:
            gray = gray.resize((max(48, int(gray.width * scale)), max(48, int(gray.height * scale))), Image.Resampling.BILINEAR)
        histogram = gray.histogram()
        total = sum(histogram)
        if not total:
            return ""
        # QR modules are mostly near black/near white even when a small coloured
        # logo is embedded in the centre. Histogram arithmetic stays in C/Pillow
        # as much as possible and is cheaper than sorting every candidate's pixels.
        near_binary = (sum(histogram[:49]) + sum(histogram[207:])) / total
        if near_binary < 0.66:
            return ""

        def percentile(level: float) -> int:
            target = total * level
            acc = 0
            for value, count in enumerate(histogram):
                acc += count
                if acc >= target:
                    return value
            return 255

        threshold = (percentile(0.25) + percentile(0.75)) / 2.0
        threshold = min(180.0, max(78.0, threshold))
        pixels = list(gray.get_flattened_data()) if hasattr(gray, "get_flattened_data") else list(gray.getdata())
        w, h = gray.size
        binary = [1 if px < threshold else 0 for px in pixels]

        def line_matches(seq: list[int]) -> int:
            runs: list[tuple[int, int]] = []
            last = seq[0]
            length = 1
            for value in seq[1:]:
                if value == last:
                    length += 1
                else:
                    runs.append((last, length)); last = value; length = 1
            runs.append((last, length))
            matches = 0
            for i in range(len(runs) - 4):
                colors = [runs[i+j][0] for j in range(5)]
                if colors != [1, 0, 1, 0, 1]:
                    continue
                lens = [runs[i+j][1] for j in range(5)]
                unit = (lens[0] + lens[1] + lens[3] + lens[4]) / 4.0
                if unit < 1.0:
                    continue
                ratios = [lens[0]/unit, lens[1]/unit, lens[2]/unit, lens[3]/unit, lens[4]/unit]
                if all(0.45 <= ratios[j] <= 1.75 for j in (0, 1, 3, 4)) and 2.0 <= ratios[2] <= 4.4:
                    matches += 1
            return matches

        # Sample every second row/column. A real finder square produces repeated
        # signatures across neighbouring scan lines; random text rarely does.
        row_hits = 0
        for y in range(0, h, 2):
            row_hits += line_matches(binary[y*w:(y+1)*w])
        col_hits = 0
        for x in range(0, w, 2):
            col_hits += line_matches([binary[y*w+x] for y in range(h)])
        finder_hits = row_hits + col_hits

        # A second signal catches QR variants whose finder ratios are softened by
        # resampling: dense black/white transitions over a square, near-binary image.
        transitions = 0
        samples = 0
        step_y = max(1, h // 64)
        step_x = max(1, w // 64)
        for y in range(0, h, step_y):
            seq = binary[y*w:(y+1)*w]
            transitions += sum(1 for a, b in zip(seq, seq[1:]) if a != b)
            samples += max(0, len(seq)-1)
        for x in range(0, w, step_x):
            seq = [binary[y*w+x] for y in range(h)]
            transitions += sum(1 for a, b in zip(seq, seq[1:]) if a != b)
            samples += max(0, len(seq)-1)
        transition_density = transitions / max(1, samples)
        if finder_hits >= 10 or (finder_hits >= 5 and near_binary >= 0.76 and transition_density >= 0.16):
            return "qr-code"
        return ""
    except Exception:
        # Artifact detection is a quality gate, not a reason to break all image
        # handling if a malformed raster reaches this helper.
        return ""

