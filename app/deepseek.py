from __future__ import annotations

import json
import re
from typing import Any

from .config import settings
from .http_client import UpstreamError, request_json


def available() -> bool:
    return bool(settings.deepseek_api_key)


def _v4_thinking_fields(reasoning_effort: str = "off") -> dict[str, Any]:
    """Return explicit thinking controls for all DeepSeek V4 variants."""
    model = str(settings.deepseek_model or "").lower()
    if not model.startswith("deepseek-v4"):
        return {}
    enabled = str(reasoning_effort or "off").lower() in {"high", "max"}
    out: dict[str, Any] = {"thinking": {"type": "enabled" if enabled else "disabled"}}
    if enabled:
        out["reasoning_effort"] = "max" if str(reasoning_effort).lower() == "max" else "high"
    return out



def _extract_partial_json_markdown(text: str) -> str:
    """Recover a long markdown string from a JSON object truncated near the end.

    Long-form JSON can be cut after most of the article has already been emitted.
    Do not throw away that usable prose merely because a later field or closing brace
    is missing. This parser only reads the JSON string assigned to ``markdown`` and
    never interprets arbitrary object structure.
    """
    value = str(text or "")
    match = re.search(r'"markdown"\s*:\s*"', value)
    if not match:
        return ""
    start = match.end()
    escaped = False
    end = len(value)
    for i in range(start, len(value)):
        ch = value[i]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            end = i
            break
    raw = value[start:end]
    # Appending a quote turns a truncated-but-complete JSON string into a value we
    # can decode. If the provider stopped in the middle of an escape sequence, trim
    # only the incomplete tail rather than discarding the whole article.
    for trim in range(0, min(8, len(raw)) + 1):
        candidate = raw[:-trim] if trim else raw
        try:
            decoded = json.loads('"' + candidate + '"')
            return str(decoded or "").strip()
        except Exception:
            continue
    return ""

