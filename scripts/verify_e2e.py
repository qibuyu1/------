# -*- coding: utf-8 -*-
"""t7 端到端验证脚本（仅运行一次，用于观察配图与排版改进效果）。

用法（在 数据要素实验 目录，用原 venv 运行）：
  <venv>\\python.exe scripts\\verify_e2e.py --query "..." [--strategy smart|real_first|...] [--count 4]

说明：这是唯一的真实 API 验证运行。会调用 DeepSeek/Tavily/Serper 完成一次
"检索->写作->配图->导出"；随后检查配图分布、是否空白坏图，并导出 docx/pdf。
"""
import argparse, json, os, sys, io, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import pipeline, exporter  # noqa: E402

def report_visual(article):
    """找到文章里的视觉 slot 并逐条报告。"""
    keys = list(article.keys())
    print("ARTICLE TOP-LEVEL KEYS:", keys)
    visuals = article.get("visuals") or article.get("bodyVisuals") or []
    if not visuals:
        # try nested
        for k in ("visualSpec", "generationMeta"):
            v = article.get(k)
            if isinstance(v, dict):
                visuals = visuals or v.get("slots") or v.get("visuals") or []
                if visuals:
                    break
    print("VISUAL SLOTS:", len(visuals))
    blank_guess = []
    for s in visuals[:10]:
        img = s.get("image") or {}
        url = str(img.get("url") or "")
        uri = str(img.get("dataUri") or url or "")
        kind = s.get("kind"); matched = s.get("matchedBy"); prov = img.get("provider")
        is_blank = False
        if uri.startswith("data:image"):
            try:
                import base64
                from PIL import Image
                b = base64.b64decode(uri.split(",",1)[1])
                im = Image.open(io.BytesIO(b)).convert("RGB").resize((64,36))
                px = list(im.getdata())
                nonwhite = sum(1 for p in px if not (p[0]>245 and p[1]>245 and p[2]>245))/len(px)
                ncolors = len(set(px))
                is_blank = nonwhite < 0.04 or ncolors < 8
            except Exception as e:
                is_blank = None
        print(f"  [{kind}] matchedBy={matched} provider={prov} blank={is_blank} len={len(uri)} url={url[:60]}")
        blank_guess.append(is_blank)
    return visuals

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default="数据要素治理：这周发生的几件事")
    ap.add_argument("--angle", default="把公共数据授权运营、企业数据资产入表与可信数据空间这三件事放到一条清晰的逻辑线上，讲清为什么数据要素会越来越重要")
    ap.add_argument("--strategy", default="smart")
    ap.add_argument("--count", type=int, default=4)
    args = ap.parse_args()

    payload = {
        "query": args.query,
        "description": args.angle,
        "options": {
            "style": "行业观察", "audience": "产业与政策关注者", "length": "中篇 · 1800—2400字",
            "tone": "理性、清晰", "autoEvidence": True, "citations": False,
            "imageStrategy": args.strategy, "bodyImageCount": args.count, "imagePreference": "智能混合",
        },
    }
    print("========== 开始一次真实生成（唯一 API 验证运行） ==========")
    t0 = time.time()
    article = pipeline.generate_article(payload)
    print("生成完成, 用时 %.1fs" % (time.time() - t0))
    print("article keys:", list(article.keys())[:30])
    print("title:", str(article.get("recommendedTitle") or ""))
    print("chars:", len(article.get("markdown") or article.get("content") or ""))

    report_visual(article)

    # export
    record = {"article": article, "sources": (article.get("sources") or payload.get("sources") or []), "query": args.query}
    outdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "exports")
    os.makedirs(outdir, exist_ok=True)
    for fmt in ("docx", "pdf"):
        try:
            data, fname, mime = exporter.export_article(record, fmt)
            p = os.path.join(outdir, fname)
            with open(p, "wb") as f:
                f.write(data)
            print(f"EXPORT {fmt}: {p} ({len(data)} bytes)")
        except Exception as e:
            print(f"EXPORT {fmt} FAILED: {e!r}")

if __name__ == "__main__":
    main()
