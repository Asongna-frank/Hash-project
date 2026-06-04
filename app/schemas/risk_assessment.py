# app/schemas/risk_assessment.py
from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class RiskAssessmentResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [{
                "id": "c3d4e5f6-a7b8-4c9d-0e1f-2a3b4c5d6e7f",
                "patient_id": "7c1e9b40-2a3d-4f81-9c6a-1b2c3d4e5f60",
                "computed_at": "2026-06-04T10:30:00+01:00",
                "computed_by": "system",
                "inputs": {"age": 29, "previous_loss_count": 1, "has_hypertension": True},
                "rubric_version": "v2.0",
                "result_level": "medium",
                "score": 5,
            }]
        },
    )

    id: UUID = Field(..., examples=["c3d4e5f6-a7b8-4c9d-0e1f-2a3b4c5d6e7f"])
    patient_id: UUID = Field(..., examples=["7c1e9b40-2a3d-4f81-9c6a-1b2c3d4e5f60"])
    computed_at: datetime = Field(..., examples=["2026-06-04T10:30:00+01:00"])
    computed_by: str = Field(..., examples=["system"])  # "system" | clinician UUID
    inputs: dict = Field(..., examples=[{"age": 29, "previous_loss_count": 1, "has_hypertension": True}])
    rubric_version: str = Field(..., examples=["v2.0"])
    result_level: str = Field(..., examples=["medium"])
    score: Optional[int] = Field(default=None, examples=[5])
