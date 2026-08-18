import io
import json
import unittest
import zipfile
from unittest.mock import patch

from app.article_store import ArticleStore, article_store
from app import source_verify
from app import tavily, visuals, deepseek, serper_images
from app.cache import TTLCache
from app.content_blocks import merge_visuals_into_blocks, plan_visual_slots
from app.exporter import build_docx, build_pdf
from app.file_ingest import ingest_file
from app.home_feed import home_feed
from app.pipeline import generate_article, research
from app.scoring import authority_score, normalize_title, overall_score
from app.source_verify import Verification, verify_results


def fake_llm(system_prompt, user_prompt, **kwargs):
    import re
    text = f"{system_prompt}\n{user_prompt}"
    ranges = re.findall(r"(\d{3,5})[—-](\d{3,5})\s*字", text)
    low, high = (map(int, ranges[-1]) if ranges else (1800, 2400))
    target = (low + high) // 2
    sent = "数据要素治理需要把权责、质量、授权、使用和风险控制连成闭环。"
    heads = ["问题从哪里出现", "机制如何运转", "产业落地怎么看", "下一步观察什么"]
    parts = []
    for h in heads:
        chunk = (sent * 80)[: max(260, target // 4)]
        parts += [f"## {h}", chunk]
    return {
        "titleCandidates": ["数据要素治理：从资源管理走向可验证的价值闭环", "数据治理真正难在哪", "数据要素治理进入深水区"],
        "deck": "围绕规则、流通与价值回流，重新理解数据要素治理。",
        "markdown": "\n\n".join(parts),
        "coverBrief": "真实的数据治理产业场景",
        "imageQueries": ["数据治理 项目 现场", "可信数据空间 项目", "数据交易 产业场景"],
        "imageSlots": [{"afterHeading": heads[1], "purpose": "展示机制落地", "query": "可信数据空间 项目现场"}],
        "socialSummary": "治理闭环比数据规模更关键。",
        "keyClaims": [{"claim": "治理需要闭环", "sourceIds": [1], "confidence": "medium"}],
        "riskNotes": [], "sourceNotes": [],
        "_deepseekMeta": {"apiCalled": True, "model": "deepseek-v4-flash", "finishReason": "stop", "promptTokens": 1200, "completionTokens": target, "reasoningTokens": 100, "totalTokens": 1300 + target},
    }


def fake_visuals(slots, query, **kwargs):
    out=[]
    for i, slot in enumerate(slots):
        out.append({**slot, "image": {"url": f"https://img.example.org/{i}.jpg", "description": f"与{slot.get('afterHeading') or query}对应的真实图片", "source": "测试来源", "sourceUrl": f"https://source.example.org/{i}", "provider": "serper", "width": 1200, "height": 800}})
    return out, []


def _generated(payload=None):
    payload = payload or {"query": "数据要素治理", "sources": [], "options": {"autoEvidence": False}}
    with patch.object(deepseek, "available", return_value=True), \
         patch.object(deepseek, "generate_json", side_effect=fake_llm), \
         patch.object(tavily, "available", return_value=False), \
         patch.object(serper_images, "available", return_value=True), \
         patch("app.pipeline.resolve_visuals", side_effect=fake_visuals):
        return generate_article(payload)


class CoreTests(unittest.TestCase):
    def test_authority_and_score(self):
        self.assertGreaterEqual(authority_score("https://www.gov.cn/test", "policy"), 95)
        self.assertTrue(1 <= overall_score(relevance=0.8, authority=90, freshness=85, source_type="news") <= 100)

    def test_cache_copy_safe(self):
        cache = TTLCache(max_items=2, ttl_seconds=60)
        cache.put("a", {"rows": [1, 2]})
        first = cache.get("a"); first["rows"].append(3)
        self.assertEqual(cache.get("a"), {"rows": [1, 2]})

    def test_source_verification_filters_fake_and_aggregator(self):
        rows = [
            {"type": "news", "title": "真实新闻", "url": "https://real.example/a"},
            {"type": "news", "title": "聚合新闻", "url": "https://www.msn.com/zh-cn/a"},
        ]
        with patch("app.source_verify.verify_url", side_effect=[Verification(True, "https://real.example/a", "https://real.example/original", "真实新闻", "真实新闻摘要", 200, "ok"), Verification(False, "https://www.msn.com/zh-cn/a", "", "", "", 0, "aggregator")]):
            verified, warnings = verify_results(rows, limit=2)
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0]["url"], "https://real.example/original")
        self.assertTrue(verified[0]["sourceVerified"])

    def test_tavily_fast_no_images_and_paper_search_uses_same_provider(self):
        captured=[]
        def fake_request(url, **kwargs):
            captured.append(kwargs)
            return {"results":[{"url":"https://papers.example.org/p/1","title":"数据要素治理研究","content":"研究摘要","score":0.88,"published_date":"2026-08-08"}], "response_time":0.1}
        with patch.object(tavily, "available", return_value=True), patch.object(tavily, "request_json", side_effect=fake_request):
            data = tavily.search("数据要素", topic="news", max_results=5, mode="fast", include_images=True)
            papers = tavily.search_papers("数据要素", max_results=3)
        self.assertFalse(captured[0]["payload"]["include_images"])
        self.assertEqual(data["images"], [])
        self.assertEqual(papers[0]["type"], "paper")
        self.assertEqual(papers[0]["source"], "papers.example.org")


    def test_deepseek_json_retries_and_salvages_plain_text(self):
        calls=[]
        responses=[
            {"choices":[{"message":{"content":"not json"},"finish_reason":"stop"}],"usage":{"total_tokens":20}},
            {"choices":[{"message":{"content":"# 一个真实标题\n\n## 第一节\n\n这是正文。"},"finish_reason":"stop"}],"usage":{"total_tokens":30}},
        ]
        def fake_request(*args, **kwargs):
            calls.append(kwargs["payload"]); return responses.pop(0)
        with patch.object(deepseek, "settings", type("S", (), {"deepseek_api_key":"k","deepseek_model":"deepseek-v4-flash","deepseek_base_url":"https://api.deepseek.com"})()), patch.object(deepseek, "request_json", side_effect=fake_request):
            result=deepseek.generate_json("Write valid JSON.", "Return JSON article", max_tokens=1000)
        self.assertEqual(len(calls),2)
        self.assertIn("response_format", calls[0])
        self.assertNotIn("response_format", calls[1])
        self.assertEqual(calls[0]["thinking"]["type"], "disabled")
        self.assertTrue(result["_deepseekMeta"]["apiCalled"])
        self.assertIn("正文", result["markdown"])

    def test_generation_requires_real_deepseek(self):
        with patch.object(deepseek, "available", return_value=False):
            with self.assertRaises(RuntimeError):
                generate_article({"query":"数据要素治理","sources":[],"options":{"autoEvidence":False}})

    def test_generation_has_api_telemetry_length_and_visuals(self):
        article = _generated({"query":"数据要素治理","sources":[],"options":{"autoEvidence":False,"length":"短篇 · 1000—1400字"}})
        self.assertTrue(article["generationMeta"]["apiCalled"])
        self.assertGreater(article["generationMeta"]["totalTokens"],0)
        self.assertTrue(article["articleId"])
        self.assertIn(article["visualStatus"], {"pending", "ready"})
        self.assertEqual(article["visualReport"]["bodyPlanned"], 3)

    def test_writing_controls_enter_prompt(self):
        captured=[]
        def fake(system_prompt,user_prompt,**kwargs):
            captured.append(system_prompt+"\n"+user_prompt); return fake_llm(system_prompt,user_prompt,**kwargs)
        with patch.object(deepseek,"available",return_value=True), patch.object(deepseek,"generate_json",side_effect=fake), patch.object(tavily,"available",return_value=False), patch("app.pipeline.resolve_visuals",side_effect=fake_visuals):
            generate_article({"query":"公共数据授权运营","sources":[],"options":{"autoEvidence":False,"imageCount":0,"style":"政策解读","audience":"企业管理者","length":"短篇 · 1000—1400字","tone":"锐利、有判断","titleMode":"问题型","structure":"现象—原因—影响—建议","opener":"结论先行","paragraphRhythm":"紧凑短段","evidenceStyle":"证据紧跟结论","closingMode":"行动建议"}})
        prompt="\n".join(captured)
        for marker in ["政策解决什么问题", "强调决策影响、成本收益、风险边界和可执行动作", "至少两个标题候选使用真实问题句", "多数段落控制在 2—4 句", "写作角度 / 用户实际要求"]:
            self.assertIn(marker,prompt)

    def test_auto_evidence_off_does_not_inject(self):
        article = _generated({"query":"数据要素治理","sources":[],"options":{"autoEvidence":False,"imageCount":0}})
        self.assertEqual(article["sourceCount"],0)
        self.assertEqual(article["generationMeta"]["autoEvidenceAdded"],0)

    def test_no_verified_sources_means_no_external_evidence(self):
        article = _generated({"query":"数据要素治理","sources":[{"type":"news","title":"假资料","url":"https://example.com/a","sourceVerified":False}],"options":{"autoEvidence":False,"imageCount":0}})
        self.assertEqual(article["sourceCount"],0)

    def test_content_blocks_place_images_after_heading(self):
        article={"markdown":"开头\n\n## 机制变化\n\n正文A\n\n## 产业落地\n\n正文B","imageQueries":["机制"],"imageSlots":[{"afterHeading":"机制变化","purpose":"解释机制","query":"数据机制"}]}
        slots=plan_visual_slots(article,"数据要素",max_body=2)
        body=next(x for x in slots if x["kind"]=="body")
        blocks=merge_visuals_into_blocks(article["markdown"],[{**body,"image":{"url":"https://img.example.org/a.jpg"}}])
        idx=next(i for i,b in enumerate(blocks) if b.get("text")=="机制变化")
        self.assertEqual(blocks[idx+1]["type"],"image")

    def test_docx_pdf_exports_have_media(self):
        article=_generated({"query":"可信数据空间","sources":[],"options":{"autoEvidence":False,"imageCount":0}})
        # Inject a known local image URL so export media embedding is deterministic.
        import base64
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        record=article_store.get(article["articleId"])
        with patch("app.image_fetch.fetch_image", return_value=type("I",(),{"data":png,"content_type":"image/png"})()):
            record["article"]["coverImage"]={"url":"https://img.example.org/a.png","provider":"serper"}
            record["article"]["blocks"]=merge_visuals_into_blocks(record["article"]["markdown"],[{"kind":"cover","image":{"url":"https://img.example.org/a.png","provider":"serper"}}])
            docx=build_docx(record["article"],record["sources"]); pdf=build_pdf(record["article"],record["sources"])
        self.assertTrue(docx.startswith(b"PK")); self.assertTrue(pdf.startswith(b"%PDF"))
        with zipfile.ZipFile(io.BytesIO(docx)) as z:
            self.assertTrue(any(x.startswith("word/media/") for x in z.namelist()))

    def test_file_ingest(self):
        source=ingest_file("notes.txt",("数据要素治理需要明确权责。"*8).encode())
        self.assertTrue(source["selectedByUser"]); self.assertTrue(source["sourceVerified"])

    def test_article_store_undo_restore(self):
        store=ArticleStore(max_items=4); aid=store.put({"markdown":"初稿"},sources=[],query="数据要素")
        store.update(aid,{"markdown":"改1"},save_history=True); store.update(aid,{"markdown":"改2"},save_history=True)
        self.assertEqual(store.undo(aid)["article"]["markdown"],"改1"); self.assertEqual(store.restore_original(aid)["article"]["markdown"],"初稿")

    def test_serper_contract(self):
        payload={"images":[{"title":"可信数据空间项目","imageUrl":"https://img.example.org/a.jpg","thumbnailUrl":"https://thumb.example.org/a.jpg","imageWidth":1200,"imageHeight":800,"source":"机构官网","domain":"gov.example.cn","link":"https://gov.example.cn/news/1","position":1}]}
        with patch.object(serper_images,"available",return_value=True), patch.object(serper_images,"settings",type("S",(),{"serper_api_key":"k"})()), patch.object(serper_images,"request_json",return_value=payload):
            rows=serper_images.search_images("serper-contract-unique",count=8)
        self.assertEqual(rows[0]["provider"],"serper"); self.assertEqual(rows[0]["sourceUrl"],"https://gov.example.cn/news/1"); self.assertEqual(rows[0]["width"],1200)

    def test_precise_image_semantics(self):
        slot={"query":"可信数据空间建设","afterHeading":"可信数据空间落地","purpose":"展示项目实践"}
        bad={"description":"蓝色抽象科技背景","sourceTitle":"通用素材","sourceSnippet":"视觉素材合集","source":"素材站"}
        good={"description":"可信数据空间项目签约现场","sourceTitle":"可信数据空间建设项目启动","sourceSnippet":"企业数据流通与可信交换","source":"机构官网"}
        self.assertFalse(visuals._has_semantic_anchor(bad,slot,"数据要素治理")); self.assertTrue(visuals._has_semantic_anchor(good,slot,"数据要素治理"))


    def test_fast_default_disables_thinking_and_avoids_editorial_call_when_clean(self):
        calls=[]
        def fake(system_prompt,user_prompt,**kwargs):
            calls.append(kwargs.copy())
            return fake_llm(system_prompt,user_prompt,**kwargs)
        with patch.object(deepseek,"available",return_value=True), patch.object(deepseek,"generate_json",side_effect=fake), patch.object(tavily,"available",return_value=False), patch("app.pipeline.resolve_visuals",side_effect=fake_visuals):
            article=generate_article({"query":"数据要素治理","sources":[],"options":{"autoEvidence":False,"imageCount":0,"qualityMode":"standard","length":"短篇 · 1000—1400字"}})
        self.assertEqual(calls[0]["reasoning_effort"],"off")
        self.assertLessEqual(article["generationMeta"]["callCount"],2)

    def test_visual_fallback_is_probeable_and_not_dead(self):
        slot={"query":"可信数据空间建设","afterHeading":"项目落地","purpose":"展示真实项目现场"}
        raw={"url":"https://img.example.org/f.jpg","fallbackUrl":"","description":"可信数据空间建设项目现场","sourceTitle":"可信数据空间建设项目启动","sourceSnippet":"数据空间项目","source":"机构官网","sourceUrl":"https://gov.example.cn/news/1","width":1200,"height":800,"provider":"serper"}
        with patch.object(serper_images,"available",return_value=True), patch.object(serper_images,"search_images",return_value=[raw]), patch.object(visuals,"image_profile",return_value=visuals.ImageProfile(1200,800,1.5,"fp1",True)):
            out, warnings=visuals.resolve_visuals([{**slot,"slotId":"body-1","kind":"body"}],"数据要素治理")
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]["image"]["url"],raw["url"])

    def test_home_feed_never_injects_demo(self):
        with patch("app.home_feed.research",return_value={"results":[],"warnings":["no verified sources"]}):
            data=home_feed(force=True)
        self.assertEqual(data["items"],[]); self.assertFalse(data["demo"])

    def test_visual_job_is_async_and_can_be_polled(self):
        with patch.object(deepseek, "available", return_value=True), patch.object(deepseek, "generate_json", return_value=fake_llm("", "")), patch.object(tavily, "available", return_value=False), patch("app.pipeline._start_visual_job") as starter:
            article = generate_article({"query": "数据要素治理", "sources": [], "options": {"autoEvidence": False, "imageCount": 3}})
        starter.assert_called_once()
        self.assertEqual(article["visualStatus"], "pending")

    def test_custom_image_count_creates_requested_body_slots(self):
        article = {"markdown": "导语\n\n## 第一部分\n\n这是一段足够长的正文，用来验证自定义图片数量不会被小标题数量强行截断。" * 12, "imageQueries": []}
        slots = plan_visual_slots(article, "数据要素治理", max_body=5)
        self.assertEqual(sum(1 for s in slots if s["kind"] == "body"), 5)

    def test_export_never_uses_placeholder_on_missing_image(self):
        from app.exporter import _docx_add_image
        fake_doc = type("D", (), {"add_paragraph": lambda self: (_ for _ in ()).throw(AssertionError("should not add placeholder image"))})()
        with patch("app.exporter.image_bytes_for_document", side_effect=Exception("missing")):
            _docx_add_image(fake_doc, {"url": "https://img.example.org/missing.jpg"}, max_width_inches=6.5)

    def test_source_similarity_rejects_unrelated_page(self):
        v = source_verify.title_similarity("数据要素市场建设", "今天天气预报与城市交通")
        self.assertLess(v, 0.11)

    def test_query_intent_understands_attached_description_and_preserves_topic(self):
        from app.query_intent import understand
        with patch.object(deepseek, "available", return_value=True), patch.object(deepseek, "plan_search_intent", return_value=({
            "intentSummary":"优先近30天中国主流媒体与官方政策，并补充欧美最新进展；排除泛泛数字化新闻",
            "normalizedTopic":"可信数据空间","mustTerms":["可信数据空间"],"anchorTerms":["可信数据空间","数据流通","授权运营"],"relatedTerms":["数据空间"],"excludeTerms":["泛数字化"],
            "domesticNewsQuery":"可信数据空间 中国 主流媒体 最新","globalNewsQuery":"可信数据空间 Europe US global news","policyQuery":"可信数据空间 政策 文件 通知","paperQuery":"可信数据空间 research paper study", "regionPreference":"domestic+global","timeIntent":"latest"
        }, {"apiCalled":True})):
            plan = understand("可信数据空间", "优先近30天中国主流媒体与官方政策，并补充欧美最新进展；不要泛泛的数字化新闻。")
        self.assertTrue(plan["usedModel"])
        self.assertIn("可信数据空间", plan["normalizedTopic"])
        self.assertIn("可信数据空间", plan["anchorTerms"])
        self.assertIn("欧美", plan["intentSummary"])

    def test_umbrella_topic_keeps_concrete_data_governance_news(self):
        from app.pipeline import _query_match_score
        from app.query_intent import local_plan
        intent = local_plan("数据要素治理")
        examples = [
            ("全国统一数据产权登记加快落地", "数据产权登记工作指引推动登记对象、流程和效力全国统一"),
            ("2026数据要素×大赛宁夏分赛决赛落幕", "项目覆盖工业制造、农业、城市治理等真实用数场景"),
            ("上海推广算力券、模型券、语料券", "公共数据开放并降低数据、模型和语料使用成本"),
            ("字节跳动设立AI数据与安全一级部门", "覆盖数据采购、清洗、质量评测与安全合规"),
        ]
        for title, snippet in examples:
            self.assertGreaterEqual(_query_match_score("数据要素治理", {"title": title, "snippet": snippet}, intent=intent), 16, title)
        self.assertLess(_query_match_score("数据要素治理", {"title": "数据中心冷却系统改造", "snippet": "服务器液冷与节能改造"}, intent=intent), 16)

    def test_time_filter_keeps_provider_rows_without_dates(self):
        from app.pipeline import _filter_results_by_date
        rows = [{"title": "国家数据局发布政策解读", "publishedAt": "", "sourceVerified": True}]
        kept = _filter_results_by_date(rows, time_range="week", date_from="", date_to="")
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0].get("dateUnverified"))

    def test_tavily_paper_topic_and_fast_retry_contract(self):
        calls = []
        def fake_request(url, **kwargs):
            payload = dict(kwargs.get("payload") or {})
            calls.append(payload)
            # Force fast search to retry once with basic.
            if payload.get("search_depth") == "fast":
                return {"results": []}
            return {"results": [{"title":"数据治理研究","url":"https://example.org/p","content":"研究数据产权与公共数据治理","score":0.8}]}
        with patch.object(tavily, "available", return_value=True), patch.object(tavily, "request_json", side_effect=fake_request):
            tavily.search("数据治理-fast-retry-contract", mode="fast", topic="news", max_results=2)
            tavily.search_papers("数据治理-paper-contract", max_results=2)
        self.assertTrue(all(c.get("topic") in {"news", "general"} for c in calls))
        self.assertFalse(any(c.get("topic") == "paper" for c in calls))
        basic = next(c for c in calls if c.get("search_depth") == "basic")
        self.assertNotIn("chunks_per_source", basic)

    def test_generic_query_does_not_promote_unrelated_article(self):
        from app.pipeline import _query_match_score
        row={"title":"VIDEO: Europe rPET looking less volatile in June","snippet":"price moves and recycled plastic market commentary"}
        intent={"mustTerms":["数据"],"anchorTerms":["数据要素"],"relatedTerms":[],"excludeTerms":[],"domainContext":"数据要素治理"}
        self.assertEqual(_query_match_score("数据", row, intent=intent), 0)

    def test_verified_meta_description_replaces_provider_noise(self):
        v=source_verify.Verification(True,"https://real.example/a","https://real.example/a","真实新闻","这是页面真实摘要。",200,"ok")
        with patch("app.source_verify.verify_url", return_value=v):
            verified,_=verify_results([{"type":"news","title":"检索标题","url":"https://real.example/a"}],limit=1)
        self.assertEqual(verified[0]["verifiedDescription"],"这是页面真实摘要。")


if __name__=="__main__":
    unittest.main()
