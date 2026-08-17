from __future__ import annotations

import json
import re
from typing import Any

from . import deepseek
from .cache import TTLCache

_PLAN_CACHE = TTLCache(max_items=160, ttl_seconds=20 * 60)

DOMAIN_HINTS = [
    "数据要素", "数据治理", "数据流通", "数据资产", "公共数据", "数据交易",
    "可信数据空间", "数据基础制度", "数据确权", "数据产权", "数据授权运营",
    "数据合规", "数据安全", "数据质量", "数据价值", "数据跨境", "数字经济",
    "数据产品", "数据服务", "数据标注", "合成数据", "数据供应链",
    "data governance", "data elements", "data asset", "data space", "data market",
]

SOURCE_HINTS = [
    "国家数据局", "政府官网", "部委官网", "地方政府", "新华社", "人民网", "央视",
    "央广网", "36氪", "财联社", "财新", "澎湃", "证券时报", "第一财经", "主流媒体", "权威媒体",
    "行业媒体", "企业官网", "微信公众号", "学术期刊", "高校", "国际媒体",
]

SEARCH_VOCABULARY = [
    "全国统一数据市场", "数据产权登记", "公共数据授权运营", "数据资产入表", "可信数据空间",
    "数据基础制度", "数据要素市场化配置", "数据基础设施", "数据产品", "数据交易",
    "数据流通", "数据资产", "数据确权", "数据产权", "数据治理", "数据要素",
    "数据要素×", "数据要素大赛", "人工智能", "AI数据", "语料", "训练数据",
    "数据质量", "数据清洗", "数据标注", "合成数据", "数据供应链", "数据产品", "数据服务",
    "个人信息保护", "隐私保护", "数据安全", "数据合规", "数据跨境", "数据出境", "公共数据",
    "企业价值", "个人价值", "企业数据治理", "个人信息权益", "经济价值", "社会价值",
    "政策解读", "产业实践", "典型案例", "主流媒体", "权威媒体", "中文论文", "国际研究",
]

# “数据要素治理”是本站的上位主题。新闻标题往往只写其中一个具体事件，
# 例如“数据产权登记”“数据要素×大赛”“公共数据开放”“AI 数据与安全”。
# 如果把完整上位词当成必须精确命中的词，真实新闻会在本地过滤阶段被误删。
DATA_GOVERNANCE_FAMILY = [
    "数据要素", "数据治理", "数据产权", "数据产权登记", "数据确权", "公共数据",
    "公共数据授权运营", "数据流通", "数据交易", "数据资产", "数据资产入表",
    "可信数据空间", "数据基础设施", "数据要素×", "数据要素大赛", "数据产品",
    "数据安全", "数据合规", "数据质量", "数据跨境", "数据出境", "个人信息保护",
    "数据产品", "数据服务", "数据标注", "合成数据", "数据供应链", "AI数据", "训练数据", "语料",
]

_GENERIC_TOPICS = {"数据", "治理", "政策", "论文", "新闻", "价值", "市场", "数据要素治理"}

# Natural-language search phrases often contain relation words that almost never
# appear verbatim in headlines. Treat these words as syntax, not as hard search
# terms. For example “数据在技术突破上的作用” should retrieve “数据要素赋能企业
# 关键核心技术突破研究” even though the whole sentence is absent from the title.
_QUERY_RELATION_WORDS = (
    "为什么", "怎么样", "怎样", "如何", "能否", "是否", "对于", "关于", "通过",
    "在", "对", "与", "和", "及", "以及", "中的", "中", "上的", "上", "里的", "里",
    "的作用", "作用", "的影响", "影响", "的意义", "意义", "的关系", "关系",
    "促进", "推动", "帮助", "助力", "带来", "实现", "有什么", "有何", "意味着什么",
    "研究", "探讨", "分析", "机制", "路径", "现状", "趋势",
)

