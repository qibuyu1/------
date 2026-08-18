from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlparse

from . import serper_images
from .image_fetch import ImageProfile, image_profile
from .code_visuals import build_body_visual, build_cover_data_uri, visual_fit_score, code_visuals_available
from .source_page_images import discover_source_images


LATIN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]+")
HAN_RE = re.compile(r"[\u4e00-\u9fff]+")
GENERIC_TERMS = {
    "数据", "要素", "图片", "图示", "场景", "相关", "数字", "经济", "文章", "配图", "研究",
    "新闻", "政策", "产业", "中国", "报告", "治理", "平台", "信息",
}
BAD_IMAGE_HINTS = {
    "logo", "icon", "avatar", "favicon", "sprite", "placeholder", "default", "loading", "二维码",
    "头像", "图标", "站标", "水印", "壁纸", "模板", "素材合集", "背景素材", "ppt", "演示文稿",
    "qrcode", "qr code", "扫码", "扫一扫", "关注公众号", "微信公众号", "微信扫码", "加微信",
    "wechat", "weixin", "follow us", "advert", "advertisement", "广告",
}
REAL_SCENE_TERMS = {"现场", "会议", "签约", "启动", "发布", "机房", "数据中心", "服务器", "工作人员", "产业园", "平台", "展会", "研发", "项目"}
DATA_CONTEXT_TERMS = {
    "数据", "数据集", "数据分析", "数据治理", "数据管理", "数据库", "数据质量", "数据标准",
    "数据目录", "数据孤岛", "主数据", "元数据", "数据安全", "数据合规", "数据资产", "数据流通",
    "公共数据", "个人信息", "隐私保护", "数据权益", "数据平台", "可视化", "数据驱动", "数据要素",
    "模型", "算法", "机器学习", "人工智能", "AI", "大模型", "语料", "训练数据", "数据标注",
    "仿真", "数字孪生", "数字政务",
}
CONSUMER_TECH_DISTRACTIONS = {"笔记本", "显卡", "CPU", "Intel", "AMD", "手机评测", "游戏本", "装机", "跑分", "壁纸"}

# Image search needs a different ontology from document retrieval. A paragraph
# titled“数据治理如何影响普通人”should naturally reach“个人信息保护/隐私/数字政务”
# imagery, while“为什么数据治理总是失败”should reach“数据孤岛/数据质量/数据标准”
# rather than the old generic“科技研发”query. These rules are deterministic and
# therefore add no LLM tokens.

# Concrete business/industry scenes deserve their own image vocabulary. Without
# this layer a paragraph about“仓库管理员入库产生库存数据”could still accept a
# generic AI/car-recognition image merely because both pages mention 数据治理.
_SCENE_VISUAL_ROUTES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("仓库", "仓储", "库存", "入库", "出库"), (
        "仓库 库存管理 入库 数据采集",
        "仓储管理 库存数据 物流 企业",
    )),
    (("供应链", "物流", "运输", "配送"), (
        "供应链 物流 数据平台 企业",
        "物流 数据分析 运输 调度",
    )),
    (("工厂", "制造", "生产线", "车间", "设备"), (
        "智能制造 生产数据 工厂 车间",
        "工业数据 设备监测 生产线",
    )),
    (("销售", "门店", "客户", "订单", "营销"), (
        "企业 销售数据 客户管理 业务",
        "门店 订单 数据分析 经营",
    )),
    (("财务", "会计", "报表", "成本"), (
        "企业 财务数据 数据治理 报表",
        "财务分析 数据平台 企业管理",
    )),
    (("医院", "医疗", "患者", "病历"), (
        "医疗数据 医院 数据治理",
        "电子病历 医疗数据 平台",
    )),
    (("交通", "道路", "公交", "地铁"), (
        "交通数据 城市治理 调度",
        "智慧交通 数据平台 城市",
    )),
)

_LOCAL_UMBRELLA_TERMS = {
    "数据", "数据治理", "数据管理", "数据要素", "数据平台", "数字化", "信息", "治理",
    "企业", "业务", "研究", "技术", "人工智能", "AI", "大模型", "科技", "系统", "平台",
}

_VISUAL_INTENT_ROUTES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("失败", "失效", "做不好", "难落地", "困境", "问题"), (
        "数据治理 数据孤岛 数据质量 企业",
        "数据管理 数据标准 主数据 数据平台",
    )),
    (("普通人", "个人", "用户", "消费者", "市民", "公众"), (
        "数据治理 个人信息保护 隐私 数字生活",
        "公共数据 数字政务 个人信息权益 市民",
    )),
    (("业务", "经营", "企业管理", "决策"), (
        "企业数据治理 业务数据 数据平台 管理决策",
        "数据管理 企业经营 数字化 业务现场",
    )),
    (("研发", "技术突破", "技术创新", "科研", "实验"), (
        "数据驱动研发 数据平台 实验室 科研",
        "机器学习 数据模型 技术研发 真实场景",
    )),
    (("安全", "合规", "隐私", "个人信息"), (
        "数据安全 数据合规 个人信息保护 真实场景",
        "隐私保护 数据治理 网络安全 企业",
    )),
    (("公共数据", "政务", "政府", "公共服务"), (
        "公共数据 数字政务 数据开放 城市",
        "政务数据 公共服务 数据平台 市民",
    )),
    (("数据资产", "入表", "融资", "估值"), (
        "数据资产 数据入表 企业 财务",
        "数据资产 融资 数据交易 企业",
    )),
    (("可信数据空间", "数据流通", "数据交易", "数据共享"), (
        "可信数据空间 数据流通 企业 项目",
        "数据交易 数据共享 数据平台 产业",
    )),
    (("人工智能", "大模型", "训练数据", "语料", "数据标注"), (
        "人工智能 训练数据 数据标注 模型",
        "大模型 数据集 语料 数据平台",
    )),
)


def _direct_diagram_decision(slot: dict[str, Any], query: str, strategy: str) -> bool:
    """Whether this body slot should be drawn before web-image search.

    Concrete source-bound events retain provenance priority in smart mode.
    Structural/numerical explanation can skip Serper entirely.
    """
    if slot.get("kind") != "body":
        return False
    if strategy == "all_diagram":
        return True
    if strategy in {"real_first", "real_only"}:
        return False
    intent = str(slot.get("visualIntent") or "auto").strip().lower()
    if intent == "real":
        return False
    if intent == "diagram":
        return True
    has_source = bool(str(slot.get("sourceHintUrl") or "").strip()) or bool(slot.get("sourceId"))
    fit = visual_fit_score(slot, query)
    if strategy == "diagram_first":
        return fit >= (72 if has_source else 48)
    return (not has_source) and fit >= 72


