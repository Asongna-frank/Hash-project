"""Main FastAPI application entry point."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import Base, engine
from app.routers import auth, hospitals, patients, chat, appointments, notifications, tips
from app.routers import personnel as personnel_router
from app.routers import hospital_appointments
from app.routers import hospital_patients
from app.routers import sms as sms_router
from app.routers import alerts as alerts_router
from app.routers import wellness as wellness_router
from app.routers import avatar as avatar_router
from app.routers import hospital_notifications as hospital_notifications_router
from app.routers import hospital_stats as hospital_stats_router
from app.routers import calls as calls_router
from app.models import message  # noqa — ensures table is registered with Base
from app.models import alert as alert_model  # noqa — registers alerts table
from app.models import wellness as wellness_model  # noqa — registers daily_wellness table
from app.models import post_loss_case as post_loss_case_model  # noqa — registers post_loss_cases table
from app.models import kicks as kicks_model  # noqa — registers kick_counts table
from app.models import patient_note as patient_note_model  # noqa — registers patient_notes table
from app.models import appointment as appointment_model  # noqa — registers appointments table
from app.models import personnel as personnel_model    # noqa — registers personnel table
from app.models import audit_log as audit_log_model    # noqa — registers audit_logs table
from app.services.scheduler import scheduler

logger = logging.getLogger(__name__)

app = FastAPI(
    title="HASH Maternal Care API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event():
    """Create all DB tables and start the background scheduler."""
    Base.metadata.create_all(bind=engine)

    # Guard against double-start when uvicorn --reload triggers startup twice
    # within the same process.
    if not scheduler.running:
        scheduler.start()
        logger.info(
            "APScheduler started | jobs: %s",
            [j.id for j in scheduler.get_jobs()],
        )
    else:
        logger.debug("APScheduler already running — skipping second start")


@app.on_event("shutdown")
def shutdown_event():
    """Stop the scheduler cleanly."""
    if scheduler.running:
        scheduler.shutdown()


@app.get(
    "/",
    summary="API health check",
    description="Public liveness probe. Returns a simple ok status when the API is running.",
)
def root():
    return {"status": "ok", "message": "HASH API is running"}


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(hospitals.router)
app.include_router(patients.router,      prefix="/patients",              tags=["patients"])
app.include_router(chat.router,          prefix="/chat",                  tags=["chat"])
app.include_router(appointments.router,  prefix="/appointments",          tags=["appointments"])
app.include_router(hospital_appointments.router,
                   prefix="/hospital/appointments",
                   tags=["hospital-appointments"])
app.include_router(hospital_patients.router,
                   prefix="/hospital/patients",
                   tags=["hospital-patients"])
app.include_router(notifications.router, prefix="/notifications",         tags=["notifications"])
app.include_router(tips.router)
app.include_router(personnel_router.router)
app.include_router(sms_router.router,    prefix="/sms",                   tags=["sms"])
app.include_router(alerts_router.router, prefix="/alerts",                tags=["alerts"])
app.include_router(wellness_router.router, prefix="/patients",              tags=["wellness"])
app.include_router(avatar_router.router, prefix="/patients",               tags=["patients"])
app.include_router(hospital_notifications_router.router,
                   prefix="/hospital/notifications",
                   tags=["hospital-notifications"])
app.include_router(hospital_stats_router.router,
                   prefix="/hospital/stats",
                   tags=["hospital-stats"])
app.include_router(calls_router.router,  prefix="/calls",                 tags=["calls"])