# Single Chinese characters such as“中/上/在”are useful grammar hints but are
# disastrous as split tokens: V27 could shred concrete terms like“数据中台”and
# even classify“数据中心液冷技术”as a relationship question just because it
# contains“中”. Keep grammar detection and concept splitting separate.
_QUERY_SPLIT_RELATION_WORDS = tuple(x for x in _QUERY_RELATION_WORDS if len(x) >= 2)
_QUERY_CONCEPTUAL_MARKERS = (
    "为什么", "怎么样", "怎样", "如何", "能否", "是否", "对于", "关于", "通过",
    "的作用", "作用", "的影响", "影响", "的意义", "意义", "的关系", "关系",
    "促进", "推动", "帮助", "助力", "带来", "实现", "有什么", "有何", "意味着什么",
    "机制", "路径", "现状", "趋势",
)

_CONCEPT_VOCABULARY = (
    "关键核心技术", "技术突破", "技术创新", "科技创新", "数据科技创新", "研发创新",
    "数据要素", "数据驱动", "数据赋能", "数据治理", "数据资产入表", "数据资产", "公共数据",
    "训练数据", "语料", "数据质量", "数据清洗", "数据标注", "合成数据", "数据供应链",
    "数据产品", "数据服务", "数据安全", "数据合规", "数据跨境", "数据出境", "个人信息保护",
    "人工智能", "机器学习", "大模型", "数据分析", "数据平台", "数据中台", "主数据",
    "元数据", "数据目录", "数据标准", "数据孤岛", "数据开放", "数据共享", "数字政务",
    "数据交易所", "数据基础设施", "融资", "产业协同", "研发", "创新", "企业", "产业", "政策", "科研",
)

# Deterministic semantic expansion used by both retrieval and local relevance scoring.
# The goal is not to turn every query into a synonym soup, but to recognize the
# few alternative phrasings search engines and Chinese headlines commonly use.
_CONCEPT_ALIAS_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("数据", "数据要素", "数据驱动", "数据赋能"), ("数据", "数据要素", "数据驱动", "数据赋能", "数据分析")),
    (("技术突破", "关键核心技术", "技术攻关"), ("技术突破", "关键核心技术突破", "技术创新", "科技创新", "技术攻关")),
    (("研发", "科研", "研发创新"), ("研发", "研发创新", "技术研发", "科研")),
    (("降本", "降低成本", "成本"), ("降本", "降低成本", "成本优化", "提质增效")),
    (("提效", "提高效率", "效率"), ("提效", "提高效率", "效率提升", "提质增效")),
    (("公共数据", "授权运营"), ("公共数据", "公共数据授权运营", "公共数据开发利用")),
    (("数据产权", "数据确权", "产权登记"), ("数据产权", "数据确权", "数据产权登记", "产权登记")),
    (("数据流通", "数据交易"), ("数据流通", "数据交易", "数据流通利用", "数据市场")),
    (("数据资产入表", "数据资产", "入表"), ("数据资产入表", "数据资产", "数据资源入表", "数据资产化")),
    (("融资", "质押融资", "授信", "增信"), ("融资", "质押融资", "授信", "增信", "融资担保")),
    (("训练数据", "语料", "数据集"), ("训练数据", "数据集", "语料", "数据供应链", "数据标注")),
    (("数据质量", "质量治理"), ("数据质量", "数据质量治理", "质量评估", "数据清洗")),
    (("数据标注", "合成数据", "数据供应链"), ("数据标注", "合成数据", "数据供应链", "数据生产")),
    (("数据安全", "数据合规", "安全合规"), ("数据安全", "数据合规", "安全合规", "合规治理")),
    (("数据跨境", "数据出境", "跨境数据"), ("数据跨境", "数据出境", "跨境数据流动", "数据出境安全评估")),
    (("个人信息", "个人信息保护", "隐私保护"), ("个人信息保护", "隐私保护", "个人信息权益")),
    (("数据产品", "数据服务"), ("数据产品", "数据服务", "数据产品化")),
    (("数据要素×", "数据要素大赛"), ("数据要素×", "数据要素大赛", "数据应用场景", "场景创新")),
    (("产业协同", "协同"), ("产业协同", "产业链协同", "跨主体协同", "协同利用")),
    (("可信数据空间", "数据空间"), ("可信数据空间", "数据空间", "数据基础设施")),
    (("人工智能", "AI", "大模型"), ("人工智能", "AI", "大模型", "机器学习")),
)