def resolve_visuals(
    slots: list[dict[str, str]], query: str, *, preference: str = "", strategy: str = "smart",
    match_mode: str = "precise", source_policy: str = "balanced",
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve visuals through source-page, web-image and local drawing lanes.

    `smart` keeps concrete news/cases real-first while allowing structural,
    numerical and mechanism-heavy paragraphs to draw immediately. Mixed modes
    fall back to a local explanation graphic instead of accepting weak stock art.
    """
    warnings: list[str] = []
    if not slots:
        return [], warnings
    strategy = str(strategy or "smart").strip().lower()
    if strategy not in {"smart", "real_first", "diagram_first", "all_diagram", "real_only"}:
        strategy = "smart"
    code_ready = code_visuals_available()
    direct_diagram_ids = {
        str(slot.get("slotId") or "") for slot in slots
        if code_ready and slot.get("kind") == "body" and _direct_diagram_decision(slot, query, strategy)
    }
    serper_ready = serper_images.available()
    has_origin_images = any(
        slot.get("kind") == "body" and str(slot.get("slotId") or "") not in direct_diagram_ids and (slot.get("sourceImages") or [])
        and not _is_root_like_url(str(slot.get("sourceHintUrl") or ""))
        for slot in slots
    )
    has_source_pages = any(
        slot.get("kind") == "body" and str(slot.get("slotId") or "") not in direct_diagram_ids and str(slot.get("sourceHintUrl") or "").startswith(("http://", "https://"))
        and not _is_root_like_url(str(slot.get("sourceHintUrl") or ""))
        for slot in slots
    )
    # A cited source page is itself a zero-search-cost image lane. Do not return
    # early merely because Tavily omitted sourceImages or Serper is unavailable:
    # OpenGraph/JSON-LD/srcset discovery can still recover the real article image.
    if not serper_ready and not has_origin_images and not has_source_pages:
        visuals: list[dict[str, Any]] = []
        for slot in slots:
            if slot.get("kind") == "cover":
                visuals.append(_generated_cover_visual(slot, query) if code_ready else {**slot, "image": None, "matchedBy": "unresolved-no-cjk-font"})
            elif strategy == "real_only" or not code_ready:
                visuals.append({**slot, "image": None, "matchedBy": "unresolved-real-only" if strategy == "real_only" else "unresolved-no-cjk-font"})
            else:
                visuals.append(_generated_body_visual(slot, query, reason="no-web-image-config"))
        if not code_ready:
            message = "当前运行环境缺少可用中文字体，本地代码绘图已自动停用以避免方块字；安装 Noto CJK 后会自动恢复，文章生成不受影响。"
        else:
            message = (
                "未配置 SERPER_API_KEY，且当前没有可读取的来源页/来源原图：按当前配图策略已改用代码绘图补齐正文。"
                if strategy != "real_only" else
                "未配置 SERPER_API_KEY，且当前没有可读取的来源页/来源原图；当前选择仅真实图片，因此正文图片位保持为空。"
            )
        return visuals, [message]

    results_by_slot: dict[str, list[dict[str, Any]]] = {slot["slotId"]: [] for slot in slots}
    queries_by_slot: dict[str, set[str]] = {slot["slotId"]: set() for slot in slots}
    # First-class origin-page candidates. Selected evidence pages are extracted
    # during article generation, so their real images can be reused here without
    # another search request. Serper remains a fallback/augmentation lane.
    for slot in slots:
        if slot.get("kind") != "body" or str(slot.get("slotId") or "") in direct_diagram_ids:
            continue
        source_title = str(slot.get("sourceHint") or "").strip()
        source_name = str(slot.get("sourceName") or "").strip()
        source_url = str(slot.get("sourceHintUrl") or "").strip()
        source_snippet = str(slot.get("sourceSnippet") or "").strip()
        # A publisher homepage is not the cited article page. Treating homepage
        # assets as provenance was how follow QR cards/site promos could outrank
        # actual topic images. Root URLs now keep their title/domain for Serper
        # queries but do not donate “source-origin” images.
        origin_rows = [] if _is_root_like_url(source_url) else list(slot.get("sourceImages") or [])[:10]
        for source_pos, image_url in enumerate(origin_rows, start=1):
            image_url = str(image_url or "").strip()
            if not image_url.startswith(("http://", "https://")):
                continue
            results_by_slot[slot["slotId"]].append({
                "url": image_url,
                "fallbackUrl": "",
                "originalUrl": image_url,
                "description": source_title or str(slot.get("purpose") or "来源原图"),
                "source": source_name or _host(source_url) or "原始来源",
                "sourceUrl": source_url,
                "sourceTitle": source_title,
                "sourceSnippet": source_snippet,
                "resultScore": max(0.55, 1.0 - (source_pos - 1) * 0.06),
                "position": source_pos,
                "provider": "source-origin",
                "searchQuery": "origin-page-image",
            })
    # Source-page hero discovery and the first Serper lane run concurrently. This
    # adds a zero-search-cost way to recover the real news/case image without
    # lengthening the critical path. It also fixes a common failure mode where
    # Tavily returned the source article but omitted its OpenGraph hero image.
    network_jobs: dict[Any, tuple[str, dict[str, str], str]] = {}
    max_workers = min(10, max(4, len(slots) * 2))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="visual-search") as pool:
        for slot in slots:
            if slot.get("kind") == "cover" or str(slot.get("slotId") or "") in direct_diagram_ids:
                continue
            source_url = str(slot.get("sourceHintUrl") or "").strip()
            # Tavily already supplied concrete source-page images: probe those
            # immediately rather than waiting on another HTML request. Metadata
            # discovery is only needed when Extract omitted page images entirely.
            if source_url and not _is_root_like_url(source_url) and not slot.get("sourceImages"):
                future = pool.submit(discover_source_images, source_url, limit=8)
                network_jobs[future] = ("source-page", slot, source_url)

            if not serper_ready:
                continue
            variants = _visual_query_variants(slot, query, preference, match_mode)
            if not variants:
                variants = [_fallback_visual_query(slot, query)]
            # Keep V26's one-query fast path. A second *different* route is only
            # used later for unresolved slots, so successful slots do not spend
            # more Serper calls than before.
            variant_limit = 0 if (slot.get("sourceImages") and not _is_root_like_url(source_url)) else 1
            for visual_query in variants[:variant_limit]:
                queries_by_slot[slot["slotId"]].add(visual_query)
                future = pool.submit(_search_candidates, visual_query)
                network_jobs[future] = ("serper", slot, visual_query)

        for future in as_completed(network_jobs):
            kind, slot, value = network_jobs[future]
            try:
                if kind == "source-page":
                    source_title = str(slot.get("sourceHint") or "").strip()
                    source_name = str(slot.get("sourceName") or "").strip()
                    source_snippet = str(slot.get("sourceSnippet") or "").strip()
                    for pos, row in enumerate(future.result() or [], start=1):
                        image_url = str((row or {}).get("url") or "").strip()
                        if not image_url:
                            continue
                        results_by_slot[slot["slotId"]].append({
                            "url": image_url,
                            "fallbackUrl": "",
                            "originalUrl": image_url,
                            "description": str((row or {}).get("description") or source_title or slot.get("purpose") or "来源页图片"),
                            "source": source_name or _host(value) or "原始来源",
                            "sourceUrl": value,
                            "sourceTitle": source_title,
                            "sourceSnippet": source_snippet,
                            "resultScore": min(1.0, 0.62 + float((row or {}).get("htmlScore") or 0) * 0.045),
                            "position": pos,
                            "htmlScore": int((row or {}).get("htmlScore") or 0),
                            "origin": str((row or {}).get("origin") or "page"),
                            "width": int((row or {}).get("widthHint") or 0),
                            "height": int((row or {}).get("heightHint") or 0),
                            "provider": "source-meta",
                            "searchQuery": "source-page-metadata",
                        })
                    continue

                candidates, provider, errors = future.result()
                for error in errors:
                    warnings.append(f"Serper 配图提示：{error}")
                for candidate in candidates:
                    candidate = dict(candidate)
                    candidate.setdefault("searchQuery", value)
                    candidate.setdefault("provider", provider)
                    results_by_slot[slot["slotId"]].append(candidate)
            except Exception as exc:
                if kind == "serper":
                    warnings.append(f"配图检索“{slot.get('purpose') or slot.get('query')}”失败：{str(exc)[:120]}")

    for slot_id, candidates in list(results_by_slot.items()):
        results_by_slot[slot_id] = _dedupe_images(candidates)

    # Probe the top candidates plus their provider thumbnails/fallback URLs. This
    # detects hot-link failures before the image is committed to the article.
    probe_urls = _fair_probe_urls(
        slots, results_by_slot, query=query, source_policy=source_policy,
        match_mode=match_mode, per_slot=7, cap=32, known=set(),
    )

    profiles: dict[str, ImageProfile] = {}
    if probe_urls:
        with ThreadPoolExecutor(max_workers=min(10, len(probe_urls)), thread_name_prefix="visual-probe") as pool:
            jobs = {pool.submit(image_profile, url): url for url in probe_urls}
            for future in as_completed(jobs):
                url = jobs[future]
                try:
                    profiles[url] = future.result()
                except Exception:
                    profiles[url] = ImageProfile(0, 0, 0.0, "", False)

    used_urls: set[str] = set()
    used_fingerprints: set[str] = set()
    host_counts: dict[str, int] = {}
    resolved: dict[str, dict[str, Any]] = {}

    def pick_candidate(slot: dict[str, Any], *, mode: str) -> tuple[dict[str, Any] | None, ImageProfile | None]:
        candidates = results_by_slot.get(slot["slotId"], [])
        ranked = sorted(
            candidates,
            key=lambda image: _image_score(image, slot, query, source_policy, mode) + _profile_bonus(_best_profile(image, profiles)),
            reverse=True,
        )
        for raw in ranked:
            image = dict(raw)
            semantic_score = _image_score(image, slot, query, source_policy, mode)
            contextual = _visual_context_anchor(
                " ".join(str(slot.get(k) or "") for k in ("query", "afterHeading", "anchorText", "purpose")),
                " ".join(str(image.get(k) or "") for k in ("description", "sourceTitle", "sourceSnippet", "source")),
            )
            provider = str(image.get("provider") or "")
            threshold = 7.2 if mode == "precise" else 5.4
            if contextual or provider in {"source-origin", "source-meta"}:
                threshold -= 0.8
            if not _has_semantic_anchor(image, slot, query) or semantic_score < threshold:
                continue
            url, profile = _usable_image_url(image, profiles)
            if not url or url in used_urls or profile is None or not profile.usable:
                continue
            fingerprint = profile.fingerprint
            if fingerprint and fingerprint in used_fingerprints:
                continue
            host = _host(str(image.get("sourceUrl") or url))
            host_limit = 3 if (slot.get("sourceExplicit") or provider in {"source-origin", "source-meta"}) else 2
            if host and host_counts.get(host, 0) >= host_limit:
                continue
            if url != image.get("url"):
                image["originalUrl"] = image.get("originalUrl") or image.get("url")
                image["url"] = url
                image["usingProviderThumbnail"] = True
            image["matchScore"] = round(semantic_score + _profile_bonus(profile), 2)
            image["width"] = profile.width
            image["height"] = profile.height
            return image, profile
        return None, None

    def commit(slot: dict[str, Any], picked: dict[str, Any], profile: ImageProfile | None, *, fallback: bool = False) -> None:
        url = str(picked.get("url") or "")
        if url:
            used_urls.add(url)
        if profile and profile.fingerprint:
            used_fingerprints.add(profile.fingerprint)
        host = _host(str(picked.get("sourceUrl") or url))
        if host:
            host_counts[host] = host_counts.get(host, 0) + 1
        provider = str(picked.get("provider") or "web-image")
        if fallback:
            picked["matchedByFallback"] = True
        resolved[slot["slotId"]] = {
            **slot,
            "image": picked,
            "matchedBy": f"{provider}-{'rescue' if fallback else 'semantic'}",
        }

    unresolved: list[dict[str, Any]] = []
    for slot in slots:
        if slot.get("kind") == "cover":
            if code_ready:
                resolved[slot["slotId"]] = _generated_cover_visual(slot, query)
            else:
                resolved[slot["slotId"]] = {**slot, "image": None, "matchedBy": "unresolved-no-cjk-font"}
                warnings.append("本地代码封面因缺少中文字体暂未生成，避免出现方块字；文章正文不受影响。")
            continue
        if str(slot.get("slotId") or "") in direct_diagram_ids:
            resolved[slot["slotId"]] = _generated_body_visual(slot, query, reason=f"{strategy}-direct")
            continue
        picked, profile = pick_candidate(slot, mode=match_mode)
        if picked:
            commit(slot, picked, profile)
        else:
            unresolved.append(slot)

    # Rescue all unresolved slots in one parallel wave. Each body slot is capped
    # at two Serper image queries in total. Successful V26-style fast paths still
    # use one query; only hard slots consume the second route. Source-page slots
    # that spent zero Serper calls may use two *different* rescue routes in the
    # same wave, keeping wall-clock latency close to one request.
    rescue_jobs: dict[Any, tuple[dict[str, Any], str]] = {}
    if serper_ready and unresolved:
        with ThreadPoolExecutor(max_workers=min(8, max(2, len(unresolved) * 2)), thread_name_prefix="visual-rescue-search") as pool:
            for slot in unresolved:
                already = queries_by_slot.setdefault(slot["slotId"], set())
                budget = max(0, 2 - len(already))
                if budget <= 0:
                    continue
                variants = []
                for mode in ("broad", "precise"):
                    for v in _visual_query_variants(slot, query, preference, mode):
                        if v and v not in already and v not in variants:
                            variants.append(v)
                if not variants:
                    fallback = _fallback_visual_query(slot, query)
                    if fallback not in already:
                        variants.append(fallback)
                for visual_query in variants[:budget]:
                    already.add(visual_query)
                    future = pool.submit(_search_candidates, visual_query)
                    rescue_jobs[future] = (slot, visual_query)
            for future in as_completed(rescue_jobs):
                slot, visual_query = rescue_jobs[future]
                try:
                    candidates, provider, errors = future.result()
                    for error in errors:
                        warnings.append(f"Serper 配图提示：{error}")
                    for candidate in candidates:
                        candidate = dict(candidate)
                        candidate.setdefault("searchQuery", visual_query)
                        candidate.setdefault("provider", provider)
                        results_by_slot[slot["slotId"]].append(candidate)
                except Exception as exc:
                    warnings.append(f"配图补充检索“{slot.get('afterHeading') or slot.get('purpose') or '正文'}”失败：{str(exc)[:120]}")

    # De-duplicate and probe rescue candidates globally rather than one slot at a
    # time. Two missing images therefore do not wait for two serial 7-second
    # fallbacks.
    for slot in unresolved:
        results_by_slot[slot["slotId"]] = _dedupe_images(results_by_slot.get(slot["slotId"], []))
    rescue_probe_urls = _fair_probe_urls(
        unresolved, results_by_slot, query=query, source_policy=source_policy,
        match_mode="broad", per_slot=8, cap=36, known=set(profiles),
    )
    if rescue_probe_urls:
        with ThreadPoolExecutor(max_workers=min(12, len(rescue_probe_urls)), thread_name_prefix="visual-rescue-probe") as pool:
            jobs = {pool.submit(image_profile, u): u for u in rescue_probe_urls}
            for future in as_completed(jobs):
                u = jobs[future]
                try:
                    profiles[u] = future.result()
                except Exception:
                    profiles[u] = ImageProfile(0, 0, 0.0, "", False)

    for slot in unresolved:
        if slot["slotId"] in resolved:
            continue
        picked, profile = pick_candidate(slot, mode="broad")
        if picked:
            commit(slot, picked, profile, fallback=True)
            continue
        if strategy != "real_only" and code_ready:
            resolved[slot["slotId"]] = _generated_body_visual(slot, query, reason="search-fallback")
            warnings.append(f"“{slot.get('afterHeading') or '正文'}”未找到足够可靠的真实图片，已自动改为系统绘制示意图，避免拿无关图片凑数。")
            continue
        if strategy != "real_only" and not code_ready:
            warnings.append(f"“{slot.get('afterHeading') or '正文'}”未找到可靠真实图片；当前环境缺少中文字体，代码绘图已停用以避免方块字，该位置留空。")
            resolved[slot["slotId"]] = {**slot, "image": None, "matchedBy": "unresolved-no-cjk-font"}
            continue
        if serper_ready:
            warnings.append(f"“{slot.get('afterHeading') or '正文'}”没有找到通过来源原图/语义/可下载性检查的正文图片；当前选择仅真实图片，该位置留空。")
        else:
            warnings.append(f"“{slot.get('afterHeading') or '正文'}”的来源页原图均未通过检查，且未配置 SERPER_API_KEY；当前选择仅真实图片，该位置留空。")
        resolved[slot["slotId"]] = {**slot, "image": None, "matchedBy": "unresolved-real-only"}

    visuals = [resolved.get(slot["slotId"], {**slot, "image": None, "matchedBy": "unresolved"}) for slot in slots]
    return visuals, _dedupe_warnings(warnings)


def _fair_probe_urls(
    slots: list[dict[str, Any]], results_by_slot: dict[str, list[dict[str, Any]]], *,
    query: str, source_policy: str, match_mode: str, per_slot: int, cap: int, known: set[str],
) -> list[str]:
    """Choose probe URLs round-robin so later image slots cannot be starved.

    V27 capped the global download-probe list after iterating slots in order. With
    6-8 requested body images, early slots could consume the whole cap and later
    slots had no profile at all, making them impossible to select. Round-robin
    preserves the same global cap and therefore the same worst-case network cost.
    """
    ranked_by_slot: list[list[dict[str, Any]]] = []
    for slot in slots:
        if slot.get("kind") == "cover":
            continue
        ranked_by_slot.append(sorted(
            results_by_slot.get(str(slot.get("slotId") or ""), []),
            key=lambda image, s=slot: _image_score(image, s, query, source_policy, match_mode),
            reverse=True,
        )[:max(1, per_slot)])
    out: list[str] = []
    seen = set(known or set())
    for rank in range(max(1, per_slot)):
        for rows in ranked_by_slot:
            if rank >= len(rows):
                continue
            image = rows[rank]
            for key in ("url", "fallbackUrl"):
                value = str(image.get(key) or "").strip()
                if not value or value in seen:
                    continue
                seen.add(value)
                out.append(value)
                if len(out) >= max(1, cap):
                    return out
    return out


def _search_candidates(visual_query: str) -> tuple[list[dict[str, Any]], str, list[str]]:
    try:
        rows = serper_images.search_images(visual_query, count=18)
        return rows, "serper", []
    except Exception as exc:
        return [], "serper", [str(exc)[:120]]


def _best_profile(image: dict[str, Any], profiles: dict[str, ImageProfile]) -> ImageProfile | None:
    url = str(image.get("url") or ""); fallback = str(image.get("fallbackUrl") or "")
    first = profiles.get(url)
    if first and first.usable: return first
    second = profiles.get(fallback) if fallback else None
    return second or first


def _usable_image_url(image: dict[str, Any], profiles: dict[str, ImageProfile]) -> tuple[str, ImageProfile | None]:
    url = str(image.get("url") or ""); profile = profiles.get(url)
    if url and profile and profile.usable: return url, profile
    fallback = str(image.get("fallbackUrl") or ""); fallback_profile = profiles.get(fallback) if fallback else None
    if fallback and fallback_profile and fallback_profile.usable: return fallback, fallback_profile
    return "", profile or fallback_profile


def _dedupe_warnings(rows: list[str]) -> list[str]:
    out=[]; seen=set()
    for row in rows:
        if row and row not in seen:
            seen.add(row); out.append(row)
    return out[:10]


def _visual_query_variants(slot: dict[str, str], query: str, preference: str = "", match_mode: str = "precise") -> list[str]:
    subject = str(slot.get("query") or query).strip()
    heading = str(slot.get("afterHeading") or "").strip()
    anchor_text = str(slot.get("anchorText") or "").strip()
    purpose = str(slot.get("purpose") or "").strip()
    preference = str(preference or "").strip()
    preference_text = "" if preference in {"", "混合", "自动"} else f" {preference}"
    source_hint = str(slot.get("sourceHint") or "").strip()
    source_name = str(slot.get("sourceName") or "").strip()
    source_url = str(slot.get("sourceHintUrl") or "").strip()
    source_host = _host(source_url)

    combined = " ".join(x for x in (query, subject, heading, anchor_text, purpose) if x)
    core = _visual_search_core(query, subject, heading, anchor_text)
    intent_routes = _visual_intent_routes(combined)

    # Concrete evidence gets an exact-title lane. Keep it short and do not glue
    # the paragraph text onto the title: Google Images behaves much more like a
    # browser when the query is a few concrete nouns rather than a prompt.
    source_routes: list[str] = []
    if source_hint:
        exact_title = _short_query_piece(source_hint, 84)
        if exact_title:
            source_routes.append(f'"{exact_title}" {source_name}'.strip())

    generic_routes = intent_routes or _generic_visual_routes(combined, core)
    variants: list[str] = []
    if source_routes:
        variants.extend(source_routes[:1])
    if source_hint and source_host:
        exact_title = _short_query_piece(source_hint, 72)
        variants.append(f'"{exact_title}" site:{source_host}')
    variants.extend(generic_routes[:2])
    if not variants:
        variants = [_fallback_visual_query(slot, query)]

    # Broad mode should genuinely change search intent instead of repeating the
    # same source-title query. Rotate thematic routes to the front.
    if match_mode != "precise" and len(variants) > 1:
        thematic = [v for v in variants if "site:" not in v and not v.startswith('"')]
        sourced = [v for v in variants if v not in thematic]
        variants = thematic + sourced

    output: list[str] = []
    for value in variants:
        value = " ".join(str(value or "").split())
        if preference_text and value and preference_text.strip() not in value:
            value = f"{value}{preference_text}"
        value = value[:132]
        if value and value not in output:
            output.append(value)
    return output[:4]


def _short_query_piece(text: str, limit: int = 48) -> str:
    value = re.sub(r"\[[0-9,， ]+\]", " ", str(text or ""))
    value = re.sub(r"[\r\n\t]+", " ", value)
    value = " ".join(value.split()).strip(" ，。；：！？、-—|/")
    if len(value) <= limit:
        return value
    clauses = [x.strip() for x in re.split(r"[，。；：！？、|/—]+", value) if x.strip()]
    useful = [x for x in clauses if len(_compact(x)) >= 4]
    if useful:
        # Prefer concise clauses; the old max-length rule routinely selected a
        # whole explanatory sentence and over-constrained image search.
        best = min(useful[:7], key=lambda x: (abs(len(x) - min(18, limit)), len(x)))
        return best[:limit]
    return value[:limit]


def _visual_intent_routes(text: str) -> list[str]:
    compact = _compact(text)
    out: list[str] = []
    # Concrete scene routes come first. They are much more discriminative than
    # umbrella concepts such as“数据治理/人工智能”and therefore improve both
    # Google Images query precision and the local semantic gate below.
    for route_table in (_SCENE_VISUAL_ROUTES, _VISUAL_INTENT_ROUTES):
        for triggers, routes in route_table:
            if any(_compact(trigger) in compact for trigger in triggers):
                for route in routes:
                    if route not in out:
                        out.append(route)
    return out[:4]


def _generic_visual_routes(text: str, core: str) -> list[str]:
    compact = _compact(text)
    if any(x in compact for x in ("数据治理", "数据管理", "数据要素")):
        return [
            f"{core} 数据治理 数据平台 企业".strip(),
            f"{core} 数据分析 数据可视化 数字化".strip(),
        ]
    if any(x in compact for x in ("人工智能", "大模型", "ai", "机器学习")):
        return [
            f"{core} 人工智能 数据模型 真实场景".strip(),
            f"{core} 数据中心 算法 数据可视化".strip(),
        ]
    return [
        f"{core} 数据 科技 真实场景".strip(),
        f"{core} 数据平台 数据可视化".strip(),
    ]


def _visual_search_core(query: str, subject: str, heading: str, anchor_text: str) -> str:
    # Extract a few stable concepts instead of forwarding long natural-language
    # sentences. This fixes queries such as“为什么数据治理总是失败”and“数据治理
    # 如何影响普通人”, which previously became 70+ character prompt fragments.
    combined = " ".join(x for x in (heading, subject, query) if x)
    compact = _compact(combined)
    vocabulary = [
        "可信数据空间", "公共数据授权运营", "个人信息保护", "关键核心技术", "数据产权登记",
        "数据资产入表", "数据质量", "数据安全", "数据合规", "数据流通", "数据交易", "数据资产",
        "数据治理", "数据管理", "数据要素", "训练数据", "数据标注", "人工智能", "大模型",
        "机器学习", "研发", "科研", "技术突破", "技术创新", "普通人", "个人", "业务", "企业",
        "仓储", "仓库", "库存", "入库", "出库", "供应链", "物流", "生产线", "制造", "工厂",
        "销售", "门店", "客户", "订单", "财务", "医院", "医疗", "交通",
    ]
    terms: list[str] = []
    for term in vocabulary:
        if _compact(term) in compact and term not in terms:
            terms.append(term)
        if len(terms) >= 3:
            break
    if not terms:
        for raw in (heading, query, subject):
            piece = _short_query_piece(raw, 24)
            if piece and _compact(piece) not in {_compact(x) for x in terms}:
                terms.append(piece)
            if len(terms) >= 2:
                break
    if "数据治理" in compact and "数据治理" not in terms:
        terms.insert(0, "数据治理")
    return " ".join(terms[:3])[:64] or "数据治理"


def _fallback_visual_query(slot: dict[str, str], query: str) -> str:
    core = _visual_search_core(
        query,
        str(slot.get("query") or ""),
        str(slot.get("afterHeading") or ""),
        str(slot.get("anchorText") or ""),
    )
    routes = _visual_intent_routes(" ".join(str(slot.get(k) or "") for k in ("query", "afterHeading", "anchorText", "purpose")))
    return (routes[0] if routes else f"{core} 数据治理 数据平台 真实场景")[:132]



def _visual_context_anchor(subject_text: str, candidate_text: str) -> bool:
    """Return True when a candidate matches a topic-specific visual synonym lane.

    This is intentionally stricter than broad semantic similarity: the subject
    must activate a route and the candidate must hit at least two concrete terms
    from that route. It lets“个人信息保护 + 隐私”stand in for a paragraph about
    ordinary people's data-governance experience without admitting generic stock
    technology pictures.
    """
    subject_compact = _compact(subject_text)
    candidate_compact = _compact(candidate_text)
    for route_table in (_SCENE_VISUAL_ROUTES, _VISUAL_INTENT_ROUTES):
        for triggers, routes in route_table:
            if not any(_compact(trigger) in subject_compact for trigger in triggers):
                continue
            for route in routes:
                terms = [term for term in route.split() if len(_compact(term)) >= 2]
                hits = sum(1 for term in terms if _compact(term) in candidate_compact)
                if hits >= 2:
                    return True
    return False



def _local_scene_requires_match(slot: dict[str, str]) -> bool:
    local = _compact(" ".join(str(slot.get(k) or "") for k in ("afterHeading", "anchorText", "purpose")))
    for triggers, _ in _SCENE_VISUAL_ROUTES:
        if any(_compact(trigger) in local for trigger in triggers):
            return True
    return False


def _local_scene_anchor(slot: dict[str, str], candidate_text: str) -> bool:
    local = " ".join(str(slot.get(k) or "") for k in ("afterHeading", "anchorText", "purpose"))
    if _visual_context_anchor(local, candidate_text):
        return True
    # Fallback lexical match for concrete nouns that are not yet in the scene
    # route table. Umbrella words never count by themselves.
    desired = {t for t in _semantic_terms(local) if t not in GENERIC_TERMS and t not in _LOCAL_UMBRELLA_TERMS and len(t) >= 3}
    candidate = _semantic_terms(candidate_text)
    return len(desired & candidate) >= 2


def _has_semantic_anchor(image: dict[str, Any], slot: dict[str, str], query: str) -> bool:
    subject_text = " ".join(x for x in [query, str(slot.get("query") or ""), str(slot.get("afterHeading") or ""), str(slot.get("anchorText") or "")] if x)
    candidate = " ".join(str(image.get(key) or "") for key in ("description", "sourceTitle", "sourceSnippet", "source", "sourceUrl"))
    source_hint = str(slot.get("sourceHint") or "").strip()
    source_hint_url = str(slot.get("sourceHintUrl") or "").strip()
    explicit_source = bool(slot.get("sourceExplicit"))

    candidate_compact = _compact(candidate)
    # Long contiguous phrases are much safer than arbitrary Chinese bi-grams.
    strong_phrase = False
    for size in (8, 7, 6, 5, 4):
        chunks = HAN_RE.findall(subject_text)
        for chunk in chunks:
            if len(chunk) < size:
                continue
            for i in range(len(chunk) - size + 1):
                phrase = chunk[i:i+size]
                if phrase in candidate_compact:
                    strong_phrase = True
                    break
            if strong_phrase:
                break
        if strong_phrase:
            break

    desired_terms = {t for t in _semantic_terms(subject_text) if t not in GENERIC_TERMS and len(t) >= 3}
    candidate_terms = _semantic_terms(candidate)
    long_overlap = desired_terms & candidate_terms
    lexical_anchor = strong_phrase or len(long_overlap) >= 2

    source_text_match = False
    if source_hint:
        hint_terms = {t for t in _semantic_terms(source_hint) if t not in GENERIC_TERMS and len(t) >= 3}
        source_text_match = len(hint_terms & candidate_terms) >= 2
    candidate_source_url = str(image.get("sourceUrl") or "")
    same_host = _same_source_host(source_hint_url, candidate_source_url)
    same_page = _same_page_url(source_hint_url, candidate_source_url)
    source_match = source_text_match or same_page

    requires_data_context = _requires_data_context(subject_text)
    contextual_anchor = _visual_context_anchor(subject_text, candidate)
    has_data_context = _contains_any(candidate, DATA_CONTEXT_TERMS) or contextual_anchor
    provider = str(image.get("provider") or "")
    # Source-origin/source-meta rows are fetched from the exact cited page. Serper
    # rows merely sharing a large media site's host are *not* exact provenance;
    # require the same page URL or a strong title match on that host.
    exact_source_candidate = explicit_source and not _is_root_like_url(source_hint_url) and (
        (provider in {"source-origin", "source-meta"} and (same_page or same_host))
        or (provider == "serper" and (same_page or (same_host and source_text_match)))
    )
    # For a model-explicit case/news source, source provenance itself is a strong
    # semantic anchor. This lets an article about data-driven R&D use a real photo
    # from the cited laboratory/company story even when the photo caption does not
    # literally contain the word “数据”. Generic web images still need a data anchor.
    if requires_data_context and not has_data_context and not exact_source_candidate:
        return False

    # A concrete paragraph scene must not be satisfied by an image that only
    # repeats the article-wide umbrella topic. Example: a warehouse/inventory
    # paragraph should not accept computer-vision cars just because the source
    # title also contains“数据治理/AI”. Exact cited-page provenance remains an
    # exception; the binary artifact gate still rejects QR/logo/follow cards.
    if provider == "serper" and _local_scene_requires_match(slot) and not _local_scene_anchor(slot, candidate):
        return False

    if exact_source_candidate:
        # Exact evidence-page provenance is intentionally strong, because a real
        # case photo may not contain the word“数据”in its caption. But late assets
        # from the same page are often related-links/decorations; require a textual
        # anchor for those instead of accepting the host alone.
        if str(image.get("provider") or "") in {"source-origin", "source-meta"}:
            try:
                position = int(image.get("position") or 1)
            except (TypeError, ValueError):
                position = 1
            # Tavily's source-page image list has no reliable per-image caption.
            # Page-title metadata therefore cannot prove that a late asset is the
            # article photo (it may be a related-card thumbnail or footer graphic).
            # Inspect only the first six origin assets; if none works, Serper gets
            # one precise fallback query rather than accepting decorative debris.
            if position > 6:
                return False
        return True
    return lexical_anchor or contextual_anchor or (source_match and strong_phrase)


def _image_score(
    image: dict[str, Any],
    slot: dict[str, str],
    query: str,
    source_policy: str = "balanced",
    match_mode: str = "precise",
) -> float:
    desired = " ".join(
        x for x in [query, str(slot.get("query") or ""), str(slot.get("afterHeading") or ""), str(slot.get("anchorText") or "")]
        if x
    )
    candidate_text = " ".join(
        str(image.get(key) or "")
        for key in ("description", "sourceTitle", "sourceSnippet", "source")
    )
    desired_terms = _semantic_terms(desired)
    candidate_terms = _semantic_terms(candidate_text)
    overlap = {t for t in (desired_terms & candidate_terms) if t not in GENERIC_TERMS}
    weighted = sorted(overlap, key=lambda t: (len(t), t), reverse=True)[:10]
    semantic = sum(2.6 if len(term) >= 4 else 1.4 for term in weighted)
    if match_mode == "precise" and len([t for t in weighted if len(t) >= 3]) >= 2:
        semantic += 3.0

    normalized_desired = _compact(desired)
    normalized_candidate = _compact(candidate_text)
    phrase_bonus = 0.0
    for phrase in [str(slot.get("query") or ""), str(slot.get("afterHeading") or "")]:
        phrase = _compact(phrase)
        if len(phrase) >= 4 and phrase in normalized_candidate:
            phrase_bonus += 5.0

    source_url = str(image.get("sourceUrl") or "")
    host = _host(source_url)
    traceability = 3.0 if source_url else 0.0
    authority = 0.0
    if host.endswith("gov.cn") or host.endswith("edu.cn") or host.endswith("org.cn"):
        authority = 4.0 if source_policy == "authority" else 2.0
    elif host:
        authority = 0.7
    if source_policy == "authority" and any(token in host for token in (
        "news.cn", "xinhuanet.com", "people.com.cn", "cctv.com", "cnr.cn", "chinanews.com",
        "gov.cn", "edu.cn", "36kr.com", "thepaper.cn", "yicai.com", "sina.com.cn", "mp.weixin.qq.com",
    )):
        authority += 2.0

    result_relevance = min(4.0, max(0.0, float(image.get("resultScore") or 0)) * 4.0)
    # If the image was extracted from the exact evidence page used in the article,
    # prefer it over a generic image-search hit when both are semantically valid.
    provider = str(image.get("provider") or "")
    origin_bonus = 6.0 if provider == "source-meta" else (5.5 if provider == "source-origin" else 0.0)
    if provider == "source-meta":
        html_score = max(0, min(10, int(image.get("htmlScore") or 0)))
        origin = str(image.get("origin") or "")
        origin_bonus += min(2.2, html_score * 0.22)
        if origin in {"page-meta", "json-ld"}:
            origin_bonus += 1.2
    source_hint_url = str(slot.get("sourceHintUrl") or "")
    same_source = _same_source_host(source_hint_url, source_url)
    same_page = _same_page_url(source_hint_url, source_url)
    source_hint_terms = {t for t in _semantic_terms(str(slot.get("sourceHint") or "")) if t not in GENERIC_TERMS and len(t) >= 3}
    source_title_terms = _semantic_terms(str(image.get("sourceTitle") or ""))
    source_title_match = len(source_hint_terms & source_title_terms) >= 2 if source_hint_terms else False
    if bool(slot.get("sourceExplicit")) and (same_page or (provider in {"source-origin", "source-meta"} and same_source)):
        explicit_source_bonus = 9.0
    elif bool(slot.get("sourceExplicit")) and same_source and source_title_match:
        explicit_source_bonus = 5.0
    else:
        explicit_source_bonus = 1.0 if same_source else 0.0
    bad_text = f"{str(image.get('url') or '').lower()} {candidate_text.lower()}"
    bad_penalty = 10.0 if any(token in bad_text for token in BAD_IMAGE_HINTS) else 0.0
    if str(image.get("provider") or "") in {"source-origin", "source-meta"}:
        url_text = str(image.get("url") or "").lower()
        if any(token in url_text for token in ("logo", "banner", "avatar", "qrcode", "qr-code", "weixin", "wechat", "wxcode", "icon", "advert", "share")):
            bad_penalty += 24.0
        # Origin pages often expose many decorative assets after the article image.
        # Keep later images eligible, but gently prefer the first few content images.
        try:
            position = int(image.get("position") or 1)
        except (TypeError, ValueError):
            position = 1
        bad_penalty += max(0, position - 4) * 0.7
    if _requires_data_context(desired) and any(token.lower() in bad_text.lower() for token in CONSUMER_TECH_DISTRACTIONS) and not any(token.lower() in desired.lower() for token in CONSUMER_TECH_DISTRACTIONS):
        bad_penalty += 12.0
    if _requires_data_context(desired) and not _contains_any(candidate_text, DATA_CONTEXT_TERMS) and not (bool(slot.get("sourceExplicit")) and same_source):
        bad_penalty += 10.0
    context_bonus = 4.0 if _visual_context_anchor(desired, candidate_text) else 0.0
    local_scene_bonus = 5.0 if _local_scene_anchor(slot, candidate_text) else 0.0
    if str(image.get("provider") or "") == "serper" and _local_scene_requires_match(slot) and not local_scene_bonus:
        bad_penalty += 8.0
    scene_bonus = sum(0.8 for token in REAL_SCENE_TERMS if token in candidate_text)
    preference = str(slot.get("preference") or "") + " " + str(slot.get("purpose") or "")
    if any(token in preference for token in ("真实", "现场", "项目", "机构")):
        scene_bonus += sum(0.9 for token in REAL_SCENE_TERMS if token in candidate_text)
    cover_bonus = 1.5 if slot.get("kind") == "cover" and any(token in candidate_text for token in ("新闻", "现场", "发布", "会议", "项目")) else 0.0
    return semantic + phrase_bonus + traceability + authority + result_relevance + origin_bonus + explicit_source_bonus + context_bonus + local_scene_bonus + scene_bonus + cover_bonus - bad_penalty



def _requires_data_context(text: str) -> bool:
    compact = _compact(text)
    return any(token.lower() in compact for token in ("数据", "datadriven", "data", "数据驱动", "数据治理", "数据要素"))


def _contains_any(text: str, terms: set[str]) -> bool:
    lowered = str(text or "").lower()
    for raw in terms:
        term = str(raw or "").lower().strip()
        if not term:
            continue
        if re.fullmatch(r"[a-z0-9_+-]+", term):
            # Short Latin anchors such as AI must be token-bound. Plain substring
            # matching made words like "detail" or "main" accidentally satisfy
            # the data-context gate because they contain the letters "ai".
            if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lowered):
                return True
        elif term in lowered:
            return True
    return False


def _generated_cover_visual(slot: dict[str, str] | None, query: str) -> dict[str, Any]:
    slot = dict(slot or {"slotId": "cover", "kind": "cover", "afterHeading": "", "purpose": "文章封面视觉", "query": query})
    title = str(slot.get("coverTitle") or slot.get("query") or query).strip()
    brief = str(slot.get("coverBrief") or slot.get("purpose") or "").strip()
    uri = build_cover_data_uri(title, brief, query)
    image = {
        "url": uri, "description": title, "source": "系统生成封面", "sourceUrl": "",
        "sourceTitle": title, "sourceSnippet": brief, "provider": "generated-cover",
        "width": 1600, "height": 900, "matchScore": 100.0,
    }
    return {**slot, "image": image, "matchedBy": "generated-cover-title-bound"}



def _generated_body_visual(slot: dict[str, Any], query: str, *, reason: str) -> dict[str, Any]:
    slot = dict(slot)
    image = build_body_visual(slot, query)
    image["generationReason"] = reason
    return {**slot, "image": image, "matchedBy": f"generated-diagram-{reason}"}

def _profile_bonus(profile: ImageProfile | None) -> float:
    if profile is None:
        return 0.0
    if not profile.usable:
        return -50.0
    pixels = profile.width * profile.height
    resolution = min(5.0, pixels / 650_000)
    aspect = profile.aspect
    aspect_bonus = 2.2 if 1.2 <= aspect <= 2.1 else (1.0 if 0.75 <= aspect <= 2.6 else -1.0)
    return resolution + aspect_bonus


def _semantic_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for token in LATIN_RE.findall(text.lower()):
        if len(token) > 1:
            terms.add(token)
    for chunk in HAN_RE.findall(text):
        if len(chunk) <= 6:
            terms.add(chunk)
        for size in (2, 3, 4):
            if len(chunk) < size:
                continue
            for i in range(len(chunk) - size + 1):
                terms.add(chunk[i:i + size])
    return terms


def _compact(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", str(text or "")).lower()


def _same_source_host(a: str, b: str) -> bool:
    ah, bh = _host(a), _host(b)
    return bool(ah and bh and (ah == bh or ah.endswith("." + bh) or bh.endswith("." + ah)))


def _same_page_url(a: str, b: str) -> bool:
    if not _same_source_host(a, b):
        return False
    try:
        pa, pb = urlparse(str(a or "")), urlparse(str(b or ""))
        path_a = re.sub(r"/+", "/", pa.path or "/").rstrip("/") or "/"
        path_b = re.sub(r"/+", "/", pb.path or "/").rstrip("/") or "/"
        if path_a == path_b:
            return True
        # Some image-search providers strip harmless .html/.shtml suffixes.
        strip_suffix = lambda x: re.sub(r"\.(?:s?html?|htm)$", "", x, flags=re.I).rstrip("/") or "/"
        return strip_suffix(path_a) == strip_suffix(path_b) and path_a != "/"
    except Exception:
        return False


def _is_root_like_url(url: str) -> bool:
    """True for publisher homepages, which are not evidence-page provenance."""
    try:
        parsed = urlparse(str(url or "").strip())
        path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
        meaningful_query = bool(parsed.query and re.search(r"(?:id|article|news|content|page|doc|item)=", parsed.query, flags=re.I))
        return bool(parsed.hostname and path == "/" and not meaningful_query)
    except Exception:
        return False


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _dedupe_images(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for raw in images:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or "").strip()
        if not url or url in seen or not url.startswith(("http://", "https://")):
            continue
        lowered = url.lower()
        if any(token in lowered for token in ("favicon", "sprite", "avatar", "placeholder", "logo", "qrcode", "qr-code", "qr_code", "weixin", "wechat", "wxcode", "/icon", "banner", "/ads/", "advert")):
            continue
        seen.add(url)
        try:
            position = int(raw.get("position") or 0)
        except (TypeError, ValueError):
            position = 0
        try:
            width = int(raw.get("width") or 0)
        except (TypeError, ValueError):
            width = 0
        try:
            height = int(raw.get("height") or 0)
        except (TypeError, ValueError):
            height = 0
        output.append(
            {
                "url": url,
                "description": str(raw.get("description") or "")[:420],
                "source": str(raw.get("source") or "")[:160],
                "sourceUrl": str(raw.get("sourceUrl") or "")[:3000],
                "sourceTitle": str(raw.get("sourceTitle") or "")[:320],
                "sourceSnippet": str(raw.get("sourceSnippet") or "")[:900],
                "resultScore": float(raw.get("resultScore") or 0),
                "searchQuery": str(raw.get("searchQuery") or "")[:260],
                "provider": str(raw.get("provider") or "")[:40],
                "fallbackUrl": str(raw.get("fallbackUrl") or "")[:3000],
                "originalUrl": str(raw.get("originalUrl") or "")[:3000],
                "confidence": str(raw.get("confidence") or "")[:40],
                "origin": str(raw.get("origin") or "")[:40],
                "htmlScore": max(0, min(20, int(raw.get("htmlScore") or 0))),
                # Preserve provider rank/position. Source-page image quality logic
                # depends on this to demote late related-card/decorative assets.
                "position": position,
                "width": width,
                "height": height,
            }
        )
    return output
