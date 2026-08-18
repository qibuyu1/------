import re
import unittest
from pathlib import Path
from unittest.mock import patch

from app import image_fetch
from app.source_verify import title_similarity

ROOT = Path(__file__).resolve().parents[1]

class RegressionTests(unittest.TestCase):
    def test_svg_is_rejected_not_fabricated(self):
        with patch.object(image_fetch, "_validate_public_http_url"), patch.object(image_fetch, "build_opener") as opener:
            resp = type("R", (), {
                "geturl": lambda self: "https://images.example.org/a.svg",
                "headers": type("H", (), {"get_content_type": lambda self: "image/svg+xml"})(),
                "read": lambda self, n: b"<svg></svg>",
                "__enter__": lambda self: self,
                "__exit__": lambda *args: None,
            })()
            opener.return_value.open.return_value = resp
            with self.assertRaises(image_fetch.ImageFetchError):
                image_fetch.fetch_image("https://images.example.org/a.svg")

    def test_home_carousel_has_visible_initial_transform(self):
        css = (ROOT / "web" / "styles.css").read_text()
        self.assertIn(".carousel-track{display:flex;will-change:transform;transform:translate3d(0,0,0)}", css)

    def test_compose_has_custom_image_count_and_draft_restore(self):
        js = (ROOT / "web" / "compose.js").read_text()
        self.assertIn("function getBodyImageCount()", js)
        self.assertIn('sessionStorage.setItem("deg.articleId", article.articleId)', js)
        self.assertIn("restoreDraftIfAvailable", js)

    def test_search_has_attached_description_and_understanding_output(self):
        html=(ROOT / "web" / "search.html").read_text()
        js=(ROOT / "web" / "search.js").read_text()
        self.assertIn('id="searchDescription"', html)
        self.assertIn("description", js)
        self.assertIn("检索策略", js)
        self.assertIn('id="regionPreference"', html)

    def test_no_demo_placeholder_runtime_path(self):
        source = "\n".join(p.read_text() for p in (ROOT / "app").glob("*.py"))
        self.assertNotIn("_placeholder_png", source)
        self.assertNotIn("演示视觉", source)

    def test_unrelated_titles_are_low_similarity(self):
        self.assertLess(title_similarity("可信数据空间建设", "今晚的天气预报与道路拥堵提醒"), 0.16)

if __name__ == "__main__":
    unittest.main()

class V19DeepSearchTests(unittest.TestCase):
    def test_generic_data_title_without_governance_anchor_is_rejected(self):
        from app.pipeline import _query_match_score
        row={"title":"全球数据中心冷却材料市场增长预测","snippet":"工业材料与设备市场分析","source":"market.example"}
        self.assertLess(_query_match_score("数据", row, intent={"mustTerms":["数据"],"anchorTerms":["数据"],"relatedTerms":[],"descriptionTerms":[],"excludeTerms":[]}), 22)

    def test_attached_description_changes_search_plan_and_keeps_topic(self):
        from app.query_intent import local_plan
        plan=local_plan("数据", "只看近30天中国政策与主流媒体，排除数据中心和消费互联网，优先公共数据授权运营")
        self.assertIn("数据", plan["mustTerms"])
        self.assertTrue(any("数据中心" in x for x in plan["excludeTerms"]))
        self.assertTrue(any("公共数据授权运营" in x or "公共数据" in x for x in plan["descriptionTerms"]+plan["anchorTerms"]))

    def test_paper_query_plan_is_not_data_governance_hardcoded(self):
        from app.query_intent import local_plan
        plan=local_plan("低空经济", "优先近两年的中英文论文")
        self.assertIn("低空经济", plan["paperQuery"])
        self.assertNotIn("数据要素", plan["paperQuery"])

    def test_compose_auto_evidence_path_does_not_reference_undefined_intent(self):
        from app import pipeline, deepseek, tavily
        with patch.object(deepseek, "available", return_value=True), \
             patch.object(deepseek, "generate_json", return_value={
                 "titleCandidates":["低空经济正在发生的变化"], "deck":"导语", "markdown":"## 变化\n\n" + ("这是一段围绕低空经济的解释。"*80),
                 "understoodBrief":"聚焦政策与产业变化", "coverBrief":"真实低空经济产业场景", "imageQueries":[], "imageSlots":[],
                 "socialSummary":"简介", "keyClaims":[], "riskNotes":[], "sourceNotes":[]}), \
             patch.object(tavily, "available", return_value=False), \
             patch.object(pipeline, "_start_visual_job"):
            article=pipeline.generate_article({"query":"低空经济","sources":[],"options":{"autoEvidence":True,"imageCount":0,"length":"短篇 · 1000—1400字"}})
        self.assertIn("articleId", article)
        self.assertEqual(article.get("demo"), False)

    def test_writing_brief_is_displayable_without_polluting_markdown(self):
        from app.brief import local_brief
        plan=local_brief("不要泛泛解释，希望回答企业为什么改变数据资产管理方式；结尾给出三个观察信号", "数据资产")
        self.assertTrue(plan["avoid"])
        self.assertTrue(plan["mustInclude"])


class V20DomesticFirstTests(unittest.TestCase):
    def test_default_plan_is_domestic_first_and_expands_generic_topic(self):
        from app.query_intent import local_plan
        plan = local_plan(
            "数据",
            "我们主要研究数据要素治理，关注数据对企业和个人的价值与重要性，优先政策和近期新闻",
        )
        self.assertEqual(plan["regionPreference"], "domestic-first")
        self.assertIn("数据要素", plan["domesticNewsQuery"])
        self.assertIn("数据治理", plan["domesticNewsQuery"])
        self.assertLessEqual(len(plan["domesticNewsQuery"].split()), 3)
        self.assertTrue(any("数据产权登记" in q for q in plan["newsQueryVariants"]))
        self.assertTrue(any("AI数据" in q for q in plan["newsQueryVariants"]))
        self.assertNotIn("研究数据要素治理", plan["domesticNewsQuery"])
        self.assertNotIn("关注数据对企业和", plan["domesticNewsQuery"])

    def test_indexed_result_survives_anti_bot_but_not_title_mismatch(self):
        from app.source_verify import Verification, verify_results
        row = {
            "id": "cn-1", "type": "policy", "title": "国家数据局推进数据产权登记",
            "url": "https://www.gov.cn/zhengce/2026/a.html", "source": "gov.cn",
            "snippet": "政策明确数据产权登记、数据资产入表和流通利用的实施要求。",
            "provider": "tavily", "relevance": 0.82, "queryMatchScore": 76, "authorityScore": 96,
        }
        with patch("app.source_verify.verify_url", return_value=Verification(False, row["url"], row["url"], "", "", 403, "HTTP 403")):
            kept, _ = verify_results([row], limit=1)
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0]["sourceUsable"])
        self.assertEqual(kept[0]["sourceStatus"], "indexed")
        with patch("app.source_verify.verify_url", return_value=Verification(False, row["url"], row["url"], "无关页面", "", 200, "网页标题与检索标题关联度过低(0.01)")):
            rejected, _ = verify_results([row], limit=1)
        self.assertEqual(rejected, [])

    def test_default_region_quota_keeps_domestic_primary(self):
        from app.pipeline import _apply_region_quota
        rows = []
        for i in range(8):
            rows.append({"id": f"d{i}", "originRegion": "domestic", "queryMatchScore": 90 - i, "score": 90 - i})
        for i in range(6):
            rows.append({"id": f"g{i}", "originRegion": "global", "queryMatchScore": 95 - i, "score": 95 - i})
        selected = _apply_region_quota(rows, 8, "domestic-first")
        self.assertGreaterEqual(sum(1 for row in selected if row["originRegion"] == "domestic"), 6)

    def test_research_contract_returns_domestic_first_indexed_results(self):
        from app import pipeline, tavily

        def row(idx, kind, region):
            subjects = ["数据产权登记", "公共数据授权运营", "可信数据空间建设", "数据资产入表", "企业数据治理转型", "个人信息权益保护"]
            return {
                "id": f"{region}-{kind}-{idx}", "type": kind,
                "title": f"{subjects[idx % len(subjects)]}：数据要素治理{kind}观察",
                "url": f"https://{'gov.cn' if region == 'domestic' else 'oecd.org'}/{kind}/{idx}",
                "source": "国内权威来源" if region == "domestic" else "国际机构",
                "snippet": "数据要素治理、数据流通和企业价值的最新进展与实施案例。",
                "publishedAt": "2026-08-12", "provider": "tavily", "relevance": 0.88,
                "authorityScore": 90, "freshnessScore": 98, "originRegion": region,
            }

        domestic_news = {"results": [row(i, "news", "domestic") for i in range(5)], "images": []}
        global_news = {"results": [row(1, "news", "global")], "images": []}
        domestic_policy = {"results": [row(i, "policy", "domestic") for i in range(2)], "images": []}
        global_policy = {"results": [row(2, "policy", "global")], "images": []}
        papers = [row(i, "paper", "domestic") for i in range(2)] + [row(3, "paper", "global")]

        def accept(rows, limit=14):
            return ([{**item, "sourceUsable": True, "sourceVerified": False, "sourceStatus": "indexed"} for item in rows], [])

        with patch.object(tavily, "available", return_value=True), \
             patch.object(tavily, "search_domestic_news", return_value=domestic_news), \
             patch.object(tavily, "search", return_value=global_news), \
             patch.object(tavily, "search_policy", return_value=domestic_policy), \
             patch.object(tavily, "search_global_policy", return_value=global_policy), \
             patch.object(tavily, "search_papers", return_value=papers), \
             patch.object(pipeline, "verify_results", side_effect=accept):
            result = pipeline.research({"query": "数据要素治理", "types": ["news", "policy", "paper"], "maxResults": 8, "regionPreference": "domestic-first"})
        self.assertEqual(len(result["results"]), 8)
        self.assertGreaterEqual(result["meta"]["domesticCount"], 6)
        self.assertEqual(result["meta"]["indexedCount"], 8)

    def test_user_facing_empty_states_are_professional(self):
        copy = "\n".join((ROOT / "web" / name).read_text() for name in ("search.js", "home.js", "feed.js", "search.html", "feed.html"))
        for phrase in ("宁可不瞎编", "能力不足", "无辜打扰", "小船已经出发", "逆风", "临时堵车"):
            self.assertNotIn(phrase, copy)