def generate_json(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 7000,
    temperature: float = 0.55,
    reasoning_effort: str = "off",
) -> dict[str, Any]:
    """Reliable long-form generation with a strict two-stage ceiling.

    Normal path is one request. If structured JSON is empty/truncated/invalid, a
    second plain-Markdown request salvages the article. Transient transport errors
    on the first request receive one HTTP-level retry; invalid model content does
    not loop repeatedly. This keeps the common path fast while preventing a single
    brief upstream hiccup from turning into a failed article.
    """
    if not available():
        raise UpstreamError("DEEPSEEK_API_KEY is not configured")
    strict_system = system_prompt + "\n\nIMPORTANT: Output valid JSON only. The word JSON is intentionally present in this instruction."
    example_hint = '\nExpected JSON shape example: {"titleCandidates":["..."],"deck":"...","markdown":"..."}'
    attempts = [
        (True, strict_system, user_prompt + example_hint, reasoning_effort),
        (False, system_prompt, user_prompt + "\n如果结构化输出失败，直接返回完整 Markdown 正文；必须包含标题和正文小标题，不要空白，不要解释失败原因。", "off"),
    ]
    last_err: Exception | None = None
    first_nonempty = ""
    for idx, (json_mode, sys_prompt, usr_prompt, effort) in enumerate(attempts, start=1):
        thinking = _v4_thinking_fields(effort)
        payload: dict[str, Any] = {
            "model": settings.deepseek_model,
            "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": usr_prompt}],
            "max_tokens": max(900, int(max_tokens)),
            "stream": False,
            **thinking,
        }
        if thinking.get("thinking", {}).get("type") != "enabled":
            payload["temperature"] = max(0.0, min(float(temperature), 1.5))
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            data = request_json(
                f"{settings.deepseek_base_url}/chat/completions",
                method="POST",
                headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                payload=payload,
                timeout=100 if idx == 1 else 90,
                retries=1 if idx == 1 else 0,
            )
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = message.get("content")
            usage = data.get("usage") or {}
            meta = {
                "apiCalled": True, "attempt": idx,
                "model": str(data.get("model") or settings.deepseek_model),
                "finishReason": str(choice.get("finish_reason") or ""),
                "promptTokens": int(usage.get("prompt_tokens") or 0),
                "completionTokens": int(usage.get("completion_tokens") or 0),
                "reasoningTokens": int(((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0),
                "totalTokens": int(usage.get("total_tokens") or 0),
                "promptCacheHitTokens": int(usage.get("prompt_cache_hit_tokens") or 0),
                "promptCacheMissTokens": int(usage.get("prompt_cache_miss_tokens") or 0),
            }
            text = str(content or "").strip()
            if not text:
                last_err = UpstreamError("DeepSeek returned empty content")
                continue
            if idx == 1:
                first_nonempty = text
            if json_mode:
                try:
                    parsed = _parse_json(text)
                except Exception as exc:
                    # Some gateways ignore JSON mode and return a perfectly usable
                    # Markdown article. Reuse it immediately instead of paying for
                    # another long request. A JSON-looking/truncated object still
                    # goes to the dedicated plain-text salvage call.
                    if len(text) >= 500 and not re.match(r"^\s*(?:```json\s*)?\{", text, flags=re.I):
                        parsed = _text_to_article_payload(text)
                    else:
                        last_err = exc
                        continue
            else:
                parsed = _text_to_article_payload(text)
            if not str(parsed.get("markdown") or "").strip():
                last_err = UpstreamError("DeepSeek response did not contain article text")
                continue
            parsed["_deepseekMeta"] = meta
            return parsed
        except Exception as exc:
            last_err = exc

    # Last-chance local salvage. A long JSON response is sometimes truncated only
    # after most of the markdown field has already arrived. Recover that field rather
    # than failing the whole article if the dedicated second request also failed.
    if first_nonempty:
        partial_markdown = _extract_partial_json_markdown(first_nonempty)
        if len(partial_markdown) >= 500:
            parsed = _text_to_article_payload(partial_markdown)
            parsed["_deepseekMeta"] = {"apiCalled": True, "attempt": 1, "model": settings.deepseek_model, "finishReason": "partial-json-salvage", "totalTokens": 0}
            return parsed
        if len(first_nonempty) >= 700 and not re.match(r"^\s*(?:```json\s*)?\{", first_nonempty, flags=re.I):
            parsed = _text_to_article_payload(first_nonempty)
            if str(parsed.get("markdown") or "").strip():
                parsed["_deepseekMeta"] = {"apiCalled": True, "attempt": 1, "model": settings.deepseek_model, "finishReason": "local-salvage", "totalTokens": 0}
                return parsed
    raise UpstreamError(f"DeepSeek 生成失败：{str(last_err or 'unknown error')[:300]}")



def plan_search_intent(keyword: str, description: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Low-cost, non-thinking intent planner for search briefs."""
    if not available():
        raise UpstreamError("DEEPSEEK_API_KEY is not configured")
    system = (
        "你是搜索需求分析器，不回答问题、不写文章，只把用户的检索需求拆成可执行的搜索计划。"
        "必须保持用户关键词原意，不能为了‘数据要素治理’而偷偷改题；只有当关键词过于宽泛时，才可用数据要素/治理/流通/资产/公共数据/数字经济作为领域边界。只输出合法JSON。"
        "默认执行中国来源优先：国家部委和地方政府、国内权威媒体、产业实践、中文论文在前，国际资料仅作补充；只有用户明确要求时才调整地区顺序。"
    )
    user = (
        "关键词：\n" + str(keyword).strip() +
        "\n附加描述：\n" + str(description).strip() +
        "\n\n任务：把用户描述转换成检索执行计划，不要回答主题本身。必须保留关键词原意；"
        "不要把‘数据要素治理’强行追加到与用户无关的主题。需要判断用户真正想看的对象、范围、地区、来源偏好、排除项以及时间意图。"
        "分别为国内新闻、国际补充新闻、国内政策、中文论文和国际补充论文生成可直接交给搜索引擎的短查询句。"
        "如果用户描述里有‘不要/排除/避免’，必须写入 excludeTerms。"
        "必须同时生成 queryVariants，覆盖不同表达但仍围绕同一主题。"
        "输出字段：intentSummary、normalizedTopic、mustTerms、anchorTerms、relatedTerms、descriptionTerms、excludeTerms、sourcePreference、domainContext、domesticNewsQuery、globalNewsQuery、policyQuery、paperQuery、domesticPaperQuery、globalPaperQuery、queryVariants、regionPreference、timeIntent。"
        "mustTerms最多8个，anchorTerms/relatedTerms/descriptionTerms最多14个，excludeTerms最多12个，queryVariants最多8个。"
    )
    data = request_json(
        f"{settings.deepseek_base_url}/chat/completions",
        method="POST",
        headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
        payload={
            "model": settings.deepseek_model,
            "messages": [{"role":"system","content":system},{"role":"user","content":user}],
            "temperature": 0.1,
            "max_tokens": 520,
            "stream": False,
            "response_format": {"type":"json_object"},
            **_v4_thinking_fields("off"),
        },
        timeout=18,
        retries=0,
    )
    choice=(data.get("choices") or [{}])[0]
    content=((choice.get("message") or {}).get("content") or "").strip()
    parsed=_parse_json(content)
    usage=data.get("usage") or {}
    meta={"apiCalled":True,"model":data.get("model") or settings.deepseek_model,"promptTokens":int(usage.get("prompt_tokens") or 0),"completionTokens":int(usage.get("completion_tokens") or 0),"totalTokens":int(usage.get("total_tokens") or 0)}
    return parsed, meta


def plan_writing_brief(query: str, angle: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Low-cost non-thinking editor-brief interpreter for the user's actual writing request."""
    if not available():
        raise UpstreamError("DEEPSEEK_API_KEY is not configured")
    system = (
        "你是中文公众号编辑的需求分析器。你只理解写作需求，不写文章。"
        "不要复述用户原话，要把真实目的、必须满足的要求、明确不要的内容、读者需求和结构策略拆出来。"
        "不得添加用户没有要求的具体事实。只输出合法JSON。"
    )
    user = (
        f"主题：{str(query).strip()}\n"
        f"用户写作切口/实际要求：{str(angle).strip()}\n\n"
        "输出字段：objective、mustInclude、avoid、readerNeed、stance、structureAdvice。"
        "objective/readerNeed/stance/structureAdvice各不超过220字；mustInclude和avoid各最多8条。"
    )
    data=request_json(
        f"{settings.deepseek_base_url}/chat/completions", method="POST",
        headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
        payload={
            "model": settings.deepseek_model,
            "messages": [{"role":"system","content":system},{"role":"user","content":user}],
            "temperature": 0.1, "max_tokens": 420, "stream": False,
            "response_format": {"type":"json_object"},
            **_v4_thinking_fields("off"),
        }, timeout=16, retries=0,
    )
    content=((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    parsed=_parse_json(content)
    usage=data.get("usage") or {}
    meta={"apiCalled":True,"model":data.get("model") or settings.deepseek_model,
          "promptTokens":int(usage.get("prompt_tokens") or 0),"completionTokens":int(usage.get("completion_tokens") or 0),
          "totalTokens":int(usage.get("total_tokens") or 0)}
    return parsed, meta

def plan_visuals(query: str, slots: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Plan several code visuals in one small non-thinking request.

    The model never writes or executes Python. It returns a constrained visual DSL
    which the trusted renderer validates and draws. One batch request serves every
    diagram-eligible slot, avoiding a model call per image.
    """
    if not available() or not slots:
        return {"plans": []}, {"apiCalled": False, "totalTokens": 0}
    compact = []
    for slot in slots[:8]:
        compact.append({
            "slotId": str(slot.get("slotId") or "")[:80],
            "heading": str(slot.get("afterHeading") or "")[:140],
            "paragraph": str(slot.get("contextText") or slot.get("anchorText") or "")[:1200],
            "purpose": str(slot.get("purpose") or "")[:180],
        })
    system = (
        "你是公众号编辑部的信息可视化策划，不画图、不写Python，只输出JSON视觉DSL。"
        "所有节点、关系、数字必须来自给定段落，不得补造事实。优先把关系画清楚，而不是装饰。"
    )
    user = (
        f"主题：{query}\n待规划位置：{json.dumps(compact, ensure_ascii=False)}\n\n"
        "为每个位置选择 flow/causal/compare/layered/network/timeline/kpi/matrix/relation/concept 之一。"
        "若段落存在主体间作用、因果、依赖、流转但不适合固定模板，优先 relation，并明确 edges。"
        "每图3—6个核心节点，节点必须覆盖该段真正的主语/对象/关键动作。关系图至少给出2条明确edges；流程图节点按实际先后；因果图必须区分原因与结果；对比图必须给出左右两组对应项。避免把整段文字塞进图。没有真实数字不得选择kpi。"
        '返回 {"plans":[{"slotId":"...","visualType":"relation","title":"...","center":"...",'
        '"nodes":["..."],"edges":[{"from":"...","to":"...","label":"..."}],'
        '"left":[],"right":[],"layers":[],"events":[],"metrics":[]}]}。'
    )
    data = request_json(
        f"{settings.deepseek_base_url}/chat/completions", method="POST",
        headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
        payload={
            "model": settings.deepseek_model,
            "messages": [{"role":"system","content":system},{"role":"user","content":user}],
            "temperature": 0.1, "max_tokens": 1100, "stream": False,
            "response_format": {"type":"json_object"},
            **_v4_thinking_fields("off"),
        }, timeout=28, retries=0,
    )
    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    parsed = _parse_json(content)
    usage = data.get("usage") or {}
    meta = {"apiCalled":True,"model":data.get("model") or settings.deepseek_model,
            "promptTokens":int(usage.get("prompt_tokens") or 0),"completionTokens":int(usage.get("completion_tokens") or 0),
            "totalTokens":int(usage.get("total_tokens") or 0)}
    return parsed, meta


def _text_to_article_payload(text: str) -> dict[str, Any]:
    text = str(text or "").strip()
    # Even the plain-text salvage attempt can return a valid JSON object prefixed
    # with the literal word “json”. Recover it here so transport syntax can never
    # leak into the reader-facing article.
    try:
        parsed = _parse_json(text)
        if isinstance(parsed, dict) and isinstance(parsed.get("markdown"), str):
            return parsed
    except Exception:
        pass
    title = ""
    lines = text.splitlines()
    for line in lines[:20]:
        m = re.match(r"^\s{0,3}#{1,3}\s+(.+?)\s*$", line)
        if m:
            title = m.group(1).strip()
            break
    body = text
    body = re.sub(r"^```(?:markdown|md|text)?\s*", "", body, flags=re.I)
    body = re.sub(r"\s*```$", "", body)
    return {
        "titleCandidates": [title or "真正值得关注的变化，可能不在表面"],
        "deck": "",
        "markdown": body,
        "coverBrief": "主题相关真实产业、政策或研究场景",
        "imageQueries": [],
        "imageSlots": [],
        "socialSummary": "",
        "keyClaims": [],
        "riskNotes": ["本稿经过结构化兜底解析，请在发布前检查段落与来源。"],
        "sourceNotes": [],
    }


def _parse_json(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    text = str(content or "").strip()
    if not text:
        raise UpstreamError("DeepSeek returned empty content")
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S | re.I)
    if fenced:
        obj = json.loads(fenced.group(1))
        if isinstance(obj, dict):
            return obj
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        obj = json.loads(text[start:end + 1])
        if isinstance(obj, dict):
            return obj
    raise UpstreamError("DeepSeek returned non-JSON content")