def _concept_groups(keyword: str, concepts: list[str]) -> list[list[str]]:
    """Build a few OR-groups for local semantic matching.

    A result only needs to hit one wording in each important group. This is what
    lets a query such as“数据在技术突破上的作用” match“数据要素赋能企业关键核心
    技术突破研究” without lowering the relevance threshold for unrelated pages.
    """
    compact = re.sub(r"\s+", "", str(keyword or ""))
    seeds = list(dict.fromkeys([x for x in concepts if x] + (["数据"] if "数据" in compact else [])))
    groups: list[list[str]] = []
    covered: set[str] = set()
    for triggers, aliases in _CONCEPT_ALIAS_RULES:
        if any(trigger in compact for trigger in triggers):
            group = [alias for alias in aliases if alias]
            if group and group not in groups:
                groups.append(group)
            covered.update(t for t in triggers if t in compact)
    # Preserve concrete user phrases that are not represented by a known alias
    # family (for example“医药研发”“工业质检”“跨境物流”).
    for seed in seeds:
        if len(seed) < 2 or seed in {"数据", "研究", "分析", "作用", "影响"}:
            continue
        # A generic alias such as“数据”must not swallow a more specific user
        # concept such as“数据资产入表”. Treat a seed as already represented only
        # when it equals an alias, or when the seed itself is a meaningful substring
        # of a longer alias in that family.
        if any(seed == alias or (len(seed) >= 4 and seed in alias) for group in groups for alias in group):
            continue
        if seed in {"重要", "必要", "意义", "作用", "影响", "什么", "如何", "为什么"}:
            continue
        groups.append([seed])
    return groups[:5]



def _concept_terms(keyword: str) -> list[str]:
    """Extract retrieval concepts from a natural-language Chinese query.

    This is intentionally deterministic and cheap. It is not a tokenizer; it only
    removes common relation scaffolding and keeps concrete noun/verb phrases that
    are likely to occur in titles, abstracts or snippets.
    """
    raw = re.sub(r"[，。；：！？、（）()【】\[\]“”‘’\"']+", " ", str(keyword or "")).strip()
    compact = re.sub(r"\s+", "", raw)
    out: list[str] = []
    for term in _CONCEPT_VOCABULARY:
        if term in compact and term not in out:
            out.append(term)
    split_pattern = "(?:" + "|".join(sorted((re.escape(x) for x in _QUERY_SPLIT_RELATION_WORDS), key=len, reverse=True)) + ")"
    for piece in re.split(split_pattern, compact):
        piece = piece.strip()
        # Multi-character relation splitting intentionally preserves concrete
        # nouns such as“数据中台”, but can leave a compact relationship fragment
        # such as“数据在技术突破”. Do not promote that grammar residue into a
        # search concept; the concrete vocabulary terms already carry the intent.
        relation_piece = re.match(r"^(.{2,12}?)(?:在|对|与)(.{2,12})$", piece)
        if relation_piece:
            for side in relation_piece.groups():
                side = side.strip()
                if side in {"数据"} or not (2 <= len(side) <= 12):
                    continue
                if side not in out:
                    out.append(side)
            continue
        if re.match(r"^数据(?:和|及).+", piece):
            continue
        if 2 <= len(piece) <= 16 and piece not in out:
            out.append(piece)
    # “数据” alone is too broad, but when paired with another concept it is a
    # useful relation anchor. Put it first when the user's sentence is explicitly
    # about what data does to/for something; this produces browser-like queries
    # such as“数据 技术突破”instead of the less natural reversed wording.
    if "数据" in compact:
        if "数据" in out:
            out.remove("数据")
        out.insert(0, "数据")
    return out[:10]


def _has_relation_intent(compact: str) -> bool:
    if any(marker in compact for marker in _QUERY_CONCEPTUAL_MARKERS):
        return True
    # A bare“在/对”only counts when it participates in an actual semantic
    # relationship. This preserves questions like“数据在技术突破上的作用”while
    # avoiding false positives such as“数据中心液冷技术”.
    return bool(re.search(r"(?:对|在).{2,24}(?:作用|影响|价值|意义|促进|推动|帮助|助力)", compact))


