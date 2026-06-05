# app/schemas/alert.py
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EmergencyRequest(BaseModel):
    """Emergency-button press from the patient app. GPS optional (SRS: alert
    still goes through without location — dashboard falls back to address)."""
    gps_lat: Optional[float] = Field(default=None, examples=[4.1527])
    gps_lng: Optional[float] = Field(default=None, examples=[9.2415])
    note: Optional[str] = Field(default=None, examples=["I am bleeding and alone at home"])

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "gps_lat": 4.1527, "gps_lng": 9.2415,
        "note": "I am bleeding and alone at home",
    }]})


class AlertPatientInfo(BaseModel):
    name: str = Field(..., examples=["Maria Nkeng"])
    phone: str = Field(..., examples=["+237679977660"])
    age: Optional[int] = Field(default=None, examples=[29])
    risk_level: Optional[str] = Field(default=None, examples=["high"])
    account_type: str = Field(..., examples=["smartphone"])
    status: str = Field(..., examples=["active"])


class AlertResponse(BaseModel):
    id: UUID = Field(..., examples=["b3e8f1a2-4c5d-4e6f-8a9b-0c1d2e3f4a5b"])
    patient_id: UUID = Field(..., examples=["7c1e9b40-2a3d-4f81-9c6a-1b2c3d4e5f60"])
    hospital_id: UUID = Field(..., examples=["4f996b23-92d3-4587-857b-038903d4253d"])
    source: str = Field(..., examples=["emergency_button"])
    triage_level: Optional[str] = Field(default=None, examples=["high"])
    reason: str = Field(..., examples=["Emergency button pressed"])
    context: Optional[str] = Field(default=None, examples=["Patient: I am bleeding\nBot: ..."])
    gps_lat: Optional[float] = Field(default=None, examples=[4.1527])
    gps_lng: Optional[float] = Field(default=None, examples=[9.2415])
    status: str = Field(..., examples=["new"])
    created_at: datetime = Field(..., examples=["2026-06-05T10:30:00+00:00"])
    acknowledged_at: Optional[datetime] = Field(default=None)
    resolved_at: Optional[datetime] = Field(default=None)
    patient: Optional[AlertPatientInfo] = Field(default=None)

    model_config = ConfigDict(from_attributes=True)


class AlertStatusUpdate(BaseModel):
    """Acknowledge or resolve an alert."""
    status: str = Field(..., examples=["ack"])  # "ack" | "resolved"

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ack"}]})
