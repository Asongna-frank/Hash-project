# app/schemas/patient.py
from datetime import date, datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class PatientCreate(BaseModel):
    # Core identity
    name: str
    phone: str
    password: str

    # Hospital link
    hospital_id: UUID

    # Pregnancy timing
    weeks_pregnant_at_signup: int = Field(..., ge=1, le=42)

    # Demographics
    age: int = Field(..., ge=13, le=60)
    parity: int = Field(default=0, ge=0)
    language: Optional[str] = None
    preferred_support: str = "none"

    # Clinical questionnaire
    # TODO: weights and thresholds are in app/core/risk_config.py
    previous_loss: bool = False
    previous_stillbirth: bool = False
    previous_caesarean: bool = False
    previous_preeclampsia: bool = False
    has_hypertension: bool = False
    has_diabetes: bool = False
    has_sickle_cell: bool = False
    has_hiv: bool = False
    has_severe_anaemia: bool = False
    multiple_pregnancy: bool = False
    late_anc_initiation: bool = False
    no_prior_anc: bool = False

    @property
    def age_outside_range(self) -> bool:
        return self.age <= 17 or self.age >= 35


class PatientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    phone: str
    hospital_id: UUID
    weeks_pregnant_at_signup: int
    lmp: date
    edd: date
    account_type: str
    age: Optional[int]
    parity: int
    language: Optional[str]
    preferred_support: str
    previous_loss: bool
    previous_stillbirth: bool
    previous_caesarean: bool
    previous_preeclampsia: bool
    has_hypertension: bool
    has_diabetes: bool
    has_sickle_cell: bool
    has_hiv: bool
    has_severe_anaemia: bool
    multiple_pregnancy: bool
    late_anc_initiation: bool
    no_prior_anc: bool
    risk_level: Optional[str]
    risk_level_set_at: Optional[datetime]
    status: str
    created_at: datetime