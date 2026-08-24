from __future__ import annotations


def plan_visual(text: str, title: str = "", context: dict | None = None) -> dict:
    """视觉策划器：先判断内容承担的信息职责，再决定视觉类型。

    不再简单按关键词配图，优先让图片承担解释功能。
    """
    content = f"{title} {text}"
    context = context or {}

    rules = [
        (("因果", "为什么", "原因", "影响", "机制", "逻辑", "路径", "链路", "闭环", "流程"),
         "mechanism_diagram", "解释变量之间关系"),
        (("架构", "体系", "框架", "生命周期", "全流程", "生态"),
         "framework_diagram", "展示结构体系"),
        (("增长", "趋势", "规模", "变化", "提升", "下降", "占比"),
         "trend_chart", "展示趋势变化"),
        (("发布", "大会", "签约", "会议", "企业宣布", "获奖"),
         "news_photo", "证明真实事件"),
        (("场景", "应用", "落地", "工厂", "城市", "医疗", "金融"),
         "scene_illustration", "展示应用环境"),
    ]

    # 视觉多样化：同一篇文章不应连续输出同一种 AI 插画。
    # 根据内容职责选择不同视觉语言，避免“最后几张图画风完全一致”。
    style_map = {
        "mechanism_diagram": {
            "style": "editorial_infographic",
            "palette": "business_report",
            "detail": "高清信息图，清晰箭头和层级关系，不使用科技炫光",
        },
        "framework_diagram": {
            "style": "clean_framework",
            "palette": "white_background",
            "detail": "咨询报告风格结构图，突出模块关系",
        },
        "trend_chart": {
            "style": "data_visualization",
            "palette": "professional_chart",
            "detail": "数据图表风格，避免插画化",
        },
        "scene_illustration": {
            "style": "documentary_scene",
            "palette": "natural_photo",
            "detail": "真实产业场景摄影感，不生成泛 AI 背景",
        },
        "news_photo": {
            "style": "news_media_photo",
            "palette": "real_world",
            "detail": "优先使用新闻来源图片，不使用 AI 生成替代",
        },
    }

    for keys, kind, reason in rules:
        if any(k in content for k in keys):
            result = {
                "type": kind,
                "reason": reason,
                "priority": "high",
                "avoid": ["无关科技背景图", "纯装饰图片", "连续重复画风"],
            }
            result.update(style_map.get(kind, {}))
            return result

    return {
        "type": "concept_illustration",
        "reason": "辅助理解抽象概念",
        "priority": "medium",
        "style": "editorial_concept_art",
        "palette": "varied",
        "detail": "根据文章主题变化视觉风格，不固定科技蓝色调",
        "avoid": ["无主题关联图片", "模板化 AI 科技背景"],
    }
