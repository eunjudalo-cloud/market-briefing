"""아카이브 index.html 생성 (정적, 외부 리소스 없음).

- 최신 브리핑 HTML(이메일 템플릿 그대로 — 링크·다크모드 유지)을 index.html 로 복사하고
  상단에 지난 날짜로 이동하는 내비게이션 바를 삽입한다.
- 개별 브리핑은 각자 `브리핑_YYYY-MM-DD.html` 로 접근.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from html import escape
from pathlib import Path

log = logging.getLogger("briefing.archive")

_HTML_RE = re.compile(r"^브리핑_(\d{4}-\d{2}-\d{2})\.html$")
_BODY_OPEN_RE = re.compile(r"<body[^>]*>", re.I)
_NAV_MAX = 30

_NAV_STYLE = (
    "font:400 12px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',"
    "'Malgun Gothic',sans-serif;color:#6b7280;background:#f4f5f7;"
    "border-bottom:1px solid #e2e5ea;padding:10px 16px;text-align:center;"
    "word-break:keep-all;"
)


def _entries(output_dir: Path) -> list[tuple[date, Path]]:
    items: list[tuple[date, Path]] = []
    for f in output_dir.glob("브리핑_*.html"):
        m = _HTML_RE.match(f.name)
        if not m:
            continue
        try:
            y, mo, d = (int(x) for x in m.group(1).split("-"))
            items.append((date(y, mo, d), f))
        except ValueError:
            continue
    items.sort(key=lambda t: t[0], reverse=True)
    return items


def _nav_html(entries: list[tuple[date, Path]], current: date) -> str:
    links = []
    for d, f in entries[:_NAV_MAX]:
        label = d.isoformat()
        if d == current:
            links.append(f'<strong style="color:inherit;">{label}</strong>')
        else:
            links.append(f'<a href="{escape(f.name)}" style="color:#1155cc;">{label}</a>')
    body = " · ".join(links)
    return (
        f'<div data-archive-nav style="{_NAV_STYLE}">'
        f'<span style="font-weight:600;color:#14171a;">마켓 브리핑</span> &nbsp; {body}'
        f"</div>"
    )


def build_index(output_dir: Path) -> Path | None:
    entries = _entries(output_dir)
    if not entries:
        log.info("아카이브: 브리핑 HTML 이 없어 index.html 을 만들지 않습니다.")
        return None

    latest_date, latest_path = entries[0]
    try:
        doc = latest_path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("아카이브: 최신 HTML 읽기 실패 %s", exc)
        return None

    nav = _nav_html(entries, latest_date)
    if _BODY_OPEN_RE.search(doc):
        doc = _BODY_OPEN_RE.sub(lambda m: m.group(0) + "\n" + nav, doc, count=1)
    else:
        doc = nav + doc

    out = output_dir / "index.html"
    out.write_text(doc, encoding="utf-8")
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")
    log.info("아카이브 갱신: %s (%d건)", out, len(entries))
    return out
