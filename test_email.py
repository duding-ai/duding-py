# test_email.py — minimal isolated SMTP check
import os, smtplib
from email.message import EmailMessage
from dotenv import load_dotenv
load_dotenv(override=True)

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASSWORD")   # matches .env key
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "1") in ("1", "true", "True")


def send_test(to_addr: str):
    if not all([SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS]):
        raise SystemExit("Missing SMTP env vars. Check SMTP_HOST/PORT/USER/SMTP_PASSWORD.")

    msg = EmailMessage()
    msg["From"] = f"Duding Notifications <{SMTP_USER}>"
    msg["To"] = to_addr
    msg["Subject"] = "Duding SMTP Test"
    msg.set_content("This is a simple plaintext test from test_email.py.")

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
        if SMTP_USE_TLS:
            s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)


if __name__ == "__main__":
    to = os.getenv("NOTIFY_TO") or input("Send test to email: ").strip()
    send_test(to)
    print(f"Sent test email to: {to}")
