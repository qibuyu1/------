from __future__ import annotations
import re
from typing import Any

DOMAIN_MAP = {
    "政策": ["政策", "通知", "指南", "规划", "监管", "改革"],
    "产业": ["企业", "产业", "项目", "落地", "应用", "商业化"],
    "技术": ["技术", "突破", "攻关", "研发", "创新", "专利"],
    "研究": ["论文", "报告", "研究", "模型", "趋势"],
}


def build_research_plan(topic: str, description: str = "") -> dict[str, Any]:
    text = f"{topic} {description}".strip()
    lanes = []
    for name, words in DOMAIN_MAP.items():
        if any(w in text for w in words):
            lanes.append(name)
    if not lanes:
        lanes = ["政策", "产业", "技术", "研究"]
    return {
        "topic": topic,
        "research_questions": [
            f"{topic}最近发生了哪些重要事件？",
            f"{topic}背后的产业变化是什么？",
            f"{topic}有哪些政策或标准推动？",
            f"{topic}未来影响哪些企业和场景？",
        ],
        "lanes": lanes,
        "query_matrix": [
            f"{topic} 最新动态",
            f"{topic} 政策 解读",
            f"{topic} 企业 应用 案例",
            f"{topic} 技术突破 产业化",
        ],
    }



def semantic_lanes(topic: str) -> dict:
    """将用户主题扩展为研究路径，而非简单关键词。"""
    return {
        "policy": [f"{topic} 政策", f"{topic} 指导意见", f"{topic} 改革"],
        "industry": [f"{topic} 企业实践", f"{topic} 产业应用", f"{topic} 落地案例"],
        "technology": [f"{topic} 技术突破", f"{topic} 科技攻关", f"{topic} 研发进展"],
        "analysis": [f"{topic} 趋势分析", f"{topic} 研究报告"],
    }