def _conceptual_query(keyword: str, concepts: list[str]) -> bool:
    compact = re.sub(r"\s+", "", str(keyword or ""))
    has_relation = _has_relation_intent(compact)
    # Very short but explicit A→B formulations such as“数据对技术突破”do not
    # need a trailing“作用/影响”to express a relationship. Require concepts on
    # both sides so this does not revive the old single-character false positives.
    if not has_relation and len(concepts) >= 2:
        has_relation = bool(re.search(r".{2,16}(?:对|与).{2,16}", compact))
    return len(compact) >= 6 and (len(concepts) >= 2 or has_relation) and has_relation


def _semantic_query_variants(keyword: str, concepts: list[str]) -> list[str]:
    """Generate a small set of high-information browser-like rewrites.

    Variants are deliberately *diverse*, not numerous: one literal concept lane,
    one common-headline paraphrase and, when useful, the original phrase. This
    improves breadth without increasing provider-call budgets.
    """
    variants: list[str] = []
    groups = _concept_groups(keyword, concepts)
    if concepts:
        variants.append(_compact_query(*concepts[:3], limit_terms=3))
    if len(groups) >= 2:
        # Choose common alternate wording from each group instead of repeating
        # the same tokens with a different order.
        left = groups[0][1] if len(groups[0]) > 1 else groups[0][0]
        right = groups[1][1] if len(groups[1]) > 1 else groups[1][0]
        variants.append(_compact_query(left, right, limit_terms=3))
    if len(groups) >= 3:
        # Relationship questions often have a third semantic side (e.g.
        # 数据资产入表 × 融资, 可信数据空间 × 产业协同). Give that relation one
        # concise browser query without increasing the number of provider calls.
        non_generic = [g for g in groups if g and g[0] not in {"数据", "企业", "产业"}]
        if len(non_generic) >= 2:
            variants.append(_compact_query(non_generic[0][0], non_generic[1][0], limit_terms=3))
    compact = re.sub(r"\s+", "", str(keyword or ""))
    if "数据" in compact and any(x in compact for x in ("成本", "降本", "效率", "提效")):
        variants.extend([
            _compact_query("数据驱动", "降本增效", *concepts[1:2], limit_terms=3),
            _compact_query("数据赋能", *concepts[1:3], limit_terms=3),
        ])
    elif "数据" in compact and (
        any(x in compact for x in ("技术突破", "技术创新", "科技创新", "关键核心技术", "技术攻关"))
        or ("研发" in compact and any(x in compact for x in ("突破", "创新", "攻关")))
    ):
        variants.extend([
            "数据要素 技术突破",
            "数据要素 关键核心技术突破",
            "数据驱动 技术创新 研发",
        ])
    if str(keyword or "").strip():
        variants.append(str(keyword).strip())
    return list(dict.fromkeys(x for x in variants if x))[:5]


def _terms(text: str) -> list[str]:
    text = str(text or "").lower()
    values: list[str] = []
    for token in [*DOMAIN_HINTS, *SEARCH_VOCABULARY, *SOURCE_HINTS]:
        if token.lower() in text and token not in values:
            values.append(token)
    for token in re.findall(r"[“\"']([^”\"']{2,18})[”\"']", text):
        token = re.sub(r"\s+", "", token).strip()
        if token and token not in values:
            values.append(token)
    for token in re.findall(r"[a-z][a-z0-9_-]{2,}", text):
        if token not in values:
            values.append(token)
    relation_terms = []
    if "企业" in text and "价值" in text:
        relation_terms.append("企业价值")
    if "个人" in text and "价值" in text:
        relation_terms.append("个人价值")
    if "企业" in text and any(x in text for x in ("治理", "管理", "数据")):
        relation_terms.append("企业数据治理")
    if "个人" in text and any(x in text for x in ("权益", "信息", "数据")):
        relation_terms.append("个人信息权益")
    relation_terms.extend(re.findall(r"近\s*\d+\s*(?:天|日|周|个月|月|年)", text))
    relation_terms.extend(re.findall(r"20\d{2}(?:年)?", text))
    for token in relation_terms:
        token = re.sub(r"\s+", "", token).strip()
        if token and token not in values:
            values.append(token)
    return values[:36]


