from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


def build_cover_data_uri(title: str, brief: str = "") -> str:
    """Create a restrained editorial cover tied to the article itself.

    This is only used when no sufficiently relevant web image survives the
    semantic/source/download checks. It is deliberately a designed cover, not a
    fabricated documentary photo: the article title is rendered on the image and
    no external scene is implied.
    """
    title = " ".join(str(title or "数据专题").split())[:84]
    brief = _clean_brief(brief)[:90]
    width, height = 1600, 900
    image = Image.new("RGB", (width, height), "#F4F8FF")
    draw = ImageDraw.Draw(image)

    # Quiet data-network geometry: decorative only, with the title carrying the
    # semantic relationship to the article.
    for x in range(0, width, 160):
        draw.line((x, 0, x, height), fill="#E6EEFB", width=1)
    for y in range(0, height, 140):
        draw.line((0, y, width, y), fill="#E6EEFB", width=1)
    nodes = [(1050, 170), (1260, 260), (1410, 145), (1180, 440), (1450, 520), (1030, 640), (1315, 725)]
    for a, b in zip(nodes, nodes[1:]):
        draw.line((*a, *b), fill="#AFC9F7", width=5)
    for x, y in nodes:
        draw.ellipse((x-11, y-11, x+11, y+11), fill="#2563EB")
        draw.ellipse((x-4, y-4, x+4, y+4), fill="#EAF2FF")

    sans_bold = _font(["NotoSansCJK-Bold.ttc", "simhei.ttf", "msyhbd.ttc", "PingFang.ttc"], 72, bold=True)
    sans = _font(["NotoSansCJK-Regular.ttc", "msyh.ttc", "PingFang.ttc"], 30)
    small = _font(["NotoSansCJK-Regular.ttc", "msyh.ttc", "PingFang.ttc"], 24)

    draw.rounded_rectangle((85, 80, 560, 132), radius=20, fill="#E7F0FF")
    draw.text((112, 91), "数据 · 治理 · 产业 · 技术", font=small, fill="#1D4ED8")

    max_text_width = 890
    lines = _wrap(draw, title, sans_bold, max_text_width, max_lines=4)
    y = 235
    for line in lines:
        draw.text((90, y), line, font=sans_bold, fill="#111827")
        y += 100

    if brief:
        brief_lines = _wrap(draw, brief, sans, 940, max_lines=2)
        y = max(y + 18, 650)
        for line in brief_lines:
            draw.text((94, y), line, font=sans, fill="#5D718A")
            y += 46

    draw.rectangle((90, 790, 270, 800), fill="#2563EB")
    draw.text((90, 820), "数治攻关 · 编辑封面", font=small, fill="#6B7F98")

    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    payload = base64.b64encode(out.getvalue()).decode("ascii")
    return f"data:image/png;base64,{payload}"


def _clean_brief(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"(?:封面|视觉|图片|场景|配图)[:：]?", "", value).strip(" ，。;；")
    return value


def _font(names: Iterable[str], size: int, *, bold: bool = False):
    candidates = []
    linux = Path("/usr/share/fonts/opentype/noto")
    windows = Path("C:/Windows/Fonts")
    mac = Path("/System/Library/Fonts")
    for name in names:
        candidates.extend([linux / name, windows / name, mac / name])
    if bold:
        candidates.insert(0, linux / "NotoSansCJK-Bold.ttc")
    else:
        candidates.insert(0, linux / "NotoSansCJK-Regular.ttc")
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size, index=2 if path.suffix.lower() == ".ttc" and "Noto" in path.name else 0)
            except Exception:
                try:
                    return ImageFont.truetype(str(path), size)
                except Exception:
                    pass
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, *, max_lines: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for ch in str(text or ""):
        candidate = current + ch
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = ch
            if len(lines) >= max_lines - 1:
                break
        else:
            current = candidate
    if current and len(lines) < max_lines:
        lines.append(current)
    consumed = "".join(lines)
    if len(consumed) < len(str(text or "")) and lines:
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines or [str(text or "数据专题")[:18]]
