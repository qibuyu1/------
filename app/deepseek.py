from __future__ import annotations

import json
import re
from typing import Any

from .config import settings
from .http_client import UpstreamError, request_json


def available() -> bool:
    return bool(settings.deepseek_api_key)


def generate_json(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 7000,
    temperature: float = 0.55,
    reasoning_effort: str = "high",
) -> dict[str, Any]:
    """Reliable structured generation.

    DeepSeek V4 supports JSON Output, but the API docs note that JSON mode can
    occasionally return empty content. We therefore retry with a stricter prompt,
    then fall back to a plain-text generation that is wrapped into the expected
    article schema. A failed parse must never turn a successful model request into
    a dead-end UI state.
    """
    if not available():
        raise UpstreamError("DEEPSEEK_API_KEY is not configured")
    strict_system = system_prompt + "\n\nIMPORTANT: Output valid JSON only. Include the literal word JSON in your response instructions context."
    example_hint = '\nExpected JSON shape example: {"titleCandidates":["..."],"deck":"...","markdown":"..."}'
    fast_mode = reasoning_effort in {"off", "none", "disabled", "fast"}
    effort = reasoning_effort if reasoning_effort in {"high", "max"} else "high"
    attempts = [
        (True, "off" if fast_mode else effort, strict_system, user_prompt + example_hint),
        (True, "off" if fast_mode else effort, strict_system, user_prompt + example_hint + "\nDo not leave content empty. Return a complete JSON object."),
        (False, "off", system_prompt, user_prompt + "\nReturn the complete article in Markdown/text, not a refusal and not an empty response."),
    ]
    last_err: Exception | None = None
    for idx, (json_mode, effort, sys_prompt, usr_prompt) in enumerate(attempts, start=1):
        payload: dict[str, Any] = {
            "model": settings.deepseek_model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": usr_prompt},
            ],
            "temperature": max(0.0, min(float(temperature), 1.5)),
            "max_tokens": max(900, int(max_tokens)),
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if settings.deepseek_model == "deepseek-v4-flash":
            thinking_enabled = not (effort == "off")
            payload["thinking"] = {"type": "enabled" if thinking_enabled else "disabled"}
            if thinking_enabled:
                payload["reasoning_effort"] = effort
        try:
            data = request_json(
                f"{settings.deepseek_base_url}/chat/completions",
                method="POST",
                headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                payload=payload,
                timeout=180 if idx < 3 else 120,
                retries=1,
            )
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = message.get("content")
            usage = data.get("usage") or {}
            meta = {
                "apiCalled": True,
                "attempt": idx,
                "model": str(data.get("model") or settings.deepseek_model),
                "finishReason": str(choice.get("finish_reason") or ""),
                "promptTokens": int(usage.get("prompt_tokens") or 0),
                "completionTokens": int(usage.get("completion_tokens") or 0),
                "reasoningTokens": int(((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0),
                "totalTokens": int(usage.get("total_tokens") or 0),
                "promptCacheHitTokens": int(usage.get("prompt_cache_hit_tokens") or 0),
                "promptCacheMissTokens": int(usage.get("prompt_cache_miss_tokens") or 0),
            }
            if content is None or not str(content).strip():
                last_err = UpstreamError("DeepSeek returned empty content")
                continue
            if json_mode:
                parsed = _parse_json(content)
                parsed["_deepseekMeta"] = meta
                return parsed
            # Plain text salvage path: wrap the model output into the minimal shape
            # expected by the rest of the editorial pipeline.
            parsed = _text_to_article_payload(str(content))
            parsed["_deepseekMeta"] = meta
            return parsed
        except Exception as exc:
            last_err = exc
            continue
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
            **({"thinking":{"type":"disabled"}} if settings.deepseek_model == "deepseek-v4-flash" else {}),
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
            **({"thinking":{"type":"disabled"}} if settings.deepseek_model == "deepseek-v4-flash" else {}),
        }, timeout=16, retries=0,
    )
    content=((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    parsed=_parse_json(content)
    usage=data.get("usage") or {}
    meta={"apiCalled":True,"model":data.get("model") or settings.deepseek_model,
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
