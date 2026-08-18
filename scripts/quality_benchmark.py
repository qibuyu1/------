#!/usr/bin/env python3
"""Offline quality/efficiency guard for V30.

No external API keys or network calls are used. It checks the local semantic gate,
provider-call budgets, prompt size proxy, source-first image behavior, and hybrid code-visual routing so future
changes can be compared without spending Tavily/Serper/DeepSeek quota.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow `python scripts/quality_benchmark.py` from the project root.  V27 only
# worked under an already-configured PYTHONPATH, so the documented self-check
# command could fail on a clean checkout.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from unittest.mock import patch

from app import deepseek, pipeline, serper_images, serper_search, tavily, visuals
from app.image_fetch import ImageProfile
from app.query_intent import local_plan


def search_precision() -> dict:
    cases = [
        (
            "数据在技术突破上的作用",
            {"title": "数据要素赋能企业关键核心技术突破研究", "snippet": "数据要素降低信息搜集成本并优化企业研发决策", "source": "科研管理"},
            {"title": "数据中心液冷技术取得新突破", "snippet": "服务器散热材料与制冷设备升级", "source": "硬件产业网"},
        ),
        (
            "数据资产入表对企业融资有什么作用",
            {"title": "数据资产入表探索融资增信新路径", "snippet": "企业将合规数据资源入表并探索质押融资和授信", "source": "财经媒体"},
            {"title": "企业资产负债表编制指南", "snippet": "固定资产与存货会计处理", "source": "会计网站"},
        ),
        (
            "可信数据空间如何促进产业协同",
            {"title": "可信数据空间推动产业链数据协同利用", "snippet": "企业跨主体共享数据并开展产业协同", "source": "产业媒体"},
            {"title": "商业办公空间升级改造", "snippet": "产业园办公空间设计与装修", "source": "地产网站"},
        ),
    ]
    rows = []
    for query, good, bad in cases:
        intent = local_plan(query)
        good_score = pipeline._query_match_score(query, good, intent=intent)
        bad_score = pipeline._query_match_score(query, bad, intent=intent)
        rows.append({
            "query": query,
            "goodScore": good_score,
            "badScore": bad_score,
            "margin": good_score - bad_score,
            "threshold": intent.get("matchThreshold"),
            "primaryQuery": intent.get("domesticNewsQuery"),
        })
    return {"cases": rows, "minMargin": min(row["margin"] for row in rows)}



def tavily_primary_budget() -> dict:
    calls = []

    def fake(name, value):
        def run(*args, **kwargs):
            calls.append((name, args[0] if args else ""))
            return value
        return run

    patches = [
        patch.object(tavily, "available", return_value=True),
        patch.object(serper_search, "available", return_value=False),
        patch.object(tavily, "search_domestic_news", side_effect=fake("domestic_news", {"results": [], "images": []})),
        patch.object(tavily, "search_domestic_web", side_effect=fake("domestic_web", {"results": [], "images": []})),
        patch.object(tavily, "search", side_effect=fake("global_news", {"results": [], "images": []})),
        patch.object(tavily, "search_policy", side_effect=fake("policy", {"results": [], "images": []})),
        patch.object(tavily, "search_papers", side_effect=fake("papers", [])),
    ]
    for item in patches:
        item.start()
    try:
        pipeline._RESEARCH_CACHE.clear()
        pipeline.research({
            "query": "数据在技术突破上的作用", "types": ["news", "policy", "paper"],
            "timeRange": "all", "maxResults": 20, "searchMode": "fast",
            "regionPreference": "domestic-first", "surface": "interactive",
        })
    finally:
        for item in reversed(patches):
            item.stop()
    return {"calls": len(calls), "routes": calls}

def serper_budget() -> dict:
    calls = []
    def fake_search(q, **kwargs):
        calls.append((q, kwargs.get("kind")))
        return []
    with patch.object(serper_search, "available", return_value=True), patch.object(serper_search, "search", side_effect=fake_search):
        pipeline._serper_recall(
            ["数据 技术突破", "数据要素 技术突破", "数据驱动 技术创新", "数据在技术突破上的作用"],
            requested_types={"news", "policy", "paper"}, count=8,
        )
    return {"calls": len(calls), "routes": calls}


def image_budget() -> dict:
    candidate = {
        "url": "https://img.example.cn/data-lab.jpg", "fallbackUrl": "",
        "description": "研发团队查看数据分析模型", "source": "研究机构",
        "sourceUrl": "https://research.example.cn/case", "sourceTitle": "数据驱动研发平台",
        "sourceSnippet": "历史实验数据和机器学习模型辅助研发决策", "resultScore": 0.9,
        "provider": "serper",
    }
    profile = ImageProfile(1280, 720, 16 / 9, "benchmark", True)
    base_slot = {
        "slotId": "body-1", "kind": "body", "query": "数据驱动研发 模型筛选",
        "afterHeading": "历史实验数据开始进入研发决策",
        "anchorText": "历史实验数据训练机器学习模型，帮助研发团队缩小候选范围。",
        "purpose": "展示数据分析辅助研发的真实场景",
    }

    no_source_calls = []
    with patch.object(serper_images, "available", return_value=True), \
         patch.object(serper_images, "search_images", side_effect=lambda q, count=14: no_source_calls.append(q) or [candidate]), \
         patch.object(visuals, "image_profile", return_value=profile):
        visuals.resolve_visuals([{**base_slot, "sourceImages": []}], "数据驱动研发")

    source_calls = []
    source_slot = {
        **base_slot,
        "sourceExplicit": True,
        "sourceHint": "数据要素赋能企业关键核心技术突破研究",
        "sourceName": "科研管理",
        "sourceHintUrl": "https://research.example.cn/case",
        "sourceSnippet": "数据赋能研发技术突破",
        "sourceImages": ["https://research.example.cn/lab.jpg"],
    }
    with patch.object(serper_images, "available", return_value=True), \
         patch.object(serper_images, "search_images", side_effect=lambda q, count=14: source_calls.append(q) or []), \
         patch.object(visuals, "image_profile", return_value=profile):
        visuals.resolve_visuals([source_slot], "数据驱动研发")
    return {"noSourceInitialCalls": len(no_source_calls), "sourcePageInitialCalls": len(source_calls)}


def hybrid_visual_routing_budget() -> dict:
    base = {
        "slotId": "body-1", "kind": "body", "query": "数据治理 机制",
        "afterHeading": "数据如何进入经营",
        "anchorText": "原始数据经过标准、质量与权属治理，形成数据产品并进入业务决策。",
        "purpose": "解释数据从治理到业务价值的链路",
    }

    smart_calls = []
    smart_slot = {**base, "visualIntent": "diagram", "visualType": "flow", "visualPlan": {"nodes": ["原始数据", "标准治理", "数据产品", "业务应用"]}}
    with patch.object(serper_images, "available", return_value=True), \
         patch.object(serper_images, "search_images", side_effect=lambda q, count=18: smart_calls.append(q) or []):
        smart_out, _ = visuals.resolve_visuals([smart_slot], "数据治理", strategy="smart")

    all_calls = []
    with patch.object(serper_images, "available", return_value=True), \
         patch.object(serper_images, "search_images", side_effect=lambda q, count=18: all_calls.append(q) or []):
        all_out, _ = visuals.resolve_visuals([base], "数据治理", strategy="all_diagram")

    real_calls = []
    real_slot = {**base, "visualIntent": "real"}
    with patch.object(serper_images, "available", return_value=True), \
         patch.object(serper_images, "search_images", side_effect=lambda q, count=18: real_calls.append(q) or []):
        real_out, _ = visuals.resolve_visuals([real_slot], "数据治理", strategy="real_first")

    return {
        "smartSerperCalls": len(smart_calls),
        "smartProvider": (smart_out[0].get("image") or {}).get("provider"),
        "allDiagramSerperCalls": len(all_calls),
        "allDiagramProvider": (all_out[0].get("image") or {}).get("provider"),
        "realFirstSerperCalls": len(real_calls),
        "realFirstFallbackProvider": (real_out[0].get("image") or {}).get("provider"),
    }


def prompt_proxy() -> dict:
    raw = (
        "国家数据局发布政策，提出加强关键数据技术攻关。"
        "企业利用历史实验数据训练模型，缩小候选方案并降低研发试错成本。"
        "研究显示数据共享、数据治理和模型评测能够提升研发效率。"
    ) * 80
    sources = [
        {"type": "news", "title": f"数据驱动研发案例{i}", "source": "权威来源", "publishedAt": "2026-08-01", "url": f"https://e.cn/{i}", "rawContent": raw, "score": 90}
        for i in range(6)
    ]
    captured = {}
    def fake_generate(system_prompt, user_prompt, **kwargs):
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return {
            "titleCandidates": ["数据开始改变研发试错的组织方式", "实验数据为什么正在进入研发决策"],
            "recommendedTitle": "数据开始改变研发试错的组织方式", "deck": "导语", "markdown": "正文" * 700,
            "understoodBrief": "解释数据如何改变研发", "coverBrief": "数据研发",
            "imageQueries": [], "imageSlots": [], "socialSummary": "", "keyClaims": [], "riskNotes": [], "sourceNotes": [],
            "_deepseekMeta": {},
        }
    with patch.object(deepseek, "generate_json", side_effect=fake_generate):
        pipeline._llm_article(
            "数据驱动研发", sources, "学术科普", "普通公众", "中篇 · 1800—2400字", True, False,
            tone="理性、清晰", length_spec={"min": 1800, "max": 2400, "target": 2100},
        )
    system_chars = len(captured.get("system", "")); user_chars = len(captured.get("user", ""))
    return {"systemChars": system_chars, "userChars": user_chars, "totalPromptChars": system_chars + user_chars}


def main() -> None:
    report = {
        "searchPrecision": search_precision(),
        "tavilyPrimary": tavily_primary_budget(),
        "serperFallback": serper_budget(),
        "images": image_budget(),
        "hybridVisuals": hybrid_visual_routing_budget(),
        "draftPromptProxy": prompt_proxy(),
    }
    checks = [
        report["searchPrecision"]["minMargin"] >= 40,
        report["tavilyPrimary"]["calls"] <= 5,
        report["serperFallback"]["calls"] <= 3,
        report["images"]["noSourceInitialCalls"] <= 1,
        report["images"]["sourcePageInitialCalls"] == 0,
        report["hybridVisuals"]["smartSerperCalls"] == 0,
        report["hybridVisuals"]["smartProvider"] == "generated-diagram",
        report["hybridVisuals"]["allDiagramSerperCalls"] == 0,
        report["hybridVisuals"]["allDiagramProvider"] == "generated-diagram",
        report["hybridVisuals"]["realFirstSerperCalls"] <= 2,
        report["hybridVisuals"]["realFirstFallbackProvider"] == "generated-diagram",
        report["draftPromptProxy"]["totalPromptChars"] < 16000,
    ]
    report["passed"] = all(checks)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
