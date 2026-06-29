"""
Email sending via Resend API (https://resend.com).
Replaces SMTP so emails work on Railway's free plan (port 587 is blocked).

Required env vars:
  RESEND_API_KEY  — from resend.com dashboard
  RESEND_FROM     — verified sender, e.g. "Tommy <tommy@duding.ai>"
"""
import base64
import os
from typing import Optional

import httpx

_API_URL = "https://api.resend.com/emails"


def _api_key() -> str:
    return os.getenv("RESEND_API_KEY", "").strip()


def _from_addr(override: Optional[str] = None) -> str:
    if override:
        return override
    from_name = os.getenv("FROM_NAME", "Duding.ai")
    from_email = os.getenv("SMTP_USER", "duding@duding.ai")
    return os.getenv("RESEND_FROM", f"{from_name} <{from_email}>")


def send_email(
    to_email: str,
    subject: str,
    body: str,
    from_name: Optional[str] = None,
    from_email: Optional[str] = None,
) -> bool:
    key = _api_key()
    if not key:
        print("[email] RESEND_API_KEY not set — skipping send.")
        return False

    from_addr = _from_addr()
    if from_name and from_email:
        from_addr = f"{from_name} <{from_email}>"
    elif from_name:
        base = os.getenv("SMTP_USER", "duding@duding.ai")
        from_addr = f"{from_name} <{base}>"

    payload = {
        "from": from_addr,
        "to": [to_email],
        "subject": subject,
        "text": body,
    }
    try:
        r = httpx.post(
            _API_URL,
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=15,
        )
        if r.status_code in (200, 201):
            print(f"[email] Sent to {to_email}")
            return True
        print(f"[email] Resend error {r.status_code}: {r.text[:200]}")
        return False
    except Exception as exc:
        print(f"[email] Error sending to {to_email}: {exc}")
        return False


def send_html_email(
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str,
) -> bool:
    key = _api_key()
    if not key:
        print("[email] RESEND_API_KEY not set — skipping send_html_email.")
        return False

    payload = {
        "from": _from_addr(),
        "to": [to_email],
        "subject": subject,
        "text": body_text,
        "html": body_html,
    }
    try:
        r = httpx.post(
            _API_URL,
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=15,
        )
        if r.status_code in (200, 201):
            print(f"[email] HTML email sent to {to_email}")
            return True
        print(f"[email] Resend error {r.status_code}: {r.text[:200]}")
        return False
    except Exception as exc:
        print(f"[email] Error sending HTML email to {to_email}: {exc}")
        return False


def send_email_with_attachment(
    to_email: str,
    subject: str,
    body: str,
    attachment_bytes: bytes,
    filename: str,
) -> bool:
    key = _api_key()
    if not key:
        print("[email] RESEND_API_KEY not set — skipping send_with_attachment.")
        return False

    payload = {
        "from": _from_addr(),
        "to": [to_email],
        "subject": subject,
        "text": body,
        "attachments": [
            {
                "filename": filename,
                "content": base64.b64encode(attachment_bytes).decode("utf-8"),
            }
        ],
    }
    try:
        r = httpx.post(
            _API_URL,
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=20,
        )
        if r.status_code in (200, 201):
            print(f"[email] Sent (with attachment) to {to_email}")
            return True
        print(f"[email] Resend error {r.status_code}: {r.text[:200]}")
        return False
    except Exception as exc:
        print(f"[email] Error sending with attachment to {to_email}: {exc}")
        return False
