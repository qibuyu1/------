from __future__ import annotations

from datetime import date, timedelta
import base64
import io

from PIL import Image, ImageDraw


def svg_data_uri(label: str, index: int) -> str:
    """Return a distinct PNG data URI for offline/demo rendering.

    PNG is intentional: browser preview, DOCX and PDF all use the same raster
    bytes, so offline exports do not collapse different SVGs into one generic
    fallback image.
    """
    themes = [
        ((231, 242, 255), (150, 190, 224), (36, 95, 150)),
        ((246, 251, 255), (199, 222, 241), (53, 108, 154)),
        ((234, 245, 255), (120, 172, 211), (31, 91, 139)),
        ((243, 248, 255), (171, 205, 231), (45, 104, 143)),
    ]
    top, bottom, ink = themes[index % len(themes)]
    width, height = 1200, 675
    image = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    # large soft white shape
    draw.ellipse((900, -190, 1390, 300), fill=(245, 250, 255))
    kind = index % 4
    if kind == 0:  # network / data flow
        nodes = [(250, 280, 76), (545, 185, 55), (835, 315, 68), (1020, 188, 44)]
        for x, y, r in nodes:
            draw.ellipse((x-r, y-r, x+r, y+r), fill=(250, 253, 255), outline=(215, 232, 246), width=3)
        draw.line((325, 265, 490, 205), fill=ink, width=8)
        draw.line((600, 208, 775, 295), fill=ink, width=8)
        draw.line((900, 285, 982, 212), fill=ink, width=8)
        for x, y, _ in nodes:
            draw.ellipse((x-13, y-13, x+13, y+13), fill=ink)
        ascii_label = "TRUSTED DATA FLOW"
    elif kind == 1:  # policy / document
        draw.rounded_rectangle((670, 115, 1030, 535), radius=20, fill=(250, 253, 255), outline=(213, 231, 245), width=3)
        draw.rounded_rectangle((715, 175, 935, 195), radius=8, fill=ink)
        for y, w in [(235, 260), (275, 230), (315, 270)]:
            draw.rounded_rectangle((715, y, 715+w, y+12), radius=6, fill=(177, 205, 226))
        draw.ellipse((850, 370, 960, 480), outline=ink, width=10)
        draw.line((875, 425, 905, 452, 945, 398), fill=ink, width=10)
        ascii_label = "POLICY & RULES"
    elif kind == 2:  # industry / growth
        x0 = 650
        bars = [(x0, 405, 105, 120), (x0+145, 315, 105, 210), (x0+290, 220, 105, 305)]
        for x, y, w, h in bars:
            draw.rounded_rectangle((x, y, x+w, y+h), radius=10, fill=(247, 252, 255), outline=(209, 229, 244), width=3)
        draw.line((675, 365, 805, 300, 900, 250, 1040, 160), fill=ink, width=10)
        draw.polygon([(1040, 160), (1008, 165), (1026, 192)], fill=ink)
        ascii_label = "INDUSTRY VALUE"
    else:  # research / trend
        draw.rounded_rectangle((630, 125, 1050, 505), radius=28, fill=(250, 253, 255), outline=(213, 231, 245), width=3)
        draw.line((685, 430, 685, 190), fill=(170, 200, 222), width=3)
        draw.line((685, 430, 995, 430), fill=(170, 200, 222), width=3)
        points = [(705, 390), (770, 330), (830, 350), (900, 250), (980, 195)]
        draw.line(points, fill=ink, width=10)
        for x, y in points:
            draw.ellipse((x-10, y-10, x+10, y+10), fill=ink)
        ascii_label = "RESEARCH INSIGHT"
    # ASCII footer stays portable even when the runtime has no Chinese fonts.
    draw.text((72, 540), ascii_label, fill=(23, 53, 78))
    draw.text((72, 570), "DATA ELEMENT GOVERNANCE - OFFLINE VISUAL", fill=(76, 111, 140))
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def get_demo_results(query: str) -> dict:
    today = date.today()
    samples = [
        ("policy", "关于构建数据基础制度更好发挥数据要素作用的政策脉络", "政策观察", 1, 96),
        ("news", "数据要素市场化配置进入精细化运营阶段：从确权到可信流通", "行业研究", 3, 91),
        ("paper", "Data as a Factor of Production: Governance, Valuation and Market Design", "OpenAlex · Demo", 18, 89),
        ("news", "公共数据授权运营加速，多地探索场景化开发与收益分配", "数字经济前沿", 6, 87),
        ("policy", "可信数据空间建设：规则、技术与产业协同的三个关键接口", "政策观察", 12, 92),
        ("paper", "Trusted Data Spaces and Data Market Governance: A Systematic Review", "OpenAlex · Demo", 45, 85),
        ("news", "企业数据资产入表之后：治理、估值和业务闭环成为新重点", "产业数字化周刊", 9, 84),
        ("paper", "Mechanism Design for Data Exchange Platforms under Privacy Constraints", "OpenAlex · Demo", 120, 82),
    ]
    results = []
    for idx, (kind, title, source, age, score) in enumerate(samples):
        results.append(
            {
                "id": f"demo-{idx}",
                "type": kind,
                "title": title,
                # Never send users to example.com. Offline rows are intentionally
                # non-clickable and visibly labelled as demonstration data.
                "url": "",
                "pdfUrl": "",
                "source": source,
                "publishedAt": str(today - timedelta(days=age)),
                "snippet": f"围绕“{query}”的示例资料。该条目仅用于无 API Key 时演示检索、筛选、资料篮和公众号写作流程；配置 Tavily 后会替换为真实网页结果。",
                "rawContent": f"这是关于 {query} 的演示资料正文摘要，重点涉及数据权利配置、流通利用、收益分配、合规治理、可信数据空间与数据资产化。",
                "authors": ["Demo Research Group"] if kind == "paper" else [],
                "citations": 36 + idx * 7 if kind == "paper" else None,
                "openAccess": True if kind == "paper" else None,
                "relevance": score / 100,
                "authorityScore": min(99, score + 4),
                "freshnessScore": max(50, 98 - age),
                "score": score,
                "images": [],
                "demo": True,
            }
        )
    images = [
        {"url": svg_data_uri(f"{query} · 数据连接", 0), "description": "数据节点连接与可信流通示意"},
        {"url": svg_data_uri(f"{query} · 规则治理", 1), "description": "政策规则与合规治理示意"},
        {"url": svg_data_uri(f"{query} · 产业增长", 2), "description": "产业应用与价值增长示意"},
        {"url": svg_data_uri(f"{query} · 研究分析", 3), "description": "研究分析与趋势判断示意"},
    ]
    return {
        "query": query,
        "answer": f"当前为演示模式。已围绕“{query}”构造政策、新闻和论文混合结果；配置 Tavily API Key 后将进行实时检索。",
        "results": results,
        "images": images,
        "demo": True,
        "warnings": ["未配置 TAVILY_API_KEY，网页/新闻结果为演示数据。"],
    }
