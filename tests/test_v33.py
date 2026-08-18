from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class V33GenerationReliabilityTests(unittest.TestCase):
    def test_long_markdown_returned_despite_json_mode_is_reused_without_second_call(self):
        from app import deepseek
        calls = []
        prose = "# 真正的标题\n\n## 第一部分\n\n" + ("这是有事实边界的完整正文。" * 80)
        def fake_request(*args, **kwargs):
            calls.append(kwargs)
            return {"choices": [{"message": {"content": prose}, "finish_reason": "stop"}], "usage": {"total_tokens": 80}}
        fake_settings = type("S", (), {"deepseek_api_key": "k", "deepseek_model": "deepseek-v4-pro", "deepseek_base_url": "https://api.deepseek.com"})()
        with patch.object(deepseek, "settings", fake_settings), patch.object(deepseek, "request_json", side_effect=fake_request):
            result = deepseek.generate_json("只输出 JSON", "写文章", max_tokens=1600)
        self.assertEqual(len(calls), 1)
        self.assertIn("完整正文", result["markdown"])
        self.assertEqual(calls[0]["payload"]["thinking"]["type"], "disabled")

    def test_empty_json_mode_falls_back_once_to_markdown(self):
        from app import deepseek
        responses = [
            {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}], "usage": {}},
            {"choices": [{"message": {"content": "# 标题\n\n## 小标题\n\n正文内容。"}, "finish_reason": "stop"}], "usage": {"total_tokens": 30}},
        ]
        calls = []
        def fake_request(*args, **kwargs):
            calls.append(kwargs["payload"])
            return responses.pop(0)
        fake_settings = type("S", (), {"deepseek_api_key": "k", "deepseek_model": "deepseek-v4-flash", "deepseek_base_url": "https://api.deepseek.com"})()
        with patch.object(deepseek, "settings", fake_settings), patch.object(deepseek, "request_json", side_effect=fake_request):
            result = deepseek.generate_json("只输出 JSON", "写文章", max_tokens=1200)
        self.assertEqual(len(calls), 2)
        self.assertIn("正文内容", result["markdown"])

    def test_truncated_json_article_is_locally_recovered_if_second_request_fails(self):
        from app import deepseek
        body = "# 标题\n\n## 第一节\n\n" + ("这是已经生成完成的大部分正文内容。" * 90)
        # Mimic a provider that emitted most of the markdown JSON string but was cut
        # before the closing quote/object; then make the plain-markdown salvage call fail.
        encoded = json.dumps(body, ensure_ascii=False)[1:-1]
        first = '{"titleCandidates":["标题"],"markdown":"' + encoded
        fake_settings = type("S", (), {"deepseek_api_key": "k", "deepseek_model": "deepseek-v4-pro", "deepseek_base_url": "https://api.deepseek.com"})()
        responses = [
            {"choices": [{"message": {"content": first}, "finish_reason": "length"}], "usage": {}},
            RuntimeError("temporary upstream failure"),
        ]
        def fake_request(*args, **kwargs):
            value = responses.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        with patch.object(deepseek, "settings", fake_settings), patch.object(deepseek, "request_json", side_effect=fake_request):
            result = deepseek.generate_json("只输出 JSON", "写文章", max_tokens=1200)
        self.assertIn("大部分正文内容", result["markdown"])
        self.assertEqual(result["_deepseekMeta"]["finishReason"], "partial-json-salvage")

    def test_auto_evidence_does_not_broaden_when_three_usable_sources_exist(self):
        from app import pipeline
        rows = [{"id": str(i), "sourceVerified": True, "score": 90} for i in range(3)]
        calls = []
        def fake_research(payload):
            calls.append(payload)
            return {"results": rows, "warnings": []}
        with patch.object(pipeline, "research", side_effect=fake_research):
            out = pipeline._research_for_article_evidence("数据治理")
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(out["results"]), 3)

    def test_two_generation_jobs_never_reuse_previous_result(self):
        from app.generation_jobs import GenerationJobStore
        store = GenerationJobStore(max_items=8, ttl_seconds=60, workers=2)
        def worker(payload):
            time.sleep(0.03)
            q = payload["query"]
            return {"articleId": f"id-{q}", "query": q, "recommendedTitle": q, "markdown": f"{q} 正文"}
        a = store.start({"query": "主题甲"}, worker)
        b = store.start({"query": "主题乙"}, worker)
        deadline = time.time() + 2
        while time.time() < deadline:
            ra, rb = store.get(a["generationJobId"]), store.get(b["generationJobId"])
            if ra and rb and ra["status"] == rb["status"] == "ready":
                break
            time.sleep(0.02)
        self.assertEqual(ra["article"]["query"], "主题甲")
        self.assertEqual(rb["article"]["query"], "主题乙")
        self.assertNotEqual(ra["article"]["articleId"], rb["article"]["articleId"])


