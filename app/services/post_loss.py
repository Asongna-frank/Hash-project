# app/services/post_loss.py
"""
Post-loss track activation — M9.

SRS hard rule: "The post-loss track only begins when a CLINICIAN marks the
pregnancy as Loss on the dashboard. The system never infers a loss from chat
messages alone." This service is therefore called ONLY from the hospital
outcome endpoint — chat loss-detection raises an alert for clinician review
but never calls this.

Activation effects (SRS 2.7):
  - patient.status -> post_loss (stops routine tips/check-ins/EDD content;
    schedulers already filter on status)
  - pregnancy outcome -> loss, with loss_date and GA at loss
  - risk escalated to high (physical recovery watch) with an audit trail
  - Dr Elvira's approved opener sent on the patient's own channel
    (app outbound + push for smartphone, SMS for choronko)
"""

import logging
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.core.risk_config import RUBRIC_VERSION
from app.models.patient import Patient
from app.models.pregnancy import Pregnancy
from app.models.risk_assessment import RiskAssessment
from app.services import content_store
from app.services.message_store import save_outbound
from app.services.push_service import push_service
from app.services.sms_service import sms_service

logger = logging.getLogger(__name__)


def _patient_lang(patient: Patient) -> str:
    return (getattr(patient, "language", None) or "en").lower()


def activate_post_loss(
    db: Session,
    patient: Patient,
    pregnancy: Pregnancy | None,
    *,
    activated_by: str,
    loss_date: date | None = None,
    ga_at_loss: int | None = None,
) -> str:
    """
    Switch the patient to the post-loss track. Returns the opener text sent.
    Stages all changes and COMMITS (the opener must only go out on a durable
    state change).
    """
    loss_date = loss_date or date.today()
    if ga_at_loss is None and patient.lmp:
        ga_at_loss = max((loss_date - patient.lmp).days // 7, 0)

    patient.status = "post_loss"
    patient.pending_loss_confirmation = False

    if pregnancy:
        pregnancy.outcome = "loss"
        pregnancy.loss_date = loss_date
        pregnancy.ga_at_loss = ga_at_loss
        pregnancy.routine_paused = True

    # Physical-recovery watch: post-loss patients are checked on closely.
    if patient.risk_level != "high":
        patient.risk_level = "high"
        patient.risk_level_set_at = datetime.now(timezone.utc)
        patient.risk_level_set_by = activated_by
        db.add(RiskAssessment(
            patient_id=patient.id,
            computed_by=activated_by,
            inputs={
                "reason": "Escalation on clinician-confirmed pregnancy loss",
                "loss_confirmed": True,
            },
            rubric_version=RUBRIC_VERSION,
            result_level="high",
            score=None,
        ))

    # Pre-approved opener, stored per language — never live-translated.
    opener = content_store.get_content("post_loss_opening", _patient_lang(patient))
    channel = "sms" if patient.account_type == "choronko" else "app"
    db.add(save_outbound(
        patient.id, opener, channel=channel,
        message_type="crisis", source_lang=_patient_lang(patient),
    ))
    db.commit()

    # Delivery (best-effort — the state change above is already durable).
    if channel == "sms":
        result = sms_service.send_sms(patient.phone, opener)
        if not result.ok:
            logger.error("Post-loss opener SMS failed | patient=%s | %s",
                         patient.id, result.error)
    else:
        try:
            push_service.send_push(str(patient.id), "HASH", opener)
        except Exception:  # noqa: BLE001
            logger.exception("Post-loss opener push failed | patient=%s", patient.id)

    logger.info("Post-loss track activated | patient=%s | by=%s | ga_at_loss=%s",
                patient.id, activated_by, ga_at_loss)
    return opener


def mark_live_birth(db: Session, patient: Patient, pregnancy: Pregnancy | None) -> None:
    """Record a live birth: stop routine pregnancy messaging, keep the record."""
    patient.status = "delivered"
    if pregnancy:
        pregnancy.outcome = "live_birth"
        pregnancy.routine_paused = True
    db.commit()
    logger.info("Live birth recorded | patient=%s", patient.id)
