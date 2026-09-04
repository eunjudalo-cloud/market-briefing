"""메일러: dry-run 미발송 / HTML 변환 / SMTP 설정 부족 시 안전."""

from pathlib import Path

from briefing.config import Settings
from briefing.deliver import mailer
from briefing.render.renderer import markdown_to_html

MD = "# 제목\n\n## 표\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- [연합] 헤드라인 — https://x\n"


def test_markdown_to_html_has_table_and_shell():
    html = markdown_to_html(MD)
    assert "<table>" in html
    assert "<!doctype html>" in html.lower()
    assert "헤드라인" in html


def test_send_dry_run_does_not_send(tmp_path: Path):
    md = tmp_path / "브리핑_2026-09-04.md"
    md.write_text(MD, encoding="utf-8")
    s = Settings(mail_to="a@b.com", mail_from="c@d.com", smtp_host="smtp.x")
    sent = mailer.send(subject="[테스트]", markdown_body=MD, md_path=md, settings=s, dry_run=True)
    assert sent is False


def test_send_missing_config_is_safe(tmp_path: Path):
    md = tmp_path / "b.md"
    md.write_text(MD, encoding="utf-8")
    s = Settings()  # SMTP 미설정
    sent = mailer.send(subject="x", markdown_body=MD, md_path=md, settings=s, dry_run=False)
    assert sent is False