class V33EvidenceAndStructureTests(unittest.TestCase):
    def test_uploaded_material_is_prioritized_ahead_of_auto_search(self):
        from app.pipeline import _prioritize_writing_sources
        rows = [
            {"id": "auto", "origin": "auto", "type": "news", "score": 99, "sourceVerified": True},
            {"id": "manual", "origin": "search", "type": "policy", "selectedByUser": True, "score": 70},
            {"id": "upload", "origin": "upload", "type": "upload", "score": 1},
        ]
        ordered = _prioritize_writing_sources(rows)
        self.assertEqual([x["id"] for x in ordered], ["upload", "manual", "auto"])

    def test_article_prompt_puts_uploaded_material_before_auto_web_and_gives_it_more_text(self):
        from app import pipeline
        captured = {}
        upload_text = "上传材料核心事实。" * 260
        auto_text = "网络补充事实。" * 260
        def fake_generate_json(system_prompt, user_prompt, **kwargs):
            captured["prompt"] = user_prompt
            return {
                "titleCandidates": ["标题"], "recommendedTitle": "标题", "deck": "导语",
                "markdown": "## 第一部分\n\n正文。\n\n## 第二部分\n\n正文。\n\n## 第三部分\n\n正文。",
                "keyClaims": [], "riskNotes": [], "sourceNotes": [], "_deepseekMeta": {"apiCalled": True, "totalTokens": 1},
            }
        sources = [
            {"id":"auto","origin":"auto","type":"news","title":"网络资料","rawContent":auto_text,"score":99},
            {"id":"upload","origin":"upload","type":"upload","title":"用户文件","rawContent":upload_text,"selectedByUser":True},
        ]
        with patch.object(pipeline.deepseek, "generate_json", side_effect=fake_generate_json):
            pipeline._llm_article("数据治理", sources, "行业观察", "普通公众", "短篇", True, False, length_spec={"min":800,"target":1100,"max":1400})
        prompt = captured["prompt"]
        self.assertLess(prompt.index("用户文件"), prompt.index("网络资料"))
        self.assertGreater(prompt.count("上传材料核心事实"), prompt.count("网络补充事实"))

    def test_wechat_heading_range_is_never_zero_and_is_independent_of_image_count(self):
        from app.pipeline import _section_count_range
        self.assertEqual(_section_count_range({"target": 1200}), (3, 4))
        self.assertEqual(_section_count_range({"target": 2100}), (4, 6))
        self.assertEqual(_section_count_range({"target": 3300}), (5, 7))

    def test_visual_slot_keeps_paragraph_context_for_relationship_planning(self):
        from app.content_blocks import plan_visual_slots
        paragraph = "企业把历史实验数据整理成统一格式后，模型先缩小候选空间，工程师再做小步验证，验证结果继续回流数据集，形成下一轮训练依据。"
        article = {"recommendedTitle": "研发试错正在改变", "markdown": f"## 从经验试错到数据闭环\n\n{paragraph}\n\n后续正文。", "imageSlots": [], "imageQueries": []}
        slots = plan_visual_slots(article, "数据驱动研发", max_body=1)
        body = next(x for x in slots if x["kind"] == "body")
        self.assertIn("模型先缩小候选空间", body.get("contextText", ""))
        self.assertGreaterEqual(len(body.get("contextText", "")), len(body.get("anchorText", "")))


