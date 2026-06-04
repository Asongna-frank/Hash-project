"""Main FastAPI application entry point."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import Base, engine
from app.routers import auth, hospitals, patients, chat, appointments, notifications, tips
from app.routers import personnel as personnel_router
from app.routers import hospital_appointments
from app.models import message  # noqa — ensures table is registered with Base
from app.models import appointment as appointment_model  # noqa — registers appointments table
from app.models import personnel as personnel_model    # noqa — registers personnel table
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


@app.get("/")
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
app.include_router(notifications.router, prefix="/notifications",         tags=["notifications"])
app.include_router(tips.router)
app.include_router(personnel_router.router)
