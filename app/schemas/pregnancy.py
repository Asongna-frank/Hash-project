# app/schemas/pregnancy.py
from datetime import date, datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class PregnancyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    lmp: date
    edd: date
    outcome: str
    loss_date: Optional[date]
    ga_at_loss: Optional[int]
    routine_paused: bool
    created_at: datetime