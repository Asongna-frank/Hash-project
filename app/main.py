"""Main FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import Base, engine
from app.routers import auth, hospitals, patients, chat, appointments, notifications, tips
from app.models import message  # noqa — ensures table is registered with Base
from app.models import appointment as appointment_model  # noqa — registers appointments table
from app.services.scheduler import scheduler

# Create FastAPI app
app = FastAPI(
    title="HASH Maternal Care API",
    version="0.1.0",
)

# Add CORS middleware (allowing all origins for development)
# TODO: Restrict origins before production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event():
    """Create database tables and start the reminder scheduler on startup."""
    Base.metadata.create_all(bind=engine)
    scheduler.start()


@app.on_event("shutdown")
def shutdown_event():
    """Stop the reminder scheduler cleanly on shutdown."""
    scheduler.shutdown()


@app.get("/")
def root():
    """Root endpoint."""
    return {
        "status": "ok",
        "message": "HASH API is running",
    }


# Include routers
app.include_router(auth.router)
app.include_router(hospitals.router)

app.include_router(patients.router, prefix="/patients", tags=["patients"])
app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(appointments.router, prefix="/appointments", tags=["appointments"])
app.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
app.include_router(tips.router)