def _extract_excludes(description: str) -> list[str]:
    desc = str(description or "").strip()
    if not desc:
        return []
    pieces: list[str] = []
    for marker in ("不要", "不需要", "排除", "避免", "别要", "不要包含", "不看", "剔除"):
        for match in re.finditer(re.escape(marker), desc):
            tail = desc[match.end():]
            tail = re.split(r"[。；;\n]", tail, maxsplit=1)[0]
            tail = re.split(r"(?:但要|同时要|希望|最好|优先)", tail, maxsplit=1)[0]
            pieces.extend(re.split(r"[、，,和及以及]", tail))
    out: list[str] = []
    for piece in pieces:
        piece = re.sub(r"\s+", "", piece).strip()
        if 2 <= len(piece) <= 30 and piece not in out:
            out.append(piece)
    return out[:12]


def _compact_query(*terms: str, limit_terms: int = 5) -> str:
    """Build a short Tavily query instead of forwarding prompt-like prose.

    Tavily performs better when a query expresses one retrieval intent. We keep
    each provider query to a handful of concrete topic phrases and use multiple
    small queries for recall, rather than one long sentence with media/year/style
    instructions mixed into it.
    """
    out: list[str] = []
    for raw in terms:
        for token in re.split(r"[、，,;；|/\n]+", str(raw or "")):
            token = re.sub(r"\s+", " ", token).strip().strip('"“”')
            if not token or token in out:
                continue
            if len(token) > 30:
                continue
            out.append(token)
            if len(out) >= limit_terms:
                return " ".join(out)
    return " ".join(out)


def _family_terms(keyword: str, description_terms: list[str]) -> list[str]:
    compact = re.sub(r"\s+", "", keyword)
    is_data_governance = compact in {"数据", "治理", "数据要素", "数据治理", "数据要素治理"}
    if not is_data_governance:
        is_data_governance = any(term in DATA_GOVERNANCE_FAMILY for term in description_terms)
    if not is_data_governance:
        return []
    preferred = [x for x in description_terms if x in DATA_GOVERNANCE_FAMILY]
    return list(dict.fromkeys(preferred + DATA_GOVERNANCE_FAMILY))[:36]


