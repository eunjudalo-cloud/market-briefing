"""이메일 발송 (SMTP STARTTLS).

- 제목: [마켓 브리핑] YYYY-MM-DD
- multipart/alternative: plain-text(Markdown 원문) + HTML
- 원본 .md 첨부
- 발송 실패는 예외를 삼키고 로그만 남긴다 (파일은 이미 저장됨).
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from ..config import Settings
from ..render.renderer import markdown_to_html, render_plain_text

log = logging.getLogger("briefing.mailer")


def _recipients(raw: str) -> list[str]:
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


def send(
    *,
    subject: str,
    markdown_body: str,
    md_path: Path,
    settings: Settings,
    html_body: str | None = None,
    dry_run: bool = True,
) -> bool:
    to_list = _recipients(settings.mail_to)
    sender = settings.mail_from or settings.smtp_user
    plain_body = render_plain_text(markdown_body)
    html_out = html_body or markdown_to_html(markdown_body)

    if dry_run:
        log.info(
            "[DRY-RUN] 메일 미발송. subject=%r from=%s to=%s attach=%s "
            "(plain %dB / html %dB)",
            subject, sender or "(미설정)", to_list or "(미설정)", md_path.name,
            len(plain_body.encode("utf-8")), len(html_out.encode("utf-8")),
        )
        return False

    if not (settings.smtp_host and sender and to_list):
        log.warning("SMTP 설정 부족(host/from/to) — 메일을 보내지 않습니다.")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to_list)
    msg.set_content(plain_body)
    msg.add_alternative(html_out, subtype="html")

    try:
        data = md_path.read_bytes()
        msg.add_attachment(
            data, maintype="text", subtype="markdown", filename=md_path.name
        )
    except OSError as exc:
        log.warning("첨부 파일 읽기 실패(%s) — 첨부 없이 발송", exc)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ctx)
            smtp.ehlo()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_pass)
            smtp.send_message(msg)
        log.info("메일 발송 완료 → %s", ", ".join(to_list))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("메일 발송 실패(%s) — 파일은 output/ 에 저장되어 있습니다.", exc)
        return False