class V22EditorialAndVisualTests(unittest.TestCase):
    def test_embedded_json_transport_is_recovered_not_rendered(self):
        from app.pipeline import _sanitize_article
        raw = 'json {"titleCandidates":["数据产权登记为什么值得关注？"],"deck":"导语","markdown":"开头正文\\n\\n## 规则正在变化\\n\\n这里是正常正文。","imageQueries":[]}'
        article = _sanitize_article({"markdown": raw}, "数据要素治理")
        self.assertTrue(article["markdown"].startswith("开头正文"))
        self.assertIn("## 规则正在变化", article["markdown"])
        self.assertNotIn("titleCandidates", article["markdown"])
        self.assertNotIn('\\n\\n', article["markdown"])

    def test_generic_site_topic_is_not_forced_into_title_prefix(self):
        from app.pipeline import _rank_wechat_titles
        titles = _rank_wechat_titles([
            "数据要素治理：——数据产权登记为什么突然重要了？",
            "一张数据‘身份证’，到底改变了什么？",
        ], "数据要素治理")
        self.assertTrue(titles)
        self.assertFalse(any(t.startswith("数据要素治理：") for t in titles))
        self.assertFalse(any(t.startswith("数据要素治理——") for t in titles))

    def test_compose_defaults_are_true_defaults_and_references_are_off(self):
        html = (ROOT / "web" / "compose.html").read_text()
        for option in (
            "默认 · 自然起题", "默认 · 按内容自然组织", "默认 · 选择最自然切口",
            "默认 · 随内容调整", "默认 · 自然融入证据", "默认 · 自然收束", "默认 · 自动平衡",
        ):
            self.assertIn(option, html)
        self.assertIn('id="citationToggle" type="checkbox"', html)
        self.assertNotIn('id="citationToggle" type="checkbox" checked', html)
        self.assertIn('id="htmlButton"', html)

    def test_preview_is_not_weekly_template_and_search_has_emoticons(self):
        compose = (ROOT / "web" / "compose.js").read_text()
        search = (ROOT / "web" / "search.js").read_text()
        self.assertNotIn("数据要素治理周报", compose)
        self.assertNotIn("newsletter-section-number", compose)
        self.assertIn("( •̀ ω •́ )✧", search)
        self.assertIn("(｡•́︿•̀｡)", search)

    def test_visual_queries_broaden_instead_of_repeating_same_serper_call(self):
        from app.visuals import _visual_query_variants
        slot = {
            "kind": "body", "query": "数据产权登记工作指引", "afterHeading": "产权登记开始落地",
            "purpose": "解释登记机制", "sourceHint": "央视新闻 数据产权登记工作指引",
        }
        variants = _visual_query_variants(slot, "数据要素治理", match_mode="precise")
        self.assertGreaterEqual(len(variants), 3)
        self.assertEqual(len(variants), len(set(variants)))
        self.assertIn("央视新闻 数据产权登记工作指引", variants[0])
        self.assertNotIn("原文 配图", variants[0])
        self.assertTrue(any("数据驱动" in q or "数据平台" in q for q in variants[1:]))

    def test_visual_resolver_can_use_third_candidate_if_first_two_fail_hotlink(self):
        from app import visuals, serper_images
        slot = {"slotId": "body-1", "kind": "body", "query": "可信数据空间建设", "afterHeading": "项目落地", "purpose": "展示真实项目现场"}
        rows = []
        for i in range(3):
            rows.append({
                "url": f"https://img.example.org/{i}.jpg", "fallbackUrl": "",
                "description": "可信数据空间建设项目现场", "sourceTitle": "可信数据空间建设项目启动",
                "sourceSnippet": "可信数据空间项目落地", "source": "机构官网",
                "sourceUrl": f"https://gov.example.cn/news/{i}", "width": 1200, "height": 800, "provider": "serper",
            })
        def profile(url):
            idx = int(url.rsplit("/", 1)[-1].split(".")[0])
            return visuals.ImageProfile(1200, 800, 1.5, f"fp{idx}", idx == 2)
        with patch.object(serper_images, "available", return_value=True), \
             patch.object(serper_images, "search_images", return_value=rows), \
             patch.object(visuals, "image_profile", side_effect=profile):
            out, _ = visuals.resolve_visuals([slot], "数据要素治理")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["image"]["url"], "https://img.example.org/2.jpg")

class V24SearchAndOriginImageTests(unittest.TestCase):
    def test_long_natural_language_query_is_decomposed_like_browser_search(self):
        from app.query_intent import local_plan
        from app.pipeline import _query_match_score
        query = "数据在技术突破上的作用"
        plan = local_plan(query)
        self.assertTrue(plan["isConceptualQuery"])
        self.assertIn("技术突破", plan["conceptTerms"])
        self.assertIn("数据", plan["conceptTerms"])
        self.assertNotEqual(plan["domesticNewsQuery"], query)
        self.assertIn("数据要素 技术突破", plan["webQueryVariants"])
        row = {
            "title": "数据要素赋能企业关键核心技术突破研究",
            "snippet": "实证考察数据要素对企业关键核心技术突破的赋能效应，并分析其作用机制。",
            "source": "科研管理", "type": "paper",
        }
        self.assertGreaterEqual(_query_match_score(query, row, intent=plan), 40)

    def test_conceptual_query_uses_general_web_lane_and_keeps_result(self):
        from app import pipeline, tavily, serper_search
        row = {
            "id": "web-1", "type": "web", "title": "数据要素赋能企业关键核心技术突破研究",
            "url": "https://kygl.example.cn/article/1", "source": "科研管理",
            "snippet": "实证考察数据要素对企业关键核心技术突破的赋能效应。",
            "relevance": .86, "authorityScore": 82, "freshnessScore": 70, "score": 88,
            "provider": "tavily", "originRegion": "domestic",
        }
        empty = {"results": [], "images": []}
        def verified(rows, limit=14):
            out=[]
            for r in rows:
                c=dict(r); c.update({"sourceVerified": True, "sourceUsable": True, "sourceStatus": "verified"}); out.append(c)
            return out, []
        with patch.object(tavily, "available", return_value=True), \
             patch.object(serper_search, "available", return_value=False), \
             patch.object(tavily, "search_domestic_news", return_value=empty), \
             patch.object(tavily, "search_domestic_web", return_value={"results":[row], "images":[]}) as web_lane, \
             patch.object(pipeline, "verify_results", side_effect=verified):
            result = pipeline.research({"query":"数据在技术突破上的作用", "types":["news"], "timeRange":"all", "maxResults":10})
        self.assertTrue(web_lane.called)
        self.assertGreaterEqual(len(result["results"]), 1)
        self.assertIn("技术突破", result["results"][0]["title"])

    def test_origin_page_images_work_even_without_serper(self):
        from app import visuals, serper_images
        slot = {
            "slotId": "body-1", "kind": "body", "query": "数据要素 技术突破",
            "afterHeading": "数据如何改变研发试错", "anchorText": "数据要素正在帮助企业缩小研发试错范围",
            "purpose": "展示案例来源中的真实图片",
            "sourceHint": "数据要素赋能企业关键核心技术突破研究",
            "sourceName": "科研管理", "sourceHintUrl": "https://kygl.example.cn/article/1",
            "sourceSnippet": "数据要素显著促进企业关键核心技术突破",
            "sourceImages": ["https://img.example.cn/article-hero.jpg"],
        }
        with patch.object(serper_images, "available", return_value=False), \
             patch.object(visuals, "image_profile", return_value=visuals.ImageProfile(1200, 800, 1.5, "originfp", True)):
            out, warnings = visuals.resolve_visuals([slot], "数据在技术突破上的作用")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["image"]["provider"], "source-origin")
        self.assertEqual(out[0]["image"]["url"], "https://img.example.cn/article-hero.jpg")
        self.assertFalse(any("未配置 SERPER" in w and "未执行" in w for w in warnings))

    def test_extract_details_requests_origin_page_images(self):
        from app import tavily
        payloads=[]
        def fake_request(url, **kwargs):
            payloads.append(kwargs.get("payload") or {})
            return {"results":[{"url":"https://news.example.cn/a", "raw_content":"正文", "images":["https://img.example.cn/a.jpg"]}]}
        with patch.object(tavily, "available", return_value=True), patch.object(tavily, "request_json", side_effect=fake_request):
            details = tavily.extract_url_details(["https://news.example.cn/a"], query="数据技术突破", include_images=True)
        self.assertTrue(payloads[-1]["include_images"])
        self.assertEqual(details["https://news.example.cn/a"]["images"], ["https://img.example.cn/a.jpg"])