class V33VisualSafetyTests(unittest.TestCase):
    def test_missing_cjk_font_disables_code_visual_without_breaking_article_visual_resolution(self):
        from app import visuals, serper_images
        slots = [
            {"slotId": "cover", "kind": "cover", "query": "数据治理", "coverTitle": "数据治理", "coverBrief": "封面"},
            {"slotId": "body-1", "kind": "body", "query": "数据治理关系", "afterHeading": "关系", "anchorText": "数据标准影响质量，质量进一步影响业务决策。", "purpose": "解释关系"},
        ]
        with patch.object(visuals, "code_visuals_available", return_value=False), patch.object(serper_images, "available", return_value=False):
            out, warnings = visuals.resolve_visuals(slots, "数据治理", strategy="smart")
        self.assertEqual(len(out), 2)
        self.assertTrue(all(x.get("image") is None for x in out))
        self.assertTrue(any("中文字体" in w for w in warnings))

    def test_relation_renderer_draws_grounded_edges(self):
        from app.code_visuals import build_body_visual, code_visuals_available
        if not code_visuals_available():
            self.skipTest("CJK font unavailable in test environment")
        slot = {
            "afterHeading": "数据怎样进入业务闭环",
            "anchorText": "业务产生数据，治理规则提升数据质量，模型消费可信数据，决策结果再回流业务。",
            "contextText": "业务产生数据，治理规则提升数据质量，模型消费可信数据，决策结果再回流业务。",
            "purpose": "画清楚数据与业务的闭环关系",
            "visualIntent": "diagram",
            "visualType": "relation",
            "visualPlan": {
                "title": "数据进入业务的闭环",
                "nodes": ["业务", "数据", "治理规则", "模型", "决策"],
                "edges": [
                    {"from": "业务", "to": "数据", "label": "产生"},
                    {"from": "数据", "to": "治理规则", "label": "进入治理"},
                    {"from": "治理规则", "to": "模型", "label": "可信供给"},
                    {"from": "模型", "to": "决策", "label": "支持"},
                    {"from": "决策", "to": "业务", "label": "反馈"},
                ],
            },
        }
        image = build_body_visual(slot, "数据治理")
        self.assertEqual(image["generatedKind"], "relation")
        self.assertTrue(image["url"].startswith("data:image/png;base64,"))
        self.assertGreater(len(image["url"]), 10000)


class V33CacheHistoryAndUiTests(unittest.TestCase):
    def test_home_feed_reuses_same_snapshot_for_seven_days(self):
        from app import home_feed as module
        with tempfile.TemporaryDirectory() as td:
            cache_file = Path(td) / "home.json"
            calls = []
            def fake_research(payload):
                calls.append(payload)
                return {"results": [{"id": str(len(calls)), "title": "数据政策", "type": payload["types"][0], "score": 90, "queryMatchScore": 90, "sourceUsable": True, "url": "https://example.cn/a" + str(len(calls))}], "warnings": []}
            with patch.object(module, "_CACHE_FILE", cache_file), patch.object(module, "_CACHE", {"at": 0.0, "data": None}), patch.object(module, "research", side_effect=fake_research):
                first = module.home_feed()
                first_calls = len(calls)
                second = module.home_feed()
            self.assertEqual(first_calls, 5)
            self.assertEqual(len(calls), 5)
            self.assertTrue(second["cached"])
            self.assertEqual(second["cacheTtlDays"], 7)

    def test_history_persists_text_snapshot(self):
        from app.history_store import HistoryStore
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "history.json"
            store = HistoryStore(path=path, max_items=10)
            hid = store.record({"recommendedTitle": "第一篇", "markdown": "## 小标题\n\n正文", "query": "主题一"}, query="主题一", article_id="a1")
            store2 = HistoryStore(path=path, max_items=10)
            item = store2.get(hid)
            self.assertEqual(item["recommendedTitle"], "第一篇")
            self.assertIn("正文", item["markdown"])
            self.assertTrue(item["archived"])

    def test_compose_ui_has_new_article_history_and_no_smart_section_toggle(self):
        html = (ROOT / "web" / "compose.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "compose.js").read_text(encoding="utf-8")
        self.assertIn('id="newArticleButton"', html)
        self.assertIn('id="historyButton"', html)
        self.assertIn('id="historyModal"', html)
        self.assertNotIn('id="smartSectionsToggle"', html)
        self.assertNotIn('smartSections:', js)
        self.assertIn("startAnotherArticle", js)
        self.assertIn("dropStaleAutoEvidence(topic)", js)

    def test_home_browser_cache_is_seven_days_and_returns_without_fetch_when_fresh(self):
        js = (ROOT / "web" / "home.js").read_text(encoding="utf-8")
        self.assertIn("7 * 24 * 60 * 60 * 1000", js)
        self.assertIn("deg.homeFeed.v33", js)
        self.assertIn("const HOME_CACHE_TTL = 7 * 24 * 60 * 60 * 1000", js)
        self.assertIn("Date.now() - Number(cached.savedAt || 0) > HOME_CACHE_TTL", js)

    def test_docker_installs_cjk_font_for_code_visuals(self):
        docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("fonts-noto-cjk", docker)


if __name__ == "__main__":
    unittest.main()
