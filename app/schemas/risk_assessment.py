# app/schemas/risk_assessment.py
from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class RiskAssessmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    computed_at: datetime
    computed_by: str
    inputs: dict
    rubric_version: str
    result_level: str
    score: Optional[int]