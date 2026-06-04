# app/schemas/patient.py
from datetime import date, datetime
from typing import Optional, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

_VALID_BLOOD_GROUPS = frozenset(
    {"A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-", "unknown"}
)


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

    # v2 scored: number of prior losses (replaces boolean previous_loss for scoring)
    previous_loss_count: int = Field(default=0, ge=0)
    # kept for backward-compatibility — if provided and count==0, count is set to 1
    previous_loss: bool = False

    # Other scored clinical flags
    previous_stillbirth: bool = False
    previous_caesarean: bool = False
    previous_preeclampsia: bool = False
    has_hypertension: bool = False
    has_diabetes: bool = False
    has_sickle_cell: bool = False
    has_hiv: bool = False
    has_severe_anaemia: bool = False
    multiple_pregnancy: bool = False

    # Legacy fields — stored but score 0 in v2 rubric
    late_anc_initiation: bool = False
    no_prior_anc: bool = False

    # v2 collected-but-not-scored fields
    gravidity: Optional[int] = Field(default=None, ge=0)
    blood_group: Optional[str] = None
    distance_close_to_hospital: Optional[bool] = None

    @model_validator(mode="after")
    def sync_loss_fields(self) -> "PatientCreate":
        # Reconcile boolean + count: count takes precedence
        if self.previous_loss_count > 0:
            self.previous_loss = True
        elif self.previous_loss and self.previous_loss_count == 0:
            self.previous_loss_count = 1
        return self

    @model_validator(mode="after")
    def validate_blood_group(self) -> "PatientCreate":
        if self.blood_group is not None and self.blood_group not in _VALID_BLOOD_GROUPS:
            raise ValueError(
                f"blood_group must be one of {sorted(_VALID_BLOOD_GROUPS)}"
            )
        return self


class PatientListItem(BaseModel):
    """Slim view returned by GET /patients (hospital list). Fields chosen by product spec."""
    id: UUID
    name: str
    phone: str
    age: Optional[int]
    status: str
    current_ga_weeks: Optional[int]  # computed on-the-fly from lmp; None if lmp missing

    model_config = ConfigDict(from_attributes=False)


class PatientUpdate(BaseModel):
    name: Optional[str] = None
    language: Optional[str] = None
    preferred_support: Optional[str] = None


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

    # Demographics
    age: Optional[int]
    parity: int
    language: Optional[str]
    preferred_support: str

    # Scored clinical history
    previous_loss: bool
    previous_loss_count: int
    previous_stillbirth: bool
    previous_caesarean: bool
    previous_preeclampsia: bool
    has_hypertension: bool
    has_diabetes: bool
    has_sickle_cell: bool
    has_hiv: bool
    has_severe_anaemia: bool
    multiple_pregnancy: bool

    # Legacy (kept, score 0)
    late_anc_initiation: bool
    no_prior_anc: bool

    # v2 collected-but-not-scored
    gravidity: Optional[int]
    blood_group: Optional[str]
    distance_close_to_hospital: Optional[bool]
    rh_negative: bool

    # Risk output
    risk_level: Optional[str]
    risk_level_set_at: Optional[datetime]

    # Status
    status: str
    is_active: bool

    # Missed check-in tracking (used by clinician dashboard)
    consecutive_missed_checkins: int
    missed_checkin_flag: bool

    created_at: datetime
