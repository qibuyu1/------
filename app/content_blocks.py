from __future__ import annotations

import re
from typing import Any

from .scoring import normalize_title


HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
LIST_RE = re.compile(r"^[-*]\s+(.+?)\s*$")


def markdown_to_blocks(markdown: str) -> list[dict[str, Any]]:
    """Convert the small Markdown subset used by the editor into typed blocks."""
    lines = str(markdown or "").replace("\r", "").split("\n")
    blocks: list[dict[str, Any]] = []
    paragraph: list[str] = []
    bullets: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            text = " ".join(x.strip() for x in paragraph if x.strip()).strip()
            if text:
                blocks.append({"type": "paragraph", "text": text})
            paragraph = []

    def flush_bullets() -> None:
        nonlocal bullets
        if bullets:
            blocks.append({"type": "bullets", "items": bullets[:]})
            bullets = []

    for raw in lines:
        line = raw.strip()
        if not line:
            flush_paragraph()
            flush_bullets()
            continue
        heading = HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            flush_bullets()
            blocks.append({"type": "heading", "level": len(heading.group(1)), "text": heading.group(2).strip()})
            continue
        bullet = LIST_RE.match(line)
        if bullet:
            flush_paragraph()
            bullets.append(bullet.group(1).strip())
            continue
        paragraph.append(line)

    flush_paragraph()
    flush_bullets()
    return blocks


def headings_from_markdown(markdown: str) -> list[str]:
    return [b["text"] for b in markdown_to_blocks(markdown) if b.get("type") == "heading"]


def plan_visual_slots(article: dict[str, Any], query: str, *, max_body: int = 3) -> list[dict[str, str]]:
    md = str(article.get("markdown") or "")
    blocks = markdown_to_blocks(md)
    headings = [b["text"] for b in blocks if b.get("type") == "heading"]
    image_queries = [str(x).strip() for x in (article.get("imageQueries") or []) if str(x).strip()]
    raw_slots = article.get("imageSlots") if isinstance(article.get("imageSlots"), list) else []
    cover_title = str(article.get("recommendedTitle") or (article.get("titleCandidates") or [query])[0] or query).strip()
    cover_brief = str(article.get("coverBrief") or "").strip()
    cover_query = " ".join(x for x in [cover_title, query, image_queries[0] if image_queries else ""] if x)[:220]
    slots: list[dict[str, str]] = [{
        "slotId": "cover", "kind": "cover", "afterHeading": "", "anchorText": "", "anchorBlockIndex": "",
        "purpose": cover_brief or "与文章核心主题直接相关的封面视觉", "query": cover_query or query,
        "coverTitle": cover_title, "coverBrief": cover_brief,
    }]
    used_slots = set(); query_cursor = 1
    first_paragraph_by_heading: dict[str, tuple[str, str]] = {}
    current_heading = ""
    for block_index, block in enumerate(blocks):
        if block.get("type") == "heading":
            current_heading = str(block.get("text") or "")
        elif block.get("type") == "paragraph" and current_heading and current_heading not in first_paragraph_by_heading:
            first_paragraph_by_heading[current_heading] = (str(block.get("text") or "")[:220], str(block_index))
    for raw in raw_slots:
        if len([x for x in slots if x.get("kind")=="body"]) >= max_body or not isinstance(raw, dict):
            break
        requested_heading = str(raw.get("afterHeading") or "").strip()
        matched = _match_heading(requested_heading, headings, {x.get("afterHeading") for x in slots})
        if not matched:
            continue
        slot_query = str(raw.get("query") or "").strip()
        if not slot_query and query_cursor < len(image_queries):
            slot_query = image_queries[query_cursor]; query_cursor += 1
        anchor_text, anchor_index = first_paragraph_by_heading.get(matched, ("", ""))
        try:
            source_id = str(max(0, int(raw.get("sourceId") or 0)))
        except (TypeError, ValueError):
            source_id = "0"
        visual_intent = str(raw.get("visualIntent") or "auto").strip().lower()
        if visual_intent not in {"auto", "real", "diagram"}:
            visual_intent = "auto"
        visual_type = str(raw.get("visualType") or "").strip().lower()
        visual_plan = raw.get("visualPlan") if isinstance(raw.get("visualPlan"), dict) else {}
        slots.append({"slotId": f"body-{len(slots)}", "kind": "body", "afterHeading": matched, "anchorText": anchor_text, "anchorBlockIndex": anchor_index, "purpose": str(raw.get("purpose") or "解释本节核心信息")[:160], "query": slot_query or f"{query} {matched} {anchor_text[:70]}", "sourceId": source_id if source_id != "0" else "", "placement": "heading", "visualIntent": visual_intent, "visualType": visual_type, "visualPlan": visual_plan})

    candidates: list[dict[str, str]] = []
    current_heading = ""
    for block_index, block in enumerate(blocks):
        if block.get("type") == "heading":
            current_heading = str(block.get("text") or "")
            continue
        if block.get("type") != "paragraph" or "参考来源" in current_heading:
            continue
        text = str(block.get("text") or "").strip()
        compact = re.sub(r"\s+", "", text)
        if len(compact) < 24:
            continue
        sentence_parts = [x.strip() for x in re.split(r"(?<=[。！？；])", text) if x.strip()]
        # Keep trailing citation markers with the sentence they support.  Without
        # this merge, a paragraph ending in "。[2]" was split into a sentence and
        # a tiny standalone "[2]" fragment, so the visual router lost the strongest
        # local signal for returning to source #2's original image.
        sentences: list[str] = []
        for part in sentence_parts:
            if sentences and re.fullmatch(r"(?:\[\d+\]\s*)+", part):
                sentences[-1] = f"{sentences[-1]}{part}"
            else:
                sentences.append(part)
        units = [x for x in sentences if len(re.sub(r"\s+", "", x)) >= 18] or [text]
        for unit in units:
            candidates.append({"heading": current_heading, "anchorText": unit[:140], "blockIndex": str(block_index)})

    need = max(0, max_body - len([x for x in slots if x.get("kind")=="body"]))
    for idx in _spread_indexes(len(candidates), need):
        c = candidates[idx]
        key = f"{normalize_title(c['heading'])}|{normalize_title(c['anchorText'][:60])}|{c['blockIndex']}"
        if key in used_slots:
            continue
        used_slots.add(key)
        model_query = image_queries[query_cursor] if query_cursor < len(image_queries) else ""
        query_cursor += 1
        slot_query = " ".join(x for x in [query, c["heading"], c["anchorText"][:90], model_query] if x)[:220]
        slots.append({"slotId": f"body-{len(slots)}", "kind": "body", "afterHeading": c["heading"], "anchorText": c["anchorText"], "anchorBlockIndex": c["blockIndex"], "purpose": "与该段核心信息直接相关的视觉解释", "query": slot_query, "placement": "paragraph", "visualIntent": "auto", "visualType": "", "visualPlan": {}})
    return slots[:max_body+1]


