"""
services/action_log.py — writes to the unified self-healing audit trail.

Every automated action taken by services/self_healing.py goes through
log_action() so there is exactly one table to read after being away:
what tripped, what the system did about it, and when.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from db import SessionLocal
from models.action_log import ActionLog


def log_action(
    category: str,
    trigger: str,
    action_taken: str,
    severity: str = "info",
    agent: Optional[str] = None,
    job_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    db = SessionLocal()
    try:
        row = ActionLog(
            category=category, severity=severity, agent=agent, job_id=job_id,
            trigger=trigger, action_taken=action_taken, details=details,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        print(f"[action_log] {severity.upper()} {category} ({job_id or agent or '-'}): {trigger}")
        return row.id
    except Exception as exc:
        db.rollback()
        print(f"[action_log] failed to write (non-fatal): {exc}")
        return None
    finally:
        db.close()


def count_actions_since(category: str, since: datetime, job_id: Optional[str] = None, agent: Optional[str] = None) -> int:
    db = SessionLocal()
    try:
        q = db.query(ActionLog).filter(ActionLog.category == category, ActionLog.occurred_at >= since)
        if job_id is not None:
            q = q.filter(ActionLog.job_id == job_id)
        if agent is not None:
            q = q.filter(ActionLog.agent == agent)
        return q.count()
    finally:
        db.close()


def get_recent_actions(limit: int = 100) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = db.query(ActionLog).order_by(ActionLog.occurred_at.desc()).limit(limit).all()
        return [
            {
                "id": r.id, "category": r.category, "severity": r.severity,
                "agent": r.agent, "job_id": r.job_id, "trigger": r.trigger,
                "action_taken": r.action_taken, "details": r.details,
                "occurred_at": r.occurred_at,
            }
            for r in rows
        ]
    finally:
        db.close()
