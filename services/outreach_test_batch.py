"""
services/outreach_test_batch.py — bounded, self-terminating outreach
trial. Built 2026-07-28 at Tommy's request: test real deliverability on
a small named-person-only batch (15-20 contacts) to decide whether to
compress the Aug 4 full-ramp re-enable timeline, without touching
OUTREACH_SENDING_ENABLED, the warmup ramp, or the existing re-enable
checklist task (services/agent_tasks.py::REENABLE_TASK_TITLE).

Distinct pathway, deliberately: starting or stopping a test batch never
writes to OutreachSendingState. It has its own pacing (default one send
per OUTREACH_TEST_BATCH_MIN_GAP_MINUTES) and self-terminates the moment
it hits target OR the bounce-rate circuit breaker
(services/self_healing.py) trips — a trip stops the batch outright
(stopped_reason="circuit_breaker"), it does not merely pause it, since
resuming after a real bounce spike should be a fresh decision.

job_process_followups is untouched and stays gated on
OUTREACH_SENDING_ENABLED alone (outreach_engine.py) — a test batch
never fires follow-ups, only initial sends, so it can't accidentally
wake up a backlog of pre-rebuild follow-ups that predate the rest
period.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from db import SessionLocal
from models.outreach_test_batch import OutreachTestBatch
from services.action_log import log_action

MIN_GAP_MINUTES = int(os.getenv("OUTREACH_TEST_BATCH_MIN_GAP_MINUTES", "90"))


def get_state(db=None) -> Optional[OutreachTestBatch]:
    owns = db is None
    db = db or SessionLocal()
    try:
        return db.query(OutreachTestBatch).filter(OutreachTestBatch.id == 1).first()
    finally:
        if owns:
            db.close()


def status() -> Dict[str, Any]:
    row = get_state()
    if not row or not row.target:
        return {"active": False}
    return {
        "active": row.completed_at is None,
        "target": row.target,
        "sent_count": row.sent_count,
        "started_at": row.started_at,
        "last_sent_at": row.last_sent_at,
        "completed_at": row.completed_at,
        "stopped_reason": row.stopped_reason,
    }


def start_test_batch(target: int) -> Dict[str, Any]:
    if target <= 0:
        return {"started": False, "reason": "target must be positive"}
    db = SessionLocal()
    try:
        row = db.query(OutreachTestBatch).filter(OutreachTestBatch.id == 1).first()
        if row and row.target and not row.completed_at:
            return {
                "started": False, "reason": "a test batch is already active",
                "target": row.target, "sent_count": row.sent_count,
            }
        if not row:
            row = OutreachTestBatch(id=1)
            db.add(row)
        row.target = target
        row.started_at = datetime.now(timezone.utc)
        row.sent_count = 0
        row.last_sent_at = None
        row.completed_at = None
        row.stopped_reason = None
        db.commit()
    finally:
        db.close()

    log_action(
        category="test_batch_started", severity="info", agent="outreach",
        trigger=f"Manual start — controlled outreach test, target={target} contacts.",
        action_taken=(
            f"Test-batch sending pathway active: up to {target} initial sends, "
            f"paced >= {MIN_GAP_MINUTES}min apart, independent of the warmup ramp. "
            "OUTREACH_SENDING_ENABLED and the Aug 4 re-enable gate are untouched. "
            "Bounce-rate circuit breaker still applies and will stop this batch outright if tripped."
        ),
    )
    return {"started": True, "target": target}


def stop_test_batch(reason: str = "manual_stop") -> Dict[str, Any]:
    db = SessionLocal()
    try:
        row = db.query(OutreachTestBatch).filter(OutreachTestBatch.id == 1).first()
        if not row or not row.target or row.completed_at:
            return {"stopped": False, "reason": "no active test batch"}
        row.completed_at = datetime.now(timezone.utc)
        row.stopped_reason = reason
        db.commit()
        count, target = row.sent_count, row.target
    finally:
        db.close()
    log_action(
        category="test_batch_stopped", severity="info", agent="outreach",
        trigger=f"Test batch stopped ({reason}) at {count}/{target} sends.",
        action_taken="Test-batch sending pathway disabled.",
    )
    return {"stopped": True, "sent_count": count, "target": target}


def _stop_internal(reason: str) -> None:
    db = SessionLocal()
    try:
        row = db.query(OutreachTestBatch).filter(OutreachTestBatch.id == 1).first()
        if not row or not row.target or row.completed_at:
            return
        row.completed_at = datetime.now(timezone.utc)
        row.stopped_reason = reason
        db.commit()
        count, target = row.sent_count, row.target
    finally:
        db.close()
    log_action(
        category="test_batch_stopped",
        severity="critical" if reason == "circuit_breaker" else "info",
        agent="outreach",
        trigger=f"Test batch auto-stopped ({reason}) at {count}/{target} sends.",
        action_taken="Test-batch sending pathway disabled — this was NOT a target-reached completion.",
    )


def evaluate_gate() -> bool:
    """Single entry point used by job_send_next_queued: True only if a
    test batch is active, under target, correctly paced, and the
    bounce-rate circuit breaker hasn't tripped. A trip stops the batch
    outright rather than merely blocking this one check."""
    from services.self_healing import is_outreach_auto_paused

    row = get_state()
    if not row or not row.target or row.completed_at:
        return False

    if is_outreach_auto_paused():
        _stop_internal("circuit_breaker")
        return False

    if row.sent_count >= row.target:
        return False

    if row.last_sent_at:
        last = row.last_sent_at if row.last_sent_at.tzinfo else row.last_sent_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - last < timedelta(minutes=MIN_GAP_MINUTES):
            return False

    return True


def record_send() -> None:
    """Call once, right after a real test-batch send succeeds or is
    attempted. Self-terminates the moment target is reached."""
    db = SessionLocal()
    try:
        row = db.query(OutreachTestBatch).filter(OutreachTestBatch.id == 1).first()
        if not row:
            return
        row.sent_count = (row.sent_count or 0) + 1
        row.last_sent_at = datetime.now(timezone.utc)
        reached = row.sent_count >= (row.target or 0)
        if reached:
            row.completed_at = datetime.now(timezone.utc)
            row.stopped_reason = "target_reached"
        db.commit()
        count, target = row.sent_count, row.target
    finally:
        db.close()
    if reached:
        log_action(
            category="test_batch_completed", severity="info", agent="outreach",
            trigger=f"Test batch reached its target ({count}/{target}) sends.",
            action_taken="Test-batch sending pathway auto-disabled (target reached).",
        )
