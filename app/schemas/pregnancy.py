# app/schemas/pregnancy.py
from datetime import date, datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class PregnancyResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [{
                "id": "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e",
                "patient_id": "7c1e9b40-2a3d-4f81-9c6a-1b2c3d4e5f60",
                "lmp": "2026-03-15",
                "edd": "2026-12-20",
                "outcome": "ongoing",
                "loss_date": None,
                "ga_at_loss": None,
                "routine_paused": False,
                "created_at": "2026-06-04T10:30:00+01:00",
            }]
        },
    )

    id: UUID = Field(..., examples=["b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e"])
    patient_id: UUID = Field(..., examples=["7c1e9b40-2a3d-4f81-9c6a-1b2c3d4e5f60"])
    lmp: date = Field(..., examples=["2026-03-15"])
    edd: date = Field(..., examples=["2026-12-20"])
    outcome: str = Field(..., examples=["ongoing"])  # ongoing | live_birth | loss
    loss_date: Optional[date] = Field(default=None, examples=[None])
    ga_at_loss: Optional[int] = Field(default=None, examples=[None])
    routine_paused: bool = Field(..., examples=[False])
    created_at: datetime = Field(..., examples=["2026-06-04T10:30:00+01:00"])
