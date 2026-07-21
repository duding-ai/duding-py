"""
services/email_verification.py — Hard verification gate + confidence scoring

Root-cause fix for the 22.6% outreach bounce rate: the pipeline's only
pre-send check was an MX-record lookup (does the domain have a mail
server at all), which cannot tell "domain has mail" from "this
specific mailbox exists." verify_email_deliverable() adds a real
SMTP-level check (RCPT TO probe, no message actually sent) as the
missing signal.

Fails CLOSED by design, matching the outreach kill-switch pattern:
any timeout, connection error, blocked port, or ambiguous response
(catch-all domain) returns verified=False. There is no code path
where an error or an inconclusive result becomes a pass.

NOTE ON FEASIBILITY: outbound SMTP (port 25) is blocked by many cloud
hosts, including some Railway environments. This module cannot assert
in advance whether Railway's network allows it — see the README
"Contact Discovery Rebuild" section for the real production log
evidence of what actually happened once this shipped. If port 25 is
blocked, every verification attempt fails closed (verified=False),
which still correctly blocks sending — it just means every address
needs a different verification path (a paid SMTP-check API) to ever
pass, which is a real limitation to resolve with Tommy, not something
to route around here.
"""
from __future__ import annotations

import smtplib
import socket
import uuid
from typing import Any, Dict, Optional

import dns.resolver

# Role-based local parts — same list the outreach engine already uses
# to classify direct vs. generic (see app.py::_GENERIC_LOCAL), kept
# here as a small independent copy since pattern-risk scoring is a
# distinct concern from email-quality classification.
_ROLE_LOCALPARTS = frozenset({
    "info", "contact", "hello", "hi", "support", "sales", "team",
    "office", "mail", "noreply", "no-reply", "enquiries", "enquiry",
    "billing", "accounts", "staff", "webmaster", "help", "service",
    "services", "general", "company", "business", "care", "news",
    "media", "pr", "marketing", "reservations", "feedback", "jobs",
    "careers", "hr", "legal", "privacy", "security", "customerservice",
    "customer", "customercare", "cs", "inquiry", "inquiries",
})


def _get_mx_host(domain: str) -> Optional[str]:
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=8)
        best = min(answers, key=lambda r: r.preference)
        return str(best.exchange).rstrip(".")
    except Exception:
        return None


def verify_email_deliverable(email: str, timeout: int = 10) -> Dict[str, Any]:
    """
    Real mailbox-existence check via SMTP RCPT TO — no message is
    actually sent (the SMTP session is aborted after RCPT, before DATA).
    Returns {"verified": bool, "reason": str, "method": str}.

    verified=True requires ALL of:
      - the domain has an MX record
      - the SMTP server is reachable and speaks the protocol
      - RCPT TO for the real address gets a clean 250
      - RCPT TO for a random nonexistent address at the SAME domain
        gets REJECTED (proves the server isn't a catch-all that
        accepts everything, which would make the first check meaningless)

    Anything else — no MX, connection refused, timeout, non-250
    response, or a catch-all domain — returns verified=False. Fails
    closed, never open, on ambiguity.
    """
    if not email or "@" not in email:
        return {"verified": False, "reason": "invalid_syntax", "method": "syntax"}

    domain = email.rsplit("@", 1)[-1].strip().lower()
    if not domain:
        return {"verified": False, "reason": "invalid_syntax", "method": "syntax"}

    mx_host = _get_mx_host(domain)
    if not mx_host:
        return {"verified": False, "reason": "no_mx_record", "method": "mx"}

    probe_from = "verify-probe@duding.ai"
    fake_address = f"nonexistent-probe-{uuid.uuid4().hex[:12]}@{domain}"

    smtp = None
    try:
        smtp = smtplib.SMTP(timeout=timeout)
        smtp.connect(mx_host, 25)
        smtp.helo("duding.ai")
        smtp.mail(probe_from)

        code_real, _ = smtp.rcpt(email)
        code_fake, _ = smtp.rcpt(fake_address)
    except (socket.timeout, socket.gaierror, ConnectionRefusedError, OSError) as exc:
        return {"verified": False, "reason": f"smtp_unreachable: {exc}", "method": "smtp"}
    except smtplib.SMTPException as exc:
        return {"verified": False, "reason": f"smtp_error: {exc}", "method": "smtp"}
    except Exception as exc:
        return {"verified": False, "reason": f"unexpected_error: {exc}", "method": "smtp"}
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass

    if code_fake == 250:
        return {"verified": False, "reason": "catch_all_domain", "method": "smtp"}
    if code_real == 250:
        return {"verified": True, "reason": "smtp_accept", "method": "smtp"}
    return {"verified": False, "reason": f"smtp_rejected_{code_real}", "method": "smtp"}


def score_confidence(email: str, email_quality: Optional[str], verification: Dict[str, Any]) -> Dict[str, Any]:
    """
    source quality + verification result + pattern risk -> 0-100 score
    -> high/medium/low/none tier. Persisted per-prospect so the sender
    can enforce a minimum tier (see OUTREACH_MIN_CONFIDENCE_TIER).

    Targeting rebuild (2026-07-22): generic role addresses are
    structurally excluded, full stop — 95.5% of the bounce autopsy was
    exactly this category, and no verification result changes that
    conclusion. tier="none" here is a hard floor the job layer treats
    as "never queue this," not just a low score to weigh against
    other signals.
    """
    if email_quality == "generic":
        return {
            "score": 0, "tier": "none",
            "reasons": ["excluded: generic role address (info@/support@/etc.) — "
                        "structurally excluded from cold outreach per the 2026-07-22 targeting rebuild"],
        }

    score = 0
    reasons = []

    if email_quality == "direct":
        score += 40
        reasons.append("+40 source: named-person address")
    else:
        reasons.append("+0 source: no quality signal recorded")

    if verification.get("verified"):
        score += 40
        reasons.append("+40 verification: SMTP accept")
    elif verification.get("reason") == "catch_all_domain":
        score += 10
        reasons.append("+10 verification: catch-all domain (inconclusive, not a pass)")
    else:
        score -= 30
        reasons.append(f"-30 verification: {verification.get('reason', 'failed')}")

    local = email.split("@")[0].lower() if email and "@" in email else ""
    if local in _ROLE_LOCALPARTS:
        score -= 10
        reasons.append("-10 pattern: role-based local part (info@/support@/etc.)")

    score = max(0, min(100, score))
    if score >= 70:
        tier = "high"
    elif score >= 40:
        tier = "medium"
    else:
        tier = "low"

    return {"score": score, "tier": tier, "reasons": reasons}