def merge_visuals_into_blocks(markdown: str, visuals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks = markdown_to_blocks(markdown)
    body_visuals = [v for v in visuals if v.get("kind") == "body" and v.get("image")]
    if not body_visuals:
        return blocks
    by_heading: dict[str, list[dict[str, Any]]] = {}
    by_anchor: dict[str, list[dict[str, Any]]] = {}
    by_index: dict[int, list[dict[str, Any]]] = {}
    for visual in body_visuals:
        h = normalize_title(str(visual.get("afterHeading") or ""))
        a = normalize_title(str(visual.get("anchorText") or ""))
        bi = visual.get("anchorBlockIndex")
        # Model-authored slots deliberately mean“after this heading”; keep that
        # editorial intent. Automatically spread fallback slots, however, should
        # follow their concrete paragraph index so several images in one section
        # do not stack directly under the same heading.
        if str(visual.get("placement") or "") == "heading" and h:
            by_heading.setdefault(h, []).append(visual)
            continue
        try:
            if bi not in (None, ""):
                by_index.setdefault(int(bi), []).append(visual)
                continue
        except (TypeError, ValueError):
            pass
        if a:
            by_anchor.setdefault(a[:60], []).append(visual)
        elif h:
            by_heading.setdefault(h, []).append(visual)
    used: set[str] = set()
    merged: list[dict[str, Any]] = []
    for idx, block in enumerate(blocks):
        merged.append(block)
        exact = by_index.get(idx, [])
        for visual in exact:
            slot = str(visual.get("slotId") or "")
            if slot and slot not in used:
                merged.append(_image_block(visual)); used.add(slot)
        if block.get("type") == "heading":
            key = normalize_title(str(block.get("text") or ""))
            for visual in by_heading.pop(key, []):
                slot = str(visual.get("slotId") or "")
                if slot and slot not in used:
                    merged.append(_image_block(visual)); used.add(slot)
        elif block.get("type") == "paragraph":
            key = normalize_title(str(block.get("text") or "")[:120])
            for visual in by_anchor.pop(key[:60], []):
                slot = str(visual.get("slotId") or "")
                if slot and slot not in used:
                    merged.append(_image_block(visual)); used.add(slot)
    return merged


def _image_block(visual: dict[str, Any]) -> dict[str, Any]:
    image = dict(visual.get("image") or {})
    return {"type": "image", "slotId": visual.get("slotId"), "url": image.get("url"), "description": image.get("description") or visual.get("purpose") or "文章配图", "caption": _caption_for(image, visual), "source": image.get("source") or "", "sourceUrl": image.get("sourceUrl") or "", "query": visual.get("query") or "", "purpose": visual.get("purpose") or ""}


def _caption_for(image: dict[str, Any], visual: dict[str, Any]) -> str:
    if str(image.get("provider") or "") == "generated-cover":
        return ""
    description = " ".join(str(image.get("description") or visual.get("purpose") or "文章配图").split()).strip()
    if len(description) > 82:
        description = description[:80].rstrip("，。；：、 ") + "…"
    source = str(image.get("source") or "").strip()
    if source:
        return f"{description} · 来源：{source}"[:180]
    return description[:120]


def _match_heading(requested: str, headings: list[str], used: set[str]) -> str:
    if not headings:
        return ""
    req = normalize_title(requested)
    if req:
        for heading in headings:
            if heading in used:
                continue
            norm = normalize_title(heading)
            if req == norm or req in norm or norm in req:
                return heading
    for heading in headings:
        if heading not in used and "参考来源" not in heading:
            return heading
    return ""


def _spread_indexes(total: int, count: int) -> list[int]:
    if total <= 0 or count <= 0:
        return []
    target = min(total, count)
    if target == 1:
        return [min(total - 1, 1 if total > 1 else 0)]
    # Integer quantiles guarantee exactly ``min(total, count)`` distinct indexes.
    # The previous round()-based formula could collapse 3 requested placements
    # into only 2 when there were 4 candidate paragraphs, silently lowering the
    # image plan before image search even started.
    return [min(total - 1, int((i + 1) * total / (target + 1))) for i in range(target)]
