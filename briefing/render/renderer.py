"""브리핑 렌더링.

- render_markdown:   PRD 7장 Markdown 템플릿
- render_email_html: 이메일용 HTML (template.email.html.j2, 인라인 CSS, 라이트/다크)
- render_plain_text: 이메일 plain-text 파트 (Markdown 정돈본)
- markdown_to_html:  임의 Markdown → 간단 HTML (아카이브 미리보기용)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..models import BriefingOutput, BriefingPayload, InvestorType, Market

log = logging.getLogger("briefing.render")

_TEMPLATE_DIR = Path(__file__).resolve().parent


def _fmt_num(v, nd: int = 2) -> str:
    return f"{v:,.{nd}f}" if v is not None else "-"


def _fmt_signed(v, nd: int = 2) -> str:
    return f"{v:+,.{nd}f}" if v is not None else "-"

_HTML_SHELL = """\
<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Malgun Gothic",sans-serif;
   line-height:1.6;color:#1a1a1a;max-width:680px;margin:0 auto;padding:16px}}
 h1{{font-size:20px}} h2{{font-size:16px;margin-top:24px;border-bottom:1px solid #ddd;padding-bottom:4px}}
 h3{{font-size:14px;margin-bottom:4px}}
 table{{border-collapse:collapse;width:100%;margin:8px 0}}
 th,td{{border:1px solid #ddd;padding:6px 8px;text-align:left;font-size:13px}}
 th{{background:#f5f5f5}}
 a{{color:#1155cc}}
 hr{{border:none;border-top:1px solid #ddd;margin:20px 0}}
 blockquote{{color:#666;border-left:3px solid #ccc;margin:8px 0;padding-left:10px}}
</style></head><body>
{body}
</body></html>"""


def markdown_to_html(md_text: str) -> str:
    try:
        import markdown as _md

        body = _md.markdown(md_text, extensions=["tables", "sane_lists", "nl2br"])
    except Exception:  # noqa: BLE001
        from html import escape

        body = "<pre>" + escape(md_text) + "</pre>"
    return _HTML_SHELL.format(body=body)


def _md_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=False,  # Markdown 출력은 HTML 이스케이프하지 않음
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def _html_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=True,  # 뉴스 제목 등 변수는 반드시 이스케이프
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["num"] = _fmt_num
    env.filters["signed"] = _fmt_signed
    return env


def render_markdown(payload: BriefingPayload, output: BriefingOutput) -> str:
    tmpl = _md_env().get_template("template.md.j2")
    return tmpl.render(
        p=payload,
        o=output,
        investors=list(InvestorType),
        markets=list(Market),
    )


def render_email_html(payload: BriefingPayload, output: BriefingOutput) -> str:
    tmpl = _html_env().get_template("template.email.html.j2")
    return tmpl.render(
        p=payload,
        o=output,
        investors=list(InvestorType),
        markets=list(Market),
    )


# 표 구분선(| --- | :--- |)만 매칭. 본문 구분선 '---' 은 건드리지 않음
_MD_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]*-{2,}[\s:|-]*\|?\s*$")


def render_plain_text(md_text: str) -> str:
    """Markdown 을 메일 plain-text 파트용으로 가볍게 정돈."""
    out: list[str] = []
    for line in md_text.splitlines():
        if _MD_TABLE_SEP_RE.match(line):
            continue
        line = line.replace("|", " ").rstrip() if line.lstrip().startswith("|") else line
        line = re.sub(r"^>\s?", "※ ", line)
        out.append(line)
    return "\n".join(out)


def write_markdown(text: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    log.info("브리핑 파일 생성: %s (%d bytes)", path, len(text.encode("utf-8")))
    return path