class V23ComposeQualityTests(unittest.TestCase):
    def test_auto_evidence_uses_writing_brief_and_is_attached(self):
        from app import pipeline, deepseek, tavily
        source = {
            "id": "auto-1", "type": "paper", "title": "数据驱动材料研发：机器学习辅助实验筛选",
            "url": "https://example.org/paper", "source": "研究机构", "snippet": "利用历史实验数据训练模型，缩小候选配方范围。",
            "publishedAt": "2025-06-01", "sourceUsable": True, "sourceVerified": False,
            "sourceStatus": "indexed", "score": 88, "queryMatchScore": 92, "freshnessScore": 70,
        }
        fake = {
            "titleCandidates": ["研发为什么开始换一种试错方式？", "实验数据开始进入研发决策"],
            "recommendedTitle": "研发为什么开始换一种试错方式？", "deck": "导语",
            "markdown": "这是一段自然开头。\n\n## 试错的成本正在变化\n\n" + ("历史实验数据进入模型后，研发团队可以先缩小候选范围，再回到实验室验证。" * 55),
            "understoodBrief": "解释数据如何改变研发试错", "coverBrief": "研发数据分析与实验验证",
            "imageQueries": [], "imageSlots": [], "socialSummary": "简介", "keyClaims": [], "riskNotes": [], "sourceNotes": [],
        }
        captured = {}
        def evidence(query, description):
            captured["query"] = query; captured["description"] = description
            return {"results": [source], "warnings": [], "meta": {}}
        with patch.object(deepseek, "available", return_value=True), \
             patch.object(deepseek, "generate_json", return_value=fake), \
             patch.object(tavily, "available", return_value=False), \
             patch.object(pipeline, "_research_for_article_evidence", side_effect=evidence), \
             patch.object(pipeline, "_start_visual_job"):
            article = pipeline.generate_article({
                "query": "数据驱动研发", "description": "重点解释实验数据如何降低研发试错成本",
                "sources": [], "options": {"autoEvidence": True, "bodyImageCount": 0, "length": "短篇 · 1000—1400字"},
            })
        self.assertIn("实验数据", captured["description"])
        self.assertEqual(article["sourceCount"], 1)
        self.assertEqual(article["generationMeta"]["autoEvidenceAdded"], 1)
        self.assertEqual(article["sourceList"][0]["origin"], "auto")

    def test_default_quality_gate_rejects_six_part_ai_outline(self):
        from app.pipeline import _needs_editorial_repair
        body = "数据进入研发流程后，工程师先用历史实验缩小范围，再做物理验证，同时保留失败数据用于下一轮学习。" * 28
        headings = ["问题：经验主义的天花板", "做法：让数据先跑起来", "机制：数据为什么能提速", "影响：从研发到产业", "条件：哪些地方能复制", "判断：数据不是魔法"]
        markdown = "\n\n".join(f"## {h}\n\n{body}" for h in headings)
        article = {"markdown": markdown, "understoodBrief": "解释研发变化", "recommendedTitle": "研发试错为什么开始被重新组织？", "titleCandidates": ["研发试错为什么开始被重新组织？", "实验数据正在改变研发节奏"]}
        self.assertTrue(_needs_editorial_repair(article, length_spec={"min": 1000, "max": 10000, "target": 2000}, smart_sections=True, ai_cliche_guard=True))

    def test_data_article_rejects_laptop_and_chemistry_only_images(self):
        from app.visuals import _has_semantic_anchor
        slot = {
            "query": "数据驱动研发 实验数据 机器学习模型",
            "afterHeading": "研发试错的方式正在变化",
            "anchorText": "企业把历史实验数据清洗后训练模型，用模型缩小候选配方范围，再回到实验室验证。",
            "purpose": "解释数据模型如何辅助研发",
        }
        laptop = {"description": "Intel VS AMD 实际场景对比", "sourceTitle": "笔记本电脑性能实测", "sourceSnippet": "CPU 跑分与游戏本评测", "source": "太平洋科技"}
        chemistry = {"description": "高比能锂离子电池正极材料结构设计", "sourceTitle": "锂电池正极材料微观结构", "sourceSnippet": "CEI 与钴迁移机理", "source": "高校实验室"}
        good = {"description": "数据驱动材料研发平台", "sourceTitle": "机器学习模型辅助电池材料研发", "sourceSnippet": "历史实验数据训练模型并预测候选配方", "source": "研究机构"}
        self.assertFalse(_has_semantic_anchor(laptop, slot, "数据驱动研发"))
        self.assertFalse(_has_semantic_anchor(chemistry, slot, "数据驱动研发"))
        self.assertTrue(_has_semantic_anchor(good, slot, "数据驱动研发"))

    def test_cover_is_generated_even_when_body_images_are_zero(self):
        from app import visuals, serper_images
        from app.content_blocks import plan_visual_slots
        article = {"recommendedTitle": "实验数据开始进入研发决策", "coverBrief": "数据分析与研发实验", "markdown": "这是一段正文。" * 40, "imageQueries": []}
        slots = plan_visual_slots(article, "数据驱动研发", max_body=0)
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0]["kind"], "cover")
        with patch.object(serper_images, "available", return_value=False):
            out, _ = visuals.resolve_visuals(slots, "数据驱动研发")
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["image"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(out[0]["image"]["provider"], "generated-cover")

    def test_generated_cover_does_not_spend_serper_image_search(self):
        from app import visuals, serper_images
        slots = [{
            "slotId": "cover", "kind": "cover", "query": "数据驱动研发",
            "coverTitle": "实验数据开始进入研发决策", "coverBrief": "历史实验数据、模型筛选与物理验证",
            "afterHeading": "", "purpose": "文章封面视觉",
        }]
        with patch.object(serper_images, "available", return_value=True), patch.object(serper_images, "search_images") as search:
            out, _ = visuals.resolve_visuals(slots, "数据驱动研发")
        search.assert_not_called()
        self.assertEqual(out[0]["image"]["provider"], "generated-cover")

    def test_default_four_section_outline_is_sent_for_editorial_repair(self):
        from app.pipeline import _needs_editorial_repair
        body = "历史实验数据经过整理后可以帮助团队缩小下一轮候选范围，但最终仍要回到真实实验完成验证。" * 25
        markdown = "\n\n".join(f"## {h}\n\n{body}" for h in ["经验开始失去覆盖力", "失败实验被重新利用", "试错轮次真正被压缩", "数据基础仍是前提"])
        article = {"markdown": markdown, "understoodBrief": "解释数据驱动研发", "recommendedTitle": "研发试错正在换一种组织方式", "titleCandidates": ["研发试错正在换一种组织方式", "失败实验为什么开始有了第二次价值"]}
        self.assertTrue(_needs_editorial_repair(article, length_spec={"min": 1000, "max": 10000, "target": 2000}, smart_sections=True, ai_cliche_guard=True, structure="默认 · 按内容自然组织"))

    def test_zero_body_image_count_still_starts_cover_job(self):
        from app import pipeline, deepseek, tavily
        fake = {
            "titleCandidates": ["数据产权登记开始影响企业用数", "一张登记凭证改变了什么"],
            "deck": "导语", "markdown": "自然开头。\n\n" + ("企业需要把数据目录、授权链条和使用留痕沉淀到日常治理中。" * 65),
            "understoodBrief": "解释制度影响", "coverBrief": "数据产权登记与企业治理", "imageQueries": [], "imageSlots": [],
            "socialSummary": "简介", "keyClaims": [], "riskNotes": [], "sourceNotes": [],
        }
        with patch.object(deepseek, "available", return_value=True), patch.object(deepseek, "generate_json", return_value=fake), patch.object(tavily, "available", return_value=False), patch.object(pipeline, "_start_visual_job") as starter:
            article = pipeline.generate_article({"query": "数据产权登记", "sources": [], "options": {"autoEvidence": False, "bodyImageCount": 0, "length": "短篇 · 1000—1400字"}})
        starter.assert_called_once()
        self.assertEqual(article["visualReport"]["coverPlanned"], 1)
        self.assertEqual(article["visualReport"]["bodyPlanned"], 0)

    def test_frontend_syncs_server_auto_evidence_and_passes_brief_to_search(self):
        js = (ROOT / "web" / "compose.js").read_text()
        self.assertIn("syncAutoEvidenceFromArticle(article)", js)
        self.assertIn("description, types", js)
        self.assertIn("evidenceTimeRange", js)


class V25ComposeLayoutAndImageTests(unittest.TestCase):
    def test_compose_preview_height_tracks_left_settings_column(self):
        css = (ROOT / "web" / "styles.css").read_text()
        js = (ROOT / "web" / "compose.js").read_text()
        self.assertIn("align-items:start", css)
        self.assertIn("height:var(--compose-column-height,auto)", css)
        self.assertIn("syncComposeColumnHeight", js)
        self.assertIn("ResizeObserver", js)
        self.assertIn("--compose-column-height", js)

    def test_llm_image_slot_source_id_is_preserved_and_binds_exact_source(self):
        from app import pipeline
        captured = {}
        article = {
            "recommendedTitle": "数据如何改变关键技术攻关",
            "markdown": "开头。\n\n## 一个真实项目开始改变试错方式\n\n这里讲某企业的真实案例以及数据如何进入研发。",
            "imageQueries": [],
            "imageSlots": [{
                "afterHeading": "一个真实项目开始改变试错方式",
                "purpose": "展示该案例的真实现场",
                "query": "企业 数据驱动 研发案例",
                "sourceId": 2,
            }],
            "sourceNotes": [{"sourceId": 2, "whyUsed": "支撑企业关键技术攻关案例"}],
            "sourceList": [
                {"n": 1, "title": "无关政策", "source": "机构甲", "url": "https://a.example.cn/1", "snippet": "其他内容", "sourceImages": []},
                {"n": 2, "title": "数据要素赋能企业关键核心技术突破研究", "source": "科研管理", "url": "https://kygl.example.cn/case", "snippet": "研究数据如何促进企业关键核心技术突破", "sourceImages": ["https://img.example.cn/case.jpg"]},
            ],
        }
        def fake_resolve(slots, query, **kwargs):
            captured["slots"] = slots
            return ([{**slot, "image": {"url": "https://img.example.cn/x.jpg", "provider": "serper"}} for slot in slots], [])
        with patch.object(pipeline, "resolve_visuals", side_effect=fake_resolve):
            pipeline._apply_visual_layout(article, "数据在技术突破上的作用", image_count=1, image_preference="混合")
        body = next(x for x in captured["slots"] if x.get("kind") == "body")
        self.assertEqual(body["sourceId"], "2")
        self.assertTrue(body["sourceExplicit"])
        self.assertEqual(body["sourceHint"], "数据要素赋能企业关键核心技术突破研究")
        self.assertEqual(body["sourceHintUrl"], "https://kygl.example.cn/case")
        self.assertEqual(body["sourceImages"], ["https://img.example.cn/case.jpg"])

    def test_explicit_case_source_can_anchor_real_source_photo_without_literal_data_word(self):
        from app.visuals import _has_semantic_anchor
        slot = {
            "query": "数据驱动研发 企业关键技术突破",
            "afterHeading": "一个真实项目开始改变试错方式",
            "anchorText": "文章在这里解释数据如何进入研发决策。",
            "sourceHint": "某企业关键核心技术攻关项目取得阶段进展",
            "sourceHintUrl": "https://news.example.cn/case/123",
            "sourceExplicit": True,
        }
        photo = {
            "description": "项目团队在实验室开展测试",
            "sourceTitle": "某企业关键核心技术攻关项目取得阶段进展",
            "sourceSnippet": "研发团队现场",
            "source": "权威媒体",
            "sourceUrl": "https://news.example.cn/case/123",
            "provider": "source-origin",
        }
        self.assertTrue(_has_semantic_anchor(photo, slot, "数据在技术突破上的作用"))
        generic = dict(photo, sourceUrl="https://other.example.cn/random", provider="serper", sourceTitle="普通实验室照片")
        self.assertFalse(_has_semantic_anchor(generic, {k:v for k,v in slot.items() if k != "sourceExplicit"}, "数据在技术突破上的作用"))

    def test_image_queries_are_short_and_source_first(self):
        from app.visuals import _visual_query_variants
        slot = {
            "kind": "body",
            "query": "数据在技术突破上的作用 企业利用历史实验数据构建模型以缩小候选方案并提高研发效率",
            "afterHeading": "过去靠经验反复试验，现在先让数据缩小范围",
            "anchorText": "这是一个非常长的自然语言段落，用来说明为什么不能把整句话全部塞给图片搜索引擎，否则搜索会越来越窄并且很慢。" * 3,
            "sourceHint": "数据要素赋能企业关键核心技术突破研究",
            "sourceName": "科研管理",
            "sourceHintUrl": "https://kygl.example.cn/article/1",
        }
        variants = _visual_query_variants(slot, "数据在技术突破上的作用", match_mode="precise")
        self.assertTrue(variants[0].startswith('"数据要素赋能企业关键核心技术突破研究"'))
        self.assertTrue(any("site:kygl.example.cn" in x for x in variants[:2]))
        self.assertTrue(all(len(x) <= 180 for x in variants))
        self.assertTrue(any("数据分析" in x or "数据平台" in x for x in variants))

    def test_image_fallback_does_not_repeat_source_query(self):
        from app import visuals, serper_images
        calls = []
        def fake_search(q, count=14):
            calls.append(q)
            return []
        slot = {
            "slotId": "body-1", "kind": "body", "query": "数据驱动研发 技术突破",
            "afterHeading": "真实案例开始改变试错方式", "anchorText": "历史实验数据进入模型后缩小候选范围。",
            "purpose": "展示案例现场", "sourceHint": "数据要素赋能企业关键核心技术突破研究",
            "sourceName": "科研管理", "sourceHintUrl": "https://kygl.example.cn/a",
        }
        with patch.object(serper_images, "available", return_value=True), patch.object(serper_images, "search_images", side_effect=fake_search):
            visuals.resolve_visuals([slot], "数据在技术突破上的作用")
        self.assertGreaterEqual(len(calls), 2)
        self.assertLessEqual(len(calls), 2)
        self.assertEqual(len(calls), len(set(calls)))
        self.assertTrue(any("数据 科技" in q or "数据平台" in q for q in calls[1:]))

    def test_unresolved_real_search_now_falls_back_to_code_visual(self):
        from app import visuals, serper_images
        slot = {
            "slotId": "body-1", "kind": "body", "query": "数据技术突破",
            "afterHeading": "数据开始进入研发决策", "anchorText": "历史实验数据进入模型后缩小候选范围。",
            "purpose": "展示数据驱动研发场景",
        }
        with patch.object(serper_images, "available", return_value=True), patch.object(serper_images, "search_images", return_value=[]):
            out, warnings = visuals.resolve_visuals([slot], "数据技术突破")
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["matchedBy"].startswith("generated-diagram"))
        self.assertEqual(out[0]["image"]["provider"], "generated-diagram")
        self.assertTrue(warnings)