def local_plan(keyword: str, description: str = "", region_preference: str = "") -> dict[str, Any]:
    keyword = keyword.strip()
    description = description.strip()
    base = f"{keyword} {description}".strip()
    lower = base.lower()
    keyword_terms = _terms(keyword)
    domain_found = [x for x in DOMAIN_HINTS if x.lower() in lower]
    description_terms = [x for x in _terms(description) if x not in keyword_terms]
    exclude_terms = _extract_excludes(description)

    region = "domestic-first"
    if any(x in description for x in ("只要国内", "只看国内", "中国为主", "国内优先", "国内为主")):
        region = "domestic-only" if any(x in description for x in ("只要国内", "只看国内")) else "domestic-first"
    elif any(x in description.lower() for x in ("只要国外", "只看国外", "国际为主", "海外优先")):
        region = "global-only" if any(x in description.lower() for x in ("只要国外", "只看国外")) else "global-first"
    elif any(x in description for x in ("同时补充国际", "兼顾国际", "国内外都要", "中外对比")):
        region = "domestic+global"
    if region_preference in {"domestic-only", "domestic-first", "domestic+global", "global-first", "global-only"}:
        region = region_preference

    source_preference = [x for x in SOURCE_HINTS if x.lower() in lower]
    family = _family_terms(keyword, description_terms)
    concepts = _concept_terms(keyword)
    conceptual = _conceptual_query(keyword, concepts)
    concept_groups = _concept_groups(keyword, concepts)
    semantic_variants = _semantic_query_variants(keyword, concepts) if conceptual else []
    strongest = next((x for x in description_terms + domain_found if x in DATA_GOVERNANCE_FAMILY), "")
    keyword_compact = re.sub(r"\s+", "", keyword)
    specific_keyword = ""
    if keyword_compact not in _GENERIC_TOPICS:
        concrete = [x for x in keyword_terms if x in SEARCH_VOCABULARY and len(x) >= 4]
        if concrete:
            specific_keyword = max(concrete, key=lambda x: (len(x), keyword_terms.index(x) * -1))
    if specific_keyword:
        primary = specific_keyword
    elif strongest:
        primary = strongest
    elif conceptual and concepts:
        primary = _compact_query(*concepts[:3], limit_terms=3) or keyword
    else:
        primary = keyword or "数据要素"

    # The first provider query should resemble what a human would type into a
    # browser: concrete nouns and actions, not the full question sentence. The
    # original phrase remains a secondary semantic lane.
    domestic_news = _compact_query(primary, "数据治理" if family else "", limit_terms=3)
    policy = _compact_query(primary, "数据产权", "公共数据", limit_terms=4) if family else _compact_query(primary, limit_terms=3)
    domestic_paper = _compact_query(primary, "研究", limit_terms=4)
    global_q = _compact_query(primary, "data governance" if family else "international", limit_terms=4)
    global_paper = _compact_query(primary, "research", "journal", limit_terms=4)

    news_variants: list[str] = [domestic_news, *semantic_variants]
    policy_variants: list[str] = [policy, *semantic_variants[:2]]
    if family:
        news_variants.extend([
            _compact_query("数据产权登记", "公共数据", "数据流通"),
            _compact_query("数据要素×", "可信数据空间", "数据资产"),
            _compact_query("AI数据", "数据安全", "语料"),
        ])
        policy_variants.extend([
            _compact_query("数据产权登记", "公共数据授权运营", "数据基础制度"),
            _compact_query("可信数据空间", "数据基础设施", "数据要素"),
        ])
    for term in description_terms[:4]:
        if term not in {primary, keyword} and len(term) <= 18:
            candidate = _compact_query(primary, term, limit_terms=3)
            if candidate and candidate not in news_variants:
                news_variants.append(candidate)

    news_variants = list(dict.fromkeys(x for x in news_variants if x))[:7]
    policy_variants = list(dict.fromkeys(x for x in policy_variants if x))[:4]
    paper_variants = list(dict.fromkeys([domestic_paper, *semantic_variants[:3], _compact_query(primary, "论文", "研究")]))[:5]

    # For natural-language relationship questions, the full sentence is not a
    # must-match term. Concrete concepts are the retrieval anchors; this is what
    # lets “数据在技术突破上的作用” match titles such as“数据要素赋能企业关键
    # 核心技术突破研究”.
    must_terms = [keyword] if keyword and not conceptual else concepts[:3]
    anchors = list(dict.fromkeys(([keyword] if keyword else []) + concepts + domain_found + description_terms + family))[:32]
    related = list(dict.fromkeys(concepts + domain_found + description_terms + family))[:32]
    web_variants = list(dict.fromkeys([*semantic_variants, domestic_news, keyword]))[:5]

    return {
        "intentSummary": (
            f"围绕“{keyword}”优先检索中国近期政策、权威媒体、产业实践与中文研究"
            + ("，国际资料作为补充" if region not in {"domestic-only", "global-only"} else "")
            + (f"；附加要求重点落实：{description}" if description else "。")
        ),
        "normalizedTopic": keyword,
        "mustTerms": must_terms,
        "conceptTerms": concepts,
        "conceptGroups": concept_groups,
        "isConceptualQuery": conceptual,
        "matchThreshold": 12 if conceptual else 16,
        "anchorTerms": anchors,
        "relatedTerms": related,
        "topicFamilyTerms": family,
        "excludeTerms": list(dict.fromkeys(exclude_terms)),
        "descriptionTerms": description_terms[:14],
        "sourcePreference": source_preference[:8],
        "domainContext": " ".join((family or domain_found + description_terms)[:8]),
        "domesticNewsQuery": domestic_news,
        "globalNewsQuery": global_q,
        "policyQuery": policy,
        "paperQuery": domestic_paper,
        "domesticPaperQuery": domestic_paper,
        "globalPaperQuery": global_paper,
        "newsQueryVariants": news_variants,
        "webQueryVariants": web_variants,
        "generalDiscoveryQuery": web_variants[0] if web_variants else domestic_news,
        "policyQueryVariants": policy_variants,
        "paperQueryVariants": paper_variants,
        "queryVariants": list(dict.fromkeys(news_variants + policy_variants + paper_variants + [global_q, global_paper]))[:10],
        "regionPreference": region,
        "timeIntent": "latest",
        "usedModel": False,
    }


