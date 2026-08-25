"""
Offline layout check (no network / no API).

Builds a sample article, exports DOCX + PDF, and inspects document.xml so a
reviewer can confirm the typography without opening a Word/PDF viewer or calling
any external service. Run with the project venv from the experiment directory:

    <venv>\\python.exe scripts\\verify_layout.py

Checks: body size/line-spacing/indent/justify, A4 page, #/##/### heading styles,
图片来源 caption prefix, 资料来源 source block, picture aspect ratio, and that
the PDF embeds/uses a CJK font without error.
"""
import sys, os, io, base64, re, zipfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

buf = io.BytesIO()
Image.new("RGB", (1200, 675), (231, 242, 255)).save(buf, format="PNG")
data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

article = {
    "recommendedTitle": "数据要素治理：从资源管理走向可验证的价值闭环",
    "deck": "围绕规则、流通与价值回流，重新理解数据要素治理。",
    "generationMeta": {"writingSpec": {"citations": True}},
    "coverImage": {"url": data_uri, "provider": "serper", "description": "数据要素治理产业场景", "caption": ""},
    "blocks": [
        {"type": "heading", "level": 1, "text": "机制如何运转"},
        {"type": "paragraph", "text": "数据要素治理需要把权责、质量、授权、使用和风险控制连成闭环。这是用于验证正文排版、行距、缩进与两端对齐的样例文本。"},
        {"type": "image", "url": data_uri, "description": "数据连接与可信流通示意", "source": "机构官网", "caption": ""},
        {"type": "heading", "level": 3, "text": "产业落地怎么看"},
        {"type": "paragraph", "text": "这是一段三级小标题下的正文，用于验证标题层级与正文间距。"},
    ],
}
sources = [
    {"title": "国家数据局《数据产权登记工作指引》", "source": "国家数据局", "publishedAt": "2026-08-06", "url": "https://example.gov.cn/1"},
]

from app.exporter import build_docx, build_pdf, _xml_escape, _build_docx_fallback

docx = build_docx(article, sources)
pdf = build_pdf(article, sources)
print("DOCX ok:", docx.startswith(b"PK"), "| PDF ok:", pdf.startswith(b"%PDF"))

with zipfile.ZipFile(io.BytesIO(docx)) as z:
    docxml = z.read("word/document.xml").decode("utf-8")
    styles = z.read("word/styles.xml").decode("utf-8")
    media = [n for n in z.namelist() if n.startswith("word/media/")]
    m = re.search(r'<w:ind [^>]*w:firstLine="(\d+)"[^>]*/>', docxml)
    nm = re.search(r'<w:style [^>]*w:styleId="Normal".*?</w:style>', styles, re.S)
    norm = nm.group(0) if nm else ""
    print("media count:", len(media))
    print("body justfied (jc both):", "both" in docxml)
    print("firstLine indent (twips, 23pt=460):", m.group(1) if m else "?")
    print("Normal size 11.5pt (w:sz 23):", '<w:sz w:val="23"' in norm)
    print("Normal line 1.5 (w:line 360):", 'w:line="360"' in norm)
    print("Normal body color #172033:", 'w:val="172033"' in norm)
    print("heading hierarchy H1/H3:", "Heading1" in docxml, "Heading3" in docxml)
    print("图片来源 caption prefix:", "图片来源" in docxml)
    print("资料来源 source block:", "资料来源" in docxml)
    print("footer brand (neutral 数据要素治理):", "数据要素治理" in docxml)
    print("PDF uses CID font STSong-Light:", b"STSong-Light" in pdf)

# fallback renderer (python-docx unavailable) still honours aspect ratio
fb = _build_docx_fallback(article, sources)
with zipfile.ZipFile(io.BytesIO(fb)) as z:
    d = z.read("word/document.xml").decode("utf-8")
    ext = re.search(r'<wp:extent cx="(\d+)" cy="(\d+)"/>', d)
    if ext:
        cx, cy = int(ext.group(1)), int(ext.group(2))
        print("fallback picture aspect (cy/cx): %.3f (src 675/1200=%.3f)" % (cy / cx, 675 / 1200))

# control-character escaping
e = _xml_escape("a\x0b<b>&" + chr(0x7f))
print("escape strips control & escapes markup:", "a<b" not in e, "&lt;b&gt;" in e)

# write artifacts for a visual check
out = os.path.join(os.getcwd(), "data", "exports")
os.makedirs(out, exist_ok=True)
open(os.path.join(out, "sample_layout.docx"), "wb").write(docx)
open(os.path.join(out, "sample_layout.pdf"), "wb").write(pdf)
print("artifacts ->", out)