class V26QualityEfficiencyTests(unittest.TestCase):
    def test_conceptual_query_matches_semantic_target_but_not_data_center_false_positive(self):
        from app.pipeline import _query_match_score
        from app.query_intent import local_plan
        intent = local_plan("数据在技术突破上的作用")
        good = {
            "title": "数据要素赋能企业关键核心技术突破研究",
            "snippet": "研究数据要素如何降低信息搜集成本、加速共享并优化企业研发决策。",
            "source": "科研管理",
        }
        bad = {
            "title": "数据中心液冷技术取得新突破",
            "snippet": "服务器散热材料和制冷设备性能提升。",
            "source": "硬件产业网",
        }
        self.assertTrue(intent["isConceptualQuery"])
        self.assertGreaterEqual(_query_match_score("数据在技术突破上的作用", good, intent=intent), 22)
        self.assertLess(_query_match_score("数据在技术突破上的作用", bad, intent=intent), 22)

    def test_short_conceptual_query_gets_two_semantic_sides(self):
        from app.query_intent import local_plan
        plan = local_plan("数据对技术突破")
        groups = plan.get("conceptGroups") or []
        self.assertTrue(plan["isConceptualQuery"])
        self.assertTrue(any("数据" in g and "数据要素" in g for g in groups))
        self.assertTrue(any("技术突破" in g and "技术创新" in g for g in groups))

    def test_simple_description_skips_intent_llm(self):
        from app import query_intent, deepseek
        query_intent._PLAN_CACHE.clear()
        with patch.object(deepseek, "available", return_value=True), patch.object(deepseek, "plan_search_intent") as planner:
            plan = query_intent.understand("数据驱动研发", "优先国内政策和权威媒体")
        planner.assert_not_called()
        self.assertFalse(plan.get("usedModel"))

    def test_conceptual_primary_tavily_budget_does_not_exceed_v25(self):
        from app import pipeline, tavily, serper_search
        calls = []
        def fake(name, value):
            def run(*args, **kwargs):
                calls.append(name)
                return value
            return run
        with patch.object(tavily, "available", return_value=True), \
             patch.object(serper_search, "available", return_value=False), \
             patch.object(tavily, "search_domestic_news", side_effect=fake("domestic_news", {"results": [], "images": []})), \
             patch.object(tavily, "search_domestic_web", side_effect=fake("domestic_web", {"results": [], "images": []})), \
             patch.object(tavily, "search", side_effect=fake("global_news", {"results": [], "images": []})), \
             patch.object(tavily, "search_policy", side_effect=fake("policy", {"results": [], "images": []})), \
             patch.object(tavily, "search_papers", side_effect=fake("papers", [])):
            pipeline._RESEARCH_CACHE.clear()
            pipeline.research({
                "query": "数据在技术突破上的作用", "types": ["news", "policy", "paper"],
                "timeRange": "all", "maxResults": 20, "searchMode": "fast",
                "regionPreference": "domestic-first", "surface": "interactive",
            })
        self.assertLessEqual(len(calls), 5)

    def test_serper_fallback_call_budget_is_at_most_three(self):
        from app import pipeline, serper_search
        calls = []
        def fake_search(q, **kwargs):
            calls.append((q, kwargs.get("kind")))
            return [{"title": q, "url": f"https://example.cn/{len(calls)}", "snippet": "数据技术创新", "source": "示例"}]
        with patch.object(serper_search, "available", return_value=True), patch.object(serper_search, "search", side_effect=fake_search):
            pipeline._serper_recall(
                ["技术突破 数据", "数据要素 关键核心技术突破", "数据驱动 技术创新 研发", "数据在技术突破上的作用"],
                requested_types={"news", "paper", "policy"}, count=8,
            )
        self.assertLessEqual(len(calls), 3)
        self.assertEqual(len({q for q, _ in calls if q}), 2)
        self.assertEqual(sum(1 for _, kind in calls if kind == "news"), 1)

    def test_default_heading_naturalizer_removes_template_labels_only_in_default_mode(self):
        from app.pipeline import _naturalize_default_structure
        md = "## 问题：经验为什么开始失效\n\n正文。\n\n## 机制：数据怎样缩小试错范围\n\n正文。\n\n## 判断\n\n正文。"
        natural = _naturalize_default_structure(md, "默认 · 按内容自然组织")
        self.assertIn("## 经验为什么开始失效", natural)
        self.assertIn("## 数据怎样缩小试错范围", natural)
        self.assertNotIn("## 判断", natural)
        self.assertEqual(_naturalize_default_structure(md, "问题—机制—案例—趋势"), md)

    def test_evidence_excerpt_is_shorter_and_keeps_relevant_facts(self):
        from app.pipeline import _evidence_excerpt
        raw = (
            "首页 导航 广告 责任编辑。" * 30
            + "国家数据局提出加强关键数据技术攻关突破，并将数据科技研发纳入科技计划体系。"
            + "研究发现，企业利用历史实验数据训练模型，可以缩小候选方案范围并降低研发试错成本。"
            + "上一篇 下一篇 扫码登录。" * 30
        )
        excerpt = _evidence_excerpt({"title": "数据如何推动技术突破", "rawContent": raw}, "数据在技术突破上的作用", limit=260)
        self.assertLessEqual(len(excerpt), 260)
        self.assertTrue("关键数据技术" in excerpt or "历史实验数据" in excerpt)
        self.assertLess(excerpt.count("上一篇"), 3)

    def test_source_page_image_gets_first_refusal_without_serper_search(self):
        from app import visuals, serper_images
        from app.image_fetch import ImageProfile
        slot = {
            "slotId": "body-1", "kind": "body", "query": "数据驱动研发 技术突破",
            "afterHeading": "企业开始用历史实验数据缩小候选范围",
            "anchorText": "企业利用历史实验数据训练模型，再回到实验室验证。",
            "purpose": "展示真实研发项目现场", "sourceExplicit": True,
            "sourceHint": "数据要素赋能企业关键核心技术突破研究", "sourceName": "科研管理",
            "sourceHintUrl": "https://kygl.example.cn/article/1",
            "sourceSnippet": "企业利用数据优化研发决策并推进关键技术攻关。",
            "sourceImages": ["https://kygl.example.cn/images/lab.jpg"],
        }
        profile = ImageProfile(1280, 720, 16/9, "abc", True)
        with patch.object(serper_images, "available", return_value=True), \
             patch.object(serper_images, "search_images") as search, \
             patch.object(visuals, "image_profile", return_value=profile):
            out, _ = visuals.resolve_visuals([slot], "数据在技术突破上的作用")
        search.assert_not_called()
        self.assertEqual(out[0]["image"]["provider"], "source-origin")

    def test_no_source_image_starts_with_one_serper_query_when_first_hit_is_usable(self):
        from app import visuals, serper_images
        from app.image_fetch import ImageProfile
        slot = {
            "slotId": "body-1", "kind": "body", "query": "数据驱动研发 模型筛选",
            "afterHeading": "历史实验数据开始进入研发决策",
            "anchorText": "历史实验数据训练机器学习模型，帮助研发团队缩小候选范围。",
            "purpose": "展示数据分析辅助研发的真实场景", "sourceImages": [],
        }
        candidate = {
            "url": "https://img.example.cn/data-lab.jpg", "fallbackUrl": "", "description": "研发团队查看数据分析模型",
            "source": "研究机构", "sourceUrl": "https://research.example.cn/case", "sourceTitle": "数据驱动研发平台",
            "sourceSnippet": "历史实验数据和机器学习模型辅助研发决策", "resultScore": 0.9, "provider": "serper",
        }
        profile = ImageProfile(1280, 720, 16/9, "xyz", True)
        with patch.object(serper_images, "available", return_value=True), \
             patch.object(serper_images, "search_images", return_value=[candidate]) as search, \
             patch.object(visuals, "image_profile", return_value=profile):
            out, _ = visuals.resolve_visuals([slot], "数据驱动研发")
        self.assertEqual(search.call_count, 1)
        self.assertIsNotNone(out[0]["image"])

    def test_pdf_prefers_canonical_docx_conversion_when_available(self):
        from app import exporter
        with patch.object(exporter, "_pdf_from_docx_if_available", return_value=b"%PDF-1.4\n%%EOF") as canonical, \
             patch.object(exporter, "_build_pdf_reportlab", return_value=b"fallback") as fallback:
            data = exporter.build_pdf({"recommendedTitle": "测试", "markdown": "正文"}, [])
        canonical.assert_called_once()
        fallback.assert_not_called()
        self.assertTrue(data.startswith(b"%PDF"))

    def test_theme_ontology_understands_quality_and_cross_border_relations_without_llm(self):
        from app.query_intent import local_plan
        quality = local_plan("训练数据质量为什么影响大模型效果")
        qgroups = quality.get("conceptGroups") or []
        self.assertTrue(quality["isConceptualQuery"])
        self.assertTrue(any("数据质量" in group and "数据清洗" in group for group in qgroups))
        self.assertTrue(any("大模型" in group and "人工智能" in group for group in qgroups))
        cross = local_plan("数据跨境怎样影响企业出海")
        cgroups = cross.get("conceptGroups") or []
        self.assertTrue(any("数据跨境" in group and "数据出境" in group for group in cgroups))

    def test_source_origin_position_survives_dedupe_and_late_assets_are_rejected(self):
        from app.visuals import _dedupe_images, _has_semantic_anchor
        raw = [{
            "url": "https://news.example.cn/images/content-7.jpg", "provider": "source-origin",
            "sourceUrl": "https://news.example.cn/article/1", "sourceTitle": "数据驱动研发案例",
            "description": "数据驱动研发案例", "position": 7,
        }]
        image = _dedupe_images(raw)[0]
        self.assertEqual(image["position"], 7)
        slot = {
            "query": "数据驱动研发", "afterHeading": "企业开始使用实验数据",
            "anchorText": "历史实验数据进入模型后缩小候选范围", "sourceExplicit": True,
            "sourceHint": "数据驱动研发案例", "sourceHintUrl": "https://news.example.cn/article/1",
        }
        self.assertFalse(_has_semantic_anchor(image, slot, "数据驱动研发"))

    def test_quality_gate_repairs_generic_ai_title_and_repetitive_connectors(self):
        from app.pipeline import _needs_editorial_repair
        base = {
            "titleCandidates": ["一文读懂数据如何改变企业研发", "数据进入研发决策后发生了什么"],
            "recommendedTitle": "一文读懂数据如何改变企业研发",
            "understoodBrief": "解释数据如何改变研发",
            "markdown": ("这意味着企业需要重新组织研发数据。" * 45) + ("更重要的是，数据开始进入下一轮决策。" * 15),
        }
        self.assertTrue(_needs_editorial_repair(base, length_spec={"min": 900, "max": 2600, "target": 1800}, smart_sections=True, ai_cliche_guard=True, structure="默认 · 按内容自然组织"))

    def test_detailed_writing_brief_uses_planner_but_simple_long_sentence_does_not(self):
        from app.brief import _needs_model_brief
        simple = "希望重点解释数据如何帮助企业把过去积累的研发经验变成下一轮实验可以继续使用的决策依据，并说明其中最关键的变化。"
        detailed = "重点写企业研发场景；不要套固定六段式。开头从真实案例切入，同时解释数据质量的边界，结尾不要列建议清单。"
        self.assertFalse(_needs_model_brief(simple))
        self.assertTrue(_needs_model_brief(detailed))