def _needs_model_intent(description: str) -> bool:
    """Use the intent LLM only when the attached brief is genuinely complex.

    Short descriptions are handled better and faster by the deterministic planner;
    the model is reserved for multi-constraint briefs where it adds real value.
    """
    text = str(description or "").strip()
    if len(text) < 36:
        return False
    constraint_markers = ("不要", "排除", "避免", "只看", "只要", "优先", "同时", "兼顾", "补充", "对比", "近", "最近", "地区", "来源")
    clause_count = len([x for x in re.split(r"[。；;\n]", text) if x.strip()])
    return clause_count >= 2 or sum(1 for marker in constraint_markers if marker in text) >= 2


def understand(keyword: str, description: str = "", region_preference: str = "") -> dict[str, Any]:
    """Understand the brief once, while keeping provider queries compact."""
    fallback = local_plan(keyword, description, region_preference)
    cache_key = json.dumps({"q": keyword.strip(), "d": description.strip(), "region": fallback["regionPreference"]}, ensure_ascii=False, sort_keys=True)
    cached = _PLAN_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if not deepseek.available() or not description.strip() or not _needs_model_intent(description):
        _PLAN_CACHE.put(cache_key, fallback)
        return fallback
    try:
        result, _meta = deepseek.plan_search_intent(keyword, description)
        if not isinstance(result, dict):
            _PLAN_CACHE.put(cache_key, fallback)
            return fallback
        plan = dict(fallback)
        list_keys = {
            "mustTerms": 8, "anchorTerms": 32, "relatedTerms": 32,
            "excludeTerms": 12, "descriptionTerms": 14, "sourcePreference": 8,
        }
        for key, cap in list_keys.items():
            value = result.get(key)
            if isinstance(value, list):
                cleaned = [str(x).strip() for x in value if str(x).strip() and len(str(x).strip()) <= 40]
                if cleaned:
                    plan[key] = list(dict.fromkeys(([keyword] if key in {"mustTerms", "anchorTerms"} else []) + cleaned + list(fallback.get(key) or [])))[:cap]
        for key in ("intentSummary", "normalizedTopic", "domainContext", "timeIntent"):
            value = result.get(key)
            if value is not None and str(value).strip():
                plan[key] = str(value).strip()[:500]

        # Search-provider queries stay under local control. The model may explain
        # intent and add anchor terms, but it cannot turn a concise query into a
        # prompt paragraph or silently make the user's phrase an exact-match gate.
        plan["regionPreference"] = fallback["regionPreference"]
        # Long natural-language questions are semantic intents, not exact-match
        # phrases. Keep their extracted concepts as the must-group while retaining
        # the original wording only as an anchor.
        if fallback.get("isConceptualQuery"):
            plan["mustTerms"] = list(dict.fromkeys(list(fallback.get("mustTerms") or []) + [x for x in plan.get("mustTerms") or [] if x != keyword]))[:8]
        else:
            plan["mustTerms"] = list(dict.fromkeys(([keyword] if keyword else []) + list(plan.get("mustTerms") or [])))[:8]
        plan["conceptGroups"] = list(fallback.get("conceptGroups") or [])[:5]
        plan["anchorTerms"] = list(dict.fromkeys(([keyword] if keyword else []) + list(plan.get("anchorTerms") or []) + list(fallback.get("topicFamilyTerms") or [])))[:32]
        plan["relatedTerms"] = list(dict.fromkeys(list(plan.get("relatedTerms") or []) + list(fallback.get("topicFamilyTerms") or [])))[:32]
        plan["usedModel"] = True
        _PLAN_CACHE.put(cache_key, plan)
        return plan
    except Exception:
        _PLAN_CACHE.put(cache_key, fallback)
        return fallback
