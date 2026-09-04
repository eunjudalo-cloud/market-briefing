"""브리핑 아카이브 index.html 생성 (정적, 외부 리소스 없음).

- output/브리핑_YYYY-MM-DD.md 목록을 최신순으로 링크
- 최신 브리핑은 HTML 로 변환해 미리보기로 삽입
"""

from __future__ import annotations

import logging
import re
from datetime import date
from html import escape
from pathlib import Path

from .renderer import markdown_to_html

log = logging.getLogger("briefing.archive")

_NAME_RE = re.compile(r"^브리핑_(\d{4}-\d{2}-\d{2})\.md$")

_PAGE = """\
<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>마켓 브리핑 아카이브</title>
<style>
 body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Malgun Gothic",sans-serif;
  line-height:1.6;color:#1a1a1a;background:#f4f5f7;margin:0;padding:24px;}}
 .wrap{{max-width:760px;margin:0 auto;}}
 h1{{font-size:20px;margin:0 0 16px;}}
 ul{{padding-left:18px;}} li{{margin:3px 0;}}
 a{{color:#1155cc;}}
 .preview{{background:#fff;border:1px solid #e2e5ea;border-radius:8px;padding:8px 20px;margin-top:20px;}}
 @media (prefers-color-scheme:dark){{
   body{{background:#0f1115;color:#e7e9ec;}} a{{color:#79a9ff;}}
   .preview{{background:#161922;border-color:#2b3140;}}
 }}
</style></head><body><div class="wrap">
<h1>마켓 브리핑 아카이브</h1>
{list_html}
{preview_html}
</div></body></html>
"""


def _entries(output_dir: Path) -> list[tuple[date, Path]]:
    items: list[tuple[date, Path]] = []
    for f in output_dir.glob("브리핑_*.md"):
        m = _NAME_RE.match(f.name)
        if not m:
            continue
        y, mo, d = (int(x) for x in m.group(1).split("-"))
        items.append((date(y, mo, d), f))
    items.sort(key=lambda t: t[0], reverse=True)
    return items


def build_index(output_dir: Path) -> Path | None:
    entries = _entries(output_dir)
    if not entries:
        log.info("아카이브: 브리핑 파일이 없어 index.html 을 만들지 않습니다.")
        return None

    lis = "\n".join(
        f'<li><a href="{escape(f.name)}">{d.isoformat()}</a></li>' for d, f in entries
    )
    list_html = f"<ul>\n{lis}\n</ul>"

    latest_date, latest_path = entries[0]
    try:
        preview_body = markdown_to_html(latest_path.read_text(encoding="utf-8"))
        # markdown_to_html 은 완전한 문서를 반환 → body 안쪽만 추출
        inner = re.search(r"<body>(.*)</body>", preview_body, re.S)
        preview_inner = inner.group(1) if inner else preview_body
        preview_html = (
            f'<div class="preview"><p style="color:#6b7280;font-size:13px;">'
            f"최신 · {latest_date.isoformat()}</p>{preview_inner}</div>"
        )
    except OSError:
        preview_html = ""

    out = output_dir / "index.html"
    out.write_text(_PAGE.format(list_html=list_html, preview_html=preview_html),
                   encoding="utf-8")
    # GitHub Pages 가 Jekyll 처리를 건너뛰도록
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")
    log.info("아카이브 갱신: %s (%d건)", out, len(entries))
    return out