class V27ImageRecallQualityTests(unittest.TestCase):
    def test_visual_queries_follow_section_meaning_not_generic_rnd_template(self):
        from app.visuals import _visual_query_variants
        failure = {
            "kind": "body", "query": "数据治理 为什么数据治理总是失败",
            "afterHeading": "为什么数据治理总是失败？", "anchorText": "很多企业的数据治理项目会失败。",
            "purpose": "解释失败原因",
        }
        people = {
            "kind": "body", "query": "数据治理如何影响普通人",
            "afterHeading": "数据治理如何影响普通人？", "anchorText": "个人在数字服务中留下大量信息。",
            "purpose": "解释普通人的实际感受",
        }
        f = _visual_query_variants(failure, "数据治理", match_mode="precise")
        p = _visual_query_variants(people, "数据治理", match_mode="precise")
        self.assertTrue(any("数据孤岛" in q or "数据质量" in q for q in f))
        self.assertTrue(any("个人信息保护" in q or "个人信息权益" in q for q in p))
        self.assertFalse(any("科技研发" in q for q in f + p))
        self.assertTrue(all(len(q) <= 132 for q in f + p))

    def test_personal_information_image_is_valid_for_ordinary_people_section(self):
        from app.visuals import _has_semantic_anchor
        slot = {
            "kind": "body", "query": "数据治理如何影响普通人",
            "afterHeading": "数据治理如何影响普通人？", "anchorText": "用户的个人信息和隐私权益会受到影响。",
        }
        good = {
            "description": "个人信息保护与隐私权益", "sourceTitle": "数字生活中的个人信息保护",
            "sourceSnippet": "个人信息权益与隐私保护", "source": "权威媒体",
        }
        bad = {
            "description": "年轻人逛商场消费", "sourceTitle": "城市生活方式摄影",
            "sourceSnippet": "时尚消费与街拍", "source": "图片站",
        }
        self.assertTrue(_has_semantic_anchor(good, slot, "数据治理"))
        self.assertFalse(_has_semantic_anchor(bad, slot, "数据治理"))

    def test_sixth_source_page_image_can_be_selected_before_spending_serper(self):
        from app import visuals, serper_images
        from app.image_fetch import ImageProfile
        urls = [f"https://news.example.cn/img/{i}.jpg" for i in range(1, 7)]
        slot = {
            "slotId": "body-1", "kind": "body", "query": "数据治理 企业业务",
            "afterHeading": "数据是业务的事", "anchorText": "企业把数据治理嵌入业务流程。",
            "purpose": "展示真实业务场景", "sourceExplicit": True,
            "sourceHint": "企业数据治理案例", "sourceName": "机构官网",
            "sourceHintUrl": "https://news.example.cn/article/1", "sourceImages": urls,
        }
        def profile(url):
            if url == urls[-1]:
                return ImageProfile(1280, 720, 16/9, "good-sixth", True)
            return ImageProfile(0, 0, 0.0, "", False)
        with patch.object(serper_images, "available", return_value=True), \
             patch.object(serper_images, "search_images") as search, \
             patch.object(visuals, "discover_source_images", return_value=[]), \
             patch.object(visuals, "image_profile", side_effect=profile):
            out, _ = visuals.resolve_visuals([slot], "数据治理")
        search.assert_not_called()
        self.assertEqual(out[0]["image"]["url"], urls[-1])

    def test_source_page_meta_image_recovers_real_news_image_when_extract_omits_it(self):
        from app import visuals, serper_images
        from app.image_fetch import ImageProfile
        slot = {
            "slotId": "body-1", "kind": "body", "query": "数据治理 企业业务",
            "afterHeading": "数据是业务的事", "anchorText": "企业把数据治理嵌入业务流程。",
            "purpose": "展示真实业务场景", "sourceExplicit": True,
            "sourceHint": "企业数据治理案例", "sourceName": "机构官网",
            "sourceHintUrl": "https://news.example.cn/article/1", "sourceImages": [],
        }
        hero = "https://news.example.cn/images/hero.jpg"
        with patch.object(serper_images, "available", return_value=True), \
             patch.object(serper_images, "search_images", return_value=[]), \
             patch.object(visuals, "discover_source_images", return_value=[{"url": hero, "description": "企业数据治理项目现场", "htmlScore": 8}]), \
             patch.object(visuals, "image_profile", return_value=ImageProfile(1200, 700, 1.71, "hero", True)):
            out, _ = visuals.resolve_visuals([slot], "数据治理")
        self.assertEqual(out[0]["image"]["provider"], "source-meta")
        self.assertEqual(out[0]["image"]["url"], hero)

    def test_three_requested_body_slots_can_all_resolve_on_first_parallel_lane(self):
        from app import visuals, serper_images
        from app.image_fetch import ImageProfile
        slots = [
            {"slotId": "body-1", "kind": "body", "query": "数据治理 业务", "afterHeading": "数据是业务的事", "anchorText": "业务数据进入经营决策。", "purpose": "业务场景"},
            {"slotId": "body-2", "kind": "body", "query": "数据治理 为什么总失败", "afterHeading": "为什么数据治理总是失败？", "anchorText": "数据孤岛与数据质量问题。", "purpose": "失败原因"},
            {"slotId": "body-3", "kind": "body", "query": "数据治理如何影响普通人", "afterHeading": "数据治理如何影响普通人？", "anchorText": "个人信息和隐私权益。", "purpose": "个人场景"},
        ]
        def candidate(q, count=18):
            if "数据孤岛" in q or "数据质量" in q:
                text, n = "数据治理 数据孤岛 数据质量 企业", 2
            elif "个人信息" in q or "普通人" in q or "数字政务" in q:
                text, n = "个人信息保护 隐私 数字生活 数据权益", 3
            else:
                text, n = "企业数据治理 业务数据 数据平台 管理决策", 1
            return [{
                "url": f"https://img{n}.example.cn/{n}.jpg", "fallbackUrl": "", "description": text,
                "source": "机构来源", "sourceUrl": f"https://source{n}.example.cn/a", "sourceTitle": text,
                "sourceSnippet": text, "resultScore": 0.95, "provider": "serper",
            }]
        with patch.object(serper_images, "available", return_value=True), \
             patch.object(serper_images, "search_images", side_effect=candidate) as search, \
             patch.object(visuals, "image_profile", side_effect=lambda u: ImageProfile(1280, 720, 16/9, u, True)):
            out, _ = visuals.resolve_visuals(slots, "数据治理")
        self.assertEqual(sum(1 for v in out if v.get("image")), 3)
        self.assertEqual(search.call_count, 3)


