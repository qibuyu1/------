from __future__ import annotations

def audit_article(article: dict) -> dict:
    text = str(article.get('content') or article.get('text') or '')
    checks = {
        'has_content': len(text.strip()) > 200,
        'has_source': bool(article.get('sources')),
        'has_visual_plan': bool(article.get('visuals') or article.get('images')),
        'avoid_empty_claim': '据悉' not in text[:200],
    }
    score = round(sum(checks.values()) / len(checks) * 100)
    return {'score': score, 'checks': checks, 'pass': score >= 75}
