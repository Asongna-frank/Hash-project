"""
Audit-logging helper.

Every state-changing access to protected data goes through write_audit so the
SRS "every PHI access and change is logged" rule is satisfied in one place.

write_audit only stages the row (db.add) — it does NOT commit. The calling
endpoint commits it in the SAME transaction as the change it describes, so an
audit row can never exist for a change that was rolled back, and vice versa.
"""

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


def write_audit(
    db: Session,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str,
    details: dict | None = None,
) -> AuditLog:
    """Stage an audit row (caller commits). Returns the row for convenience."""
    row = AuditLog(
        actor_type=actor_type,
        actor_id=str(actor_id),
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        details=details,
    )
    db.add(row)
    return row