class V28SelfAuditRegressionTests(unittest.TestCase):
    def test_source_page_metadata_still_works_without_serper_key(self):
        from app import visuals, serper_images
        from app.image_fetch import ImageProfile
        slot = {
            "slotId": "body-1", "kind": "body", "query": "数据治理 企业业务",
            "afterHeading": "企业为什么要治理数据", "anchorText": "业务流程开始依赖统一的数据标准。",
            "purpose": "企业数据治理真实场景", "sourceExplicit": True,
            "sourceHint": "企业数据治理案例", "sourceName": "机构官网",
            "sourceHintUrl": "https://news.example.cn/article/1", "sourceImages": [],
        }
        hero = "https://news.example.cn/images/hero.jpg"
        with patch.object(serper_images, "available", return_value=False), \
             patch.object(visuals, "discover_source_images", return_value=[{
                 "url": hero, "description": "企业数据治理项目现场", "htmlScore": 9,
                 "origin": "page-meta", "widthHint": 1600, "heightHint": 900,
             }]) as discover, \
             patch.object(visuals, "image_profile", return_value=ImageProfile(1600, 900, 16/9, "hero", True)):
            out, _ = visuals.resolve_visuals([slot], "数据治理")
        discover.assert_called_once()
        self.assertEqual(out[0]["image"]["provider"], "source-meta")
        self.assertEqual(out[0]["image"]["url"], hero)

    def test_probe_budget_is_fair_across_eight_body_slots(self):
        from app.visuals import _fair_probe_urls
        slots = []
        rows = {}
        for i in range(1, 9):
            slot = {
                "slotId": f"body-{i}", "kind": "body", "query": f"数据治理 场景{i}",
                "afterHeading": f"场景{i}", "anchorText": "数据治理真实业务场景",
            }
            slots.append(slot)
            rows[slot["slotId"]] = [
                {
                    "url": f"https://img{i}.example.cn/{j}.jpg", "description": f"数据治理 场景{i}",
                    "sourceTitle": f"数据治理 场景{i}", "sourceSnippet": "数据治理真实业务场景",
                    "sourceUrl": f"https://source{i}.example.cn/a", "provider": "serper", "resultScore": 0.9,
                }
                for j in range(1, 8)
            ]
        urls = _fair_probe_urls(
            slots, rows, query="数据治理", source_policy="balanced", match_mode="precise",
            per_slot=7, cap=32, known=set(),
        )
        self.assertEqual(len(urls), 32)
        for i in range(1, 9):
            self.assertTrue(any(f"img{i}.example.cn" in u for u in urls), i)

    def test_short_ai_anchor_does_not_match_inside_unrelated_latin_words(self):
        from app.visuals import _contains_any
        self.assertFalse(_contains_any("retail main detail photography", {"AI"}))
        self.assertTrue(_contains_any("AI data platform", {"AI"}))

    def test_same_host_serper_result_is_not_treated_as_exact_cited_page(self):
        from app.visuals import _has_semantic_anchor
        slot = {
            "kind": "body", "query": "数据治理 企业业务", "afterHeading": "业务中的数据治理",
            "anchorText": "企业将数据治理嵌入业务流程。", "sourceExplicit": True,
            "sourceHint": "华为：数据是业务的事", "sourceHintUrl": "https://media.example.cn/news/data-governance.html",
        }
        unrelated = {
            "provider": "serper", "description": "城市旅游摄影", "source": "同一媒体",
            "sourceUrl": "https://media.example.cn/travel/city.html", "sourceTitle": "城市旅游周末攻略",
            "sourceSnippet": "景点与酒店推荐",
        }
        self.assertFalse(_has_semantic_anchor(unrelated, slot, "数据治理"))

    def test_modern_srcset_parser_prefers_large_article_image(self):
        from app.source_page_images import _ImageMetaParser, _best_srcset_url
        self.assertEqual(_best_srcset_url("small.jpg 320w, hero.jpg 1280w"), "hero.jpg")
        parser = _ImageMetaParser()
        parser.feed('<article><picture><source srcset="small.jpg 320w, hero.jpg 1280w"><img src="tiny.gif" alt="数据治理项目现场"></picture></article>')
        self.assertTrue(any(row["url"] == "hero.jpg" for row in parser.content_images))

    def test_sanitizer_preserves_all_eight_body_image_bindings(self):
        from app.pipeline import _sanitize_article
        article = {
            "titleCandidates": ["数据治理为什么真正影响业务"],
            "recommendedTitle": "数据治理为什么真正影响业务",
            "markdown": "这是一段正常正文。",
            "imageQueries": [f"数据治理 场景{i}" for i in range(9)],
            "imageSlots": [{"afterHeading": f"标题{i}", "sourceId": i} for i in range(1, 9)],
        }
        clean = _sanitize_article(article, "数据治理")
        self.assertEqual(len(clean["imageSlots"]), 8)
        self.assertEqual(len(clean["imageQueries"]), 9)
        self.assertEqual(clean["imageSlots"][-1]["sourceId"], 8)

    def test_chinese_serper_dates_are_normalized(self):
        from app.serper_search import _normalize_date
        from datetime import datetime, timezone, timedelta
        self.assertEqual(_normalize_date("2026年8月1日"), "2026-08-01")
        expected = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
        self.assertEqual(_normalize_date("2天前"), expected)
        self.assertEqual(_normalize_date("昨天"), (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat())

    def test_query_intent_does_not_shred_data_middle_platform_or_misread_data_center(self):
        from app.query_intent import local_plan
        platform = local_plan("数据中台对业务有什么影响")
        center = local_plan("数据中心液冷技术")
        self.assertTrue(platform["isConceptualQuery"])
        self.assertTrue(any("数据中台" in group for group in (platform.get("conceptGroups") or [])))
        self.assertFalse(center["isConceptualQuery"])


class V28VisualPlacementTests(unittest.TestCase):
    def test_spread_indexes_never_collapses_requested_slots(self):
        from app.content_blocks import _spread_indexes
        self.assertEqual(len(_spread_indexes(4, 3)), 3)
        self.assertEqual(len(set(_spread_indexes(4, 3))), 3)
        self.assertEqual(len(_spread_indexes(2, 3)), 2)
        self.assertEqual(len(_spread_indexes(8, 8)), 8)

    def test_visual_with_paragraph_index_is_not_consumed_at_heading(self):
        from app.content_blocks import merge_visuals_into_blocks
        md = "## 机制\n\n第一段解释背景。\n\n第二段给出具体案例。"
        visual = {
            "slotId": "body-1", "kind": "body", "afterHeading": "机制",
            "anchorText": "第二段给出具体案例。", "anchorBlockIndex": "2", "placement": "paragraph",
            "purpose": "案例现场", "query": "数据治理案例",
            "image": {"url": "https://img.example.cn/a.jpg", "source": "来源", "provider": "serper"},
        }
        blocks = merge_visuals_into_blocks(md, [visual])
        types = [b.get("type") for b in blocks]
        # heading, first paragraph, second paragraph, image
        self.assertEqual(types, ["heading", "paragraph", "paragraph", "image"])


class V29ImagePrecisionRegressionTests(unittest.TestCase):
    def test_qr_visual_is_rejected_even_with_opaque_cdn_filename(self):
        from PIL import Image, ImageDraw
        from app.image_fetch import _visual_artifact_reason
        import random

        modules, scale, border = 41, 8, 4
        side = (modules + border * 2) * scale
        image = Image.new("RGB", (side, side), "white")
        draw = ImageDraw.Draw(image)
        random.seed(29)
        for y in range(modules):
            for x in range(modules):
                if random.random() < 0.34:
                    x0, y0 = (x + border) * scale, (y + border) * scale
                    draw.rectangle((x0, y0, x0 + scale - 1, y0 + scale - 1), fill="black")

        def finder(x0: int, y0: int) -> None:
            for yy in range(7):
                for xx in range(7):
                    black = xx in (0, 6) or yy in (0, 6) or (2 <= xx <= 4 and 2 <= yy <= 4)
                    px, py = (x0 + xx + border) * scale, (y0 + yy + border) * scale
                    draw.rectangle((px, py, px + scale - 1, py + scale - 1), fill="black" if black else "white")

        finder(0, 0); finder(modules - 7, 0); finder(0, modules - 7)
        self.assertEqual(_visual_artifact_reason(image), "qr-code")

    def test_black_white_editorial_diagram_is_not_mistaken_for_qr(self):
        from PIL import Image, ImageDraw
        from app.image_fetch import _visual_artifact_reason
        image = Image.new("RGB", (720, 540), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((70, 70, 650, 470), outline="black", width=4)
        for y in (150, 250, 350):
            draw.line((100, y, 620, y - 35), fill="black", width=5)
        for x in (180, 300, 420, 540):
            draw.ellipse((x, 180, x + 24, 204), fill="black")
        self.assertEqual(_visual_artifact_reason(image), "")

    def test_source_parser_rejects_follow_qr_by_alt_even_when_url_is_opaque(self):
        from app.source_page_images import _ImageMetaParser
        parser = _ImageMetaParser()
        parser.feed('<article><img src="https://cdn.example.cn/ab12345.png" width="600" height="600" alt="扫码关注微信公众号"></article>')
        self.assertEqual(parser.content_images, [])

    def test_warehouse_paragraph_prefers_warehouse_scene_over_generic_ai_car_image(self):
        from app.visuals import _has_semantic_anchor, _visual_query_variants
        slot = {
            "kind": "body", "query": "数据治理 企业业务",
            "afterHeading": "正文",
            "anchorText": "仓库管理员做了一次入库，系统马上产生一条库存数据，并进入后续补货流程。",
            "purpose": "展示这一业务动作如何产生可治理的数据",
        }
        queries = _visual_query_variants(slot, "数据治理", match_mode="precise")
        self.assertTrue(any("仓库" in q and ("库存" in q or "入库" in q) for q in queries), queries)
        unrelated = {
            "provider": "serper", "description": "广州人工智能数据治理活动 自动驾驶车辆识别",
            "sourceTitle": "AI 计算机视觉汽车识别", "sourceSnippet": "人工智能 数据治理 技术活动",
            "source": "科技媒体", "sourceUrl": "https://example.cn/ai-cars",
        }
        related = {
            "provider": "serper", "description": "仓库库存管理 入库扫码与库存数据采集",
            "sourceTitle": "仓储管理系统中的库存数据", "sourceSnippet": "仓库 入库 库存管理 物流",
            "source": "产业媒体", "sourceUrl": "https://example.cn/warehouse",
        }
        self.assertFalse(_has_semantic_anchor(unrelated, slot, "数据治理"))
        self.assertTrue(_has_semantic_anchor(related, slot, "数据治理"))

    def test_qr_source_meta_is_skipped_and_parallel_serper_candidate_can_win(self):
        from app import visuals, serper_images
        from app.image_fetch import ImageProfile
        slot = {
            "slotId": "body-1", "kind": "body", "query": "数据治理 企业业务",
            "afterHeading": "正文", "anchorText": "仓库管理员完成入库，产生库存数据。",
            "purpose": "展示仓储业务数据产生过程", "sourceExplicit": True,
            "sourceHint": "企业数据治理案例", "sourceName": "案例媒体",
            "sourceHintUrl": "https://news.example.cn/article/1", "sourceImages": [],
        }
        qr = "https://cdn.example.cn/opaque-a1.png"
        good = "https://img.example.cn/warehouse.jpg"

        def search_images(query, count=18):
            return [{
                "url": good, "description": "仓库 库存管理 入库 数据采集",
                "source": "产业媒体", "sourceUrl": "https://industry.example.cn/warehouse",
                "sourceTitle": "仓储管理与库存数据", "sourceSnippet": "仓库 入库 库存数据 物流",
                "resultScore": 0.95, "provider": "serper",
            }]

        def profile(url):
            if url == qr:
                return ImageProfile(600, 600, 1.0, "qr", False, "qr-code")
            return ImageProfile(1280, 720, 16/9, "warehouse", True)

        with patch.object(serper_images, "available", return_value=True), \
             patch.object(serper_images, "search_images", side_effect=search_images), \
             patch.object(visuals, "discover_source_images", return_value=[{
                 "url": qr, "description": "", "htmlScore": 8, "origin": "page-meta",
             }]), \
             patch.object(visuals, "image_profile", side_effect=profile):
            out, _ = visuals.resolve_visuals([slot], "数据治理")
        self.assertEqual(out[0]["image"]["url"], good)
        self.assertEqual(out[0]["image"]["provider"], "serper")

class V29RootSourceImageSafetyTests(unittest.TestCase):
    def test_publisher_homepage_is_not_treated_as_exact_article_image_source(self):
        from app import visuals, serper_images
        from app.image_fetch import ImageProfile
        slot = {
            "slotId": "body-1", "kind": "body", "query": "数据治理 为什么会失败",
            "afterHeading": "正文", "anchorText": "数据标准不统一会让治理项目反复失败。",
            "purpose": "解释数据治理失败原因", "sourceExplicit": True,
            "sourceHint": "独山子石化数据治理", "sourceName": "地方媒体",
            "sourceHintUrl": "https://www.example.cn/", "sourceImages": ["https://www.example.cn/opaque-follow-card.png"],
        }
        good = "https://img.example.net/data-quality.jpg"
        with patch.object(serper_images, "available", return_value=True), \
             patch.object(serper_images, "search_images", return_value=[{
                 "url": good, "description": "数据治理 数据质量 数据标准 企业",
                 "source": "产业媒体", "sourceUrl": "https://industry.example.net/data-quality",
                 "sourceTitle": "数据治理为什么失败：数据质量与标准", "sourceSnippet": "数据质量 数据标准 数据治理",
                 "resultScore": 0.96, "provider": "serper",
             }]) as search, \
             patch.object(visuals, "discover_source_images") as discover, \
             patch.object(visuals, "image_profile", return_value=ImageProfile(1280, 720, 16/9, "good", True)):
            out, _ = visuals.resolve_visuals([slot], "数据治理")
        discover.assert_not_called()
        self.assertGreaterEqual(search.call_count, 1)
        self.assertEqual(out[0]["image"]["url"], good)

    def test_root_like_url_detection_allows_article_query_urls(self):
        from app.visuals import _is_root_like_url
        self.assertTrue(_is_root_like_url("https://media.example.cn/"))
        self.assertTrue(_is_root_like_url("https://media.example.cn"))
        self.assertFalse(_is_root_like_url("https://media.example.cn/news/123.html"))
        self.assertFalse(_is_root_like_url("https://media.example.cn/?id=123"))


class V30HybridCodeVisualTests(unittest.TestCase):
    def _slot(self, **extra):
        slot = {
            "slotId": "body-1", "kind": "body", "query": "数据治理 机制",
            "afterHeading": "数据如何进入经营", "anchorText": "原始数据经过标准、质量和权属治理，形成可复用的数据产品，并进入业务决策。",
            "purpose": "解释数据从治理到业务价值的链路", "visualIntent": "auto",
        }
        slot.update(extra)
        return slot

    def test_all_diagram_skips_serper_and_draws_body(self):
        from app import visuals, serper_images
        calls = []
        with patch.object(serper_images, "available", return_value=True), \
             patch.object(serper_images, "search_images", side_effect=lambda q, count=18: calls.append(q) or []):
            out, _ = visuals.resolve_visuals([self._slot()], "数据治理", strategy="all_diagram")
        self.assertEqual(calls, [])
        self.assertEqual(out[0]["image"]["provider"], "generated-diagram")
        self.assertTrue(out[0]["image"]["url"].startswith("data:image/png;base64,"))

    def test_smart_explicit_diagram_draws_before_web_search(self):
        from app import visuals, serper_images
        calls = []
        slot = self._slot(visualIntent="diagram", visualType="flow", visualPlan={"nodes": ["原始数据", "标准治理", "数据产品", "业务应用"]})
        with patch.object(serper_images, "available", return_value=True), \
             patch.object(serper_images, "search_images", side_effect=lambda q, count=18: calls.append(q) or []):
            out, _ = visuals.resolve_visuals([slot], "数据治理", strategy="smart")
        self.assertEqual(calls, [])
        self.assertEqual(out[0]["image"]["generatedKind"], "flow")

    def test_real_first_searches_then_draws_when_search_fails(self):
        from app import visuals, serper_images
        calls = []
        slot = self._slot(visualIntent="real")
        with patch.object(serper_images, "available", return_value=True), \
             patch.object(serper_images, "search_images", side_effect=lambda q, count=18: calls.append(q) or []):
            out, _ = visuals.resolve_visuals([slot], "数据治理", strategy="real_first")
        self.assertGreaterEqual(len(calls), 1)
        self.assertLessEqual(len(calls), 2)
        self.assertEqual(out[0]["image"]["provider"], "generated-diagram")

    def test_real_only_never_draws_fallback(self):
        from app import visuals, serper_images
        slot = self._slot(visualIntent="real")
        with patch.object(serper_images, "available", return_value=True), \
             patch.object(serper_images, "search_images", return_value=[]):
            out, _ = visuals.resolve_visuals([slot], "数据治理", strategy="real_only")
        self.assertIsNone(out[0]["image"])
        self.assertTrue(str(out[0]["matchedBy"]).startswith("unresolved"))

    def test_visual_plan_survives_slot_planning(self):
        from app.content_blocks import plan_visual_slots
        article = {
            "recommendedTitle": "数据如何进入经营",
            "markdown": "## 数据如何进入经营\n\n原始数据经过标准、质量和权属治理，形成数据产品并进入业务。",
            "imageSlots": [{
                "afterHeading": "数据如何进入经营", "purpose": "解释链路", "query": "数据治理 链路",
                "visualIntent": "diagram", "visualType": "flow", "visualPlan": {"title": "从数据到价值", "nodes": ["原始数据", "标准治理", "数据产品", "业务应用"]},
            }],
        }
        slots = plan_visual_slots(article, "数据治理", max_body=1)
        body = slots[1]
        self.assertEqual(body["visualIntent"], "diagram")
        self.assertEqual(body["visualType"], "flow")
        self.assertEqual(body["visualPlan"]["nodes"][2], "数据产品")

    def test_image_slot_sanitizer_bounds_visual_dsl(self):
        from app.pipeline import _sanitize_image_slots
        rows = _sanitize_image_slots([{
            "afterHeading": "A" * 300, "visualIntent": "bad", "visualType": "unknown", "sourceId": "7",
            "visualPlan": {"title": "T" * 200, "nodes": ["N" * 200] * 12, "metrics": [{"value": "42", "label": "条规定", "extra": "x" * 200}] * 8},
        }])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["visualIntent"], "auto")
        self.assertEqual(rows[0]["visualType"], "")
        self.assertEqual(rows[0]["sourceId"], 7)
        self.assertLessEqual(len(rows[0]["visualPlan"]["title"]), 80)
        self.assertEqual(len(rows[0]["visualPlan"]["nodes"]), 6)
        self.assertEqual(len(rows[0]["visualPlan"]["metrics"]), 6)

    def test_cover_style_changes_with_content_not_one_fixed_template(self):
        from app.code_visuals import choose_cover_style
        self.assertEqual(choose_cover_style("全国统一数据产权登记工作指引"), "policy")
        self.assertEqual(choose_cover_style("研发周期从3年缩短到8个月"), "number")
        self.assertEqual(choose_cover_style("制造企业仓库如何沉淀库存数据"), "scene")
        self.assertEqual(choose_cover_style("可信数据空间里的多方协同"), "network")

    def test_number_cover_keeps_number_and_unit_semantically_paired(self):
        from app.code_visuals import _number_focus
        self.assertEqual(_number_focus("研发周期从3年缩短到8个月"), ("8", "个月"))
        self.assertEqual(_number_focus("政策共42条，覆盖数据登记全流程"), ("42", "条"))

    def test_kpi_renderer_does_not_require_fabricated_numbers(self):
        from app.code_visuals import build_body_visual
        image = build_body_visual(self._slot(visualIntent="diagram", visualType="kpi", visualPlan={"nodes": ["关键结论", "质量边界", "应用条件"]}), "数据治理")
        self.assertEqual(image["provider"], "generated-diagram")
        self.assertTrue(image["url"].startswith("data:image/png;base64,"))

    def test_compose_exposes_five_image_strategies(self):
        html = (ROOT / "web" / "compose.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "compose.js").read_text(encoding="utf-8")
        self.assertIn('id="imageStrategy"', html)
        for value in ("smart", "real_first", "diagram_first", "all_diagram", "real_only"):
            self.assertIn(f'value="{value}"', html)
        self.assertIn('imageStrategy: $("#imageStrategy")?.value || "smart"', js)
        self.assertIn("syncImageStrategyControls", js)

    def test_local_font_discovery_is_safe(self):
        from app.code_visuals import _discover_font
        found = _discover_font("regular")
        if found is not None:
            self.assertTrue(found.exists())
            self.assertTrue(found.is_file())

    def test_server_health_advertises_local_code_visuals(self):
        text = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn('"codeVisualsAvailable": True', text)
        self.assertIn('"version": "30.0"', text)
