"""Audit trail of staff actions (SDS Table 9). Written in the caller's transaction."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..db.models import AuditLog


def record(db: Session, actor_id: int | None, action: str, target: str | None = None,
           **detail: Any) -> None:
    db.add(AuditLog(actor_id=actor_id, action=action, target=target, detail=detail or None))
