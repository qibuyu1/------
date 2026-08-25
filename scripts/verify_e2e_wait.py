# -*- coding: utf-8 -*-
"""修正版端到端验证：生成后等待异步视觉任务完成，再导出含图 docx/pdf。"""
import argparse, base64, io, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import pipeline, exporter
from app.article_store import article_store as store   # instance

def report_visual(a):
    vis = a.get('visuals') or []
    print("VISUALS:", len(vis))
    for s in vis[:12]:
        img = s.get('image') or {}
        uri = str(img.get('dataUri') or img.get('url') or '')
        blank = None
        if uri.startswith('data:image'):
            try:
                from PIL import Image
                b = base64.b64decode(uri.split(',',1)[1])
                im = Image.open(io.BytesIO(b)).convert('RGB').resize((64,36))
                px = list(im.getdata())
                nonwhite = sum(1 for p in px if not (p[0]>245 and p[1]>245 and p[2]>245))/len(px)
                ncolors = len(set(px))
                blank = (nonwhite < 0.02) or (nonwhite < 0.05 and ncolors <= 3)
            except Exception:
                blank = None
        print(f"  [{s.get('kind')}] matchedBy={s.get('matchedBy')} provider={img.get('provider')} blank={blank} len={len(uri)} url={str(img.get('url'))[:48]}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--query', default='公共数据授权运营与可信数据空间：数据要素如何落地')
    ap.add_argument('--angle', default='把公共数据授权运营、企业数据资产入表与可信数据空间放到一条清晰逻辑线上，讲清为什么数据要素越来越重要')
    ap.add_argument('--strategy', default='smart')
    ap.add_argument('--count', type=int, default=5)
    ap.add_argument('--timeout', type=int, default=200)
    a = ap.parse_args()
    payload = { 'query': a.query, 'description': a.angle, 'options': {
        'style':'行业观察','audience':'产业与政策关注者','length':'中篇 · 1800—2400字','tone':'理性、清晰',
        'autoEvidence':True,'citations':False,'imageStrategy':a.strategy,'bodyImageCount':a.count,'imagePreference':'智能混合' } }
    print('================= 单次真实生成（等待视觉完成） =================')
    t0 = time.time()
    article = pipeline.generate_article(payload)
    aid = str(article.get('articleId') or '')
    print('生成完成 %.1fs, articleId=%s' % (time.time()-t0, aid))
    print('视觉状态轮询中（最多 %ds）...' % a.timeout)
    final = article; rec = None
    deadline = time.time() + a.timeout
    while time.time() < deadline:
        rec = store.get(aid)
        if rec:
            cur = rec.get('article') or {}
            st = cur.get('visualStatus'); rep = cur.get('visualReport') or {}
            placed = (rep.get('placed') or 0) + (rep.get('coverPlaced') or 0)
            print('  ... status=%s placed=%d (%.0fs)' % (st, placed, time.time()-t0))
            if st == 'ready' and placed > 0:
                final = cur; break
            if st == 'error':
                print('  视觉任务出错:', cur.get('visualError')); final = cur; break
        time.sleep(2)
    else:
        if rec: final = rec.get('article') or final
        print('  超时，按当前状态导出')

    print('title:', str(final.get('recommendedTitle') or ''))
    print('visualReport:', final.get('visualReport'))
    report_visual(final)
    record = {'article': final, 'sources': (final.get('sourceList') or payload.get('sources') or []), 'query': a.query}
    outdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'exports')
    os.makedirs(outdir, exist_ok=True)
    for fmt in ('docx','pdf'):
        try:
            data, fname, _ = exporter.export_article(record, fmt)
            p = os.path.join(outdir, fname)
            open(p,'wb').write(data)
            print('EXPORT %s: %s (%d bytes)' % (fmt, p, len(data)))
        except Exception as e:
            print('EXPORT %s FAILED: %r' % (fmt, e))

if __name__ == '__main__':
    main()
