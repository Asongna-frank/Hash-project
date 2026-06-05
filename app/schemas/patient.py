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
    name: str = Field(..., examples=["Maria Nkeng"])
    phone: str = Field(..., examples=["+237679977660"])
    password: str = Field(..., examples=["StrongPass123!"])

    # Hospital link
    hospital_id: UUID = Field(..., examples=["4f996b23-92d3-4587-857b-038903d4253d"])

    # Pregnancy timing
    weeks_pregnant_at_signup: int = Field(..., ge=1, le=42, examples=[12])

    # Demographics
    age: int = Field(..., ge=13, le=60, examples=[29])
    parity: int = Field(default=0, ge=0, examples=[1])
    language: Optional[str] = Field(default=None, examples=["fr"])
    preferred_support: str = Field(default="none", examples=["faith"])

    # v2 scored: number of prior losses (replaces boolean previous_loss for scoring)
    previous_loss_count: int = Field(default=0, ge=0, examples=[1])
    # kept for backward-compatibility — if provided and count==0, count is set to 1
    previous_loss: bool = Field(default=False, examples=[True])

    # Other scored clinical flags
    previous_stillbirth: bool = Field(default=False, examples=[False])
    previous_caesarean: bool = Field(default=False, examples=[True])
    previous_preeclampsia: bool = Field(default=False, examples=[False])
    has_hypertension: bool = Field(default=False, examples=[True])
    has_diabetes: bool = Field(default=False, examples=[False])
    has_sickle_cell: bool = Field(default=False, examples=[False])
    has_hiv: bool = Field(default=False, examples=[False])
    has_severe_anaemia: bool = Field(default=False, examples=[False])
    multiple_pregnancy: bool = Field(default=False, examples=[False])

    # Legacy fields — stored but score 0 in v2 rubric
    late_anc_initiation: bool = Field(default=False, examples=[False])
    no_prior_anc: bool = Field(default=False, examples=[False])

    # v2 collected-but-not-scored fields
    gravidity: Optional[int] = Field(default=None, ge=0, examples=[3])
    blood_group: Optional[str] = Field(default=None, examples=["O+"])
    distance_close_to_hospital: Optional[bool] = Field(default=None, examples=[True])

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "name": "Maria Nkeng",
                "phone": "+237679977660",
                "password": "StrongPass123!",
                "hospital_id": "4f996b23-92d3-4587-857b-038903d4253d",
                "weeks_pregnant_at_signup": 12,
                "age": 29,
                "parity": 1,
                "language": "fr",
                "preferred_support": "faith",
                "previous_loss_count": 1,
                "previous_stillbirth": False,
                "previous_caesarean": True,
                "has_hypertension": True,
                "has_diabetes": False,
                "has_sickle_cell": False,
                "multiple_pregnancy": False,
                "gravidity": 3,
                "blood_group": "O+",
                "distance_close_to_hospital": True,
            }]
        }
    )

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


class HospitalPatientCreate(BaseModel):
    """
    Hospital-side creation of a choronko (SMS) patient. Same clinical fields as
    self-signup, but NO password (choronko patients never log in) and NO
    hospital_id (it is taken from the calling hospital's token, not the body).
    """
    name: str = Field(..., examples=["Aisha Bello"])
    phone: str = Field(..., examples=["+237678889900"])
    weeks_pregnant_at_signup: int = Field(..., ge=1, le=42, examples=[18])
    age: int = Field(..., ge=13, le=60, examples=[34])
    parity: int = Field(default=0, ge=0, examples=[2])
    language: Optional[str] = Field(default=None, examples=["en"])
    preferred_support: str = Field(default="none", examples=["peer"])

    previous_loss_count: int = Field(default=0, ge=0, examples=[0])
    previous_loss: bool = Field(default=False, examples=[False])
    previous_stillbirth: bool = Field(default=False, examples=[False])
    previous_caesarean: bool = Field(default=False, examples=[False])
    previous_preeclampsia: bool = Field(default=False, examples=[False])
    has_hypertension: bool = Field(default=False, examples=[False])
    has_diabetes: bool = Field(default=False, examples=[True])
    has_sickle_cell: bool = Field(default=False, examples=[False])
    has_hiv: bool = Field(default=False, examples=[False])
    has_severe_anaemia: bool = Field(default=False, examples=[False])
    multiple_pregnancy: bool = Field(default=False, examples=[False])
    late_anc_initiation: bool = Field(default=False, examples=[True])
    no_prior_anc: bool = Field(default=False, examples=[False])

    gravidity: Optional[int] = Field(default=None, ge=0, examples=[3])
    blood_group: Optional[str] = Field(default=None, examples=["A+"])
    distance_close_to_hospital: Optional[bool] = Field(default=None, examples=[False])

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "name": "Aisha Bello",
                "phone": "+237678889900",
                "weeks_pregnant_at_signup": 18,
                "age": 34,
                "parity": 2,
                "language": "en",
                "preferred_support": "peer",
                "has_diabetes": True,
                "late_anc_initiation": True,
                "gravidity": 3,
                "blood_group": "A+",
            }]
        }
    )

    @model_validator(mode="after")
    def sync_loss_fields(self) -> "HospitalPatientCreate":
        if self.previous_loss_count > 0:
            self.previous_loss = True
        elif self.previous_loss and self.previous_loss_count == 0:
            self.previous_loss_count = 1
        return self

    @model_validator(mode="after")
    def validate_blood_group(self) -> "HospitalPatientCreate":
        if self.blood_group is not None and self.blood_group not in _VALID_BLOOD_GROUPS:
            raise ValueError(f"blood_group must be one of {sorted(_VALID_BLOOD_GROUPS)}")
        return self


class PatientListItem(BaseModel):
    """Slim view returned by GET /patients (hospital list). Fields chosen by product spec."""
    id: UUID = Field(..., examples=["7c1e9b40-2a3d-4f81-9c6a-1b2c3d4e5f60"])
    name: str = Field(..., examples=["Maria Nkeng"])
    phone: str = Field(..., examples=["+237679977660"])
    age: Optional[int] = Field(default=None, examples=[29])
    status: str = Field(..., examples=["active"])
    current_ga_weeks: Optional[int] = Field(default=None, examples=[24])  # computed from lmp
    risk_level: Optional[str] = Field(default=None, examples=["high"])
    missed_checkin_flag: bool = Field(default=False, examples=[False])
    account_type: str = Field(default="smartphone", examples=["smartphone"])
    last_activity: Optional[datetime] = Field(default=None, examples=["2026-06-05T09:12:00+00:00"])  # latest message either direction

    model_config = ConfigDict(
        from_attributes=False,
        json_schema_extra={
            "examples": [{
                "id": "7c1e9b40-2a3d-4f81-9c6a-1b2c3d4e5f60",
                "name": "Maria Nkeng",
                "phone": "+237679977660",
                "age": 29,
                "status": "active",
                "current_ga_weeks": 24,
                "risk_level": "high",
                "missed_checkin_flag": False,
                "account_type": "smartphone",
                "last_activity": "2026-06-05T09:12:00+00:00",
            }]
        },
    )


# Fields a PATIENT may edit on her own record (safe self-service subset).
PATIENT_SELF_EDITABLE = frozenset({"name", "phone", "language", "preferred_support"})


class PatientUpdate(BaseModel):
    """
    Editable patient fields (all optional).

    A hospital may edit ALL of these on its own patients (incl. phone/identity and
    clinical fields). A patient editing her own record is limited server-side to
    PATIENT_SELF_EDITABLE; other fields she sends are ignored.
    """
    # Identity / demographics
    name: Optional[str] = Field(default=None, examples=["Maria Nkeng Etondi"])
    phone: Optional[str] = Field(default=None, examples=["+237679977661"])
    age: Optional[int] = Field(default=None, ge=13, le=60, examples=[30])
    parity: Optional[int] = Field(default=None, ge=0, examples=[2])
    language: Optional[str] = Field(default=None, examples=["en"])
    preferred_support: Optional[str] = Field(default=None, examples=["counsellor"])
    blood_group: Optional[str] = Field(default=None, examples=["O+"])
    status: Optional[str] = Field(default=None, examples=["active"])

    # Clinical flags (hospital-editable)
    previous_loss: Optional[bool] = Field(default=None, examples=[True])
    previous_loss_count: Optional[int] = Field(default=None, ge=0, examples=[1])
    previous_stillbirth: Optional[bool] = Field(default=None, examples=[False])
    previous_caesarean: Optional[bool] = Field(default=None, examples=[True])
    previous_preeclampsia: Optional[bool] = Field(default=None, examples=[False])
    has_hypertension: Optional[bool] = Field(default=None, examples=[True])
    has_diabetes: Optional[bool] = Field(default=None, examples=[False])
    has_sickle_cell: Optional[bool] = Field(default=None, examples=[False])
    has_hiv: Optional[bool] = Field(default=None, examples=[False])
    has_severe_anaemia: Optional[bool] = Field(default=None, examples=[False])
    multiple_pregnancy: Optional[bool] = Field(default=None, examples=[False])

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"language": "en", "preferred_support": "counsellor"}]}
    )


class PatientResponse(BaseModel):
    id: UUID = Field(..., examples=["7c1e9b40-2a3d-4f81-9c6a-1b2c3d4e5f60"])
    name: str = Field(..., examples=["Maria Nkeng"])
    phone: str = Field(..., examples=["+237679977660"])
    hospital_id: UUID = Field(..., examples=["4f996b23-92d3-4587-857b-038903d4253d"])
    weeks_pregnant_at_signup: int = Field(..., examples=[12])
    lmp: date = Field(..., examples=["2026-03-15"])
    edd: date = Field(..., examples=["2026-12-20"])
    account_type: str = Field(..., examples=["smartphone"])

    # Demographics
    age: Optional[int] = Field(default=None, examples=[29])
    parity: int = Field(..., examples=[1])
    language: Optional[str] = Field(default=None, examples=["fr"])
    preferred_support: str = Field(..., examples=["faith"])

    # Scored clinical history
    previous_loss: bool = Field(..., examples=[True])
    previous_loss_count: int = Field(..., examples=[1])
    previous_stillbirth: bool = Field(..., examples=[False])
    previous_caesarean: bool = Field(..., examples=[True])
    previous_preeclampsia: bool = Field(..., examples=[False])
    has_hypertension: bool = Field(..., examples=[True])
    has_diabetes: bool = Field(..., examples=[False])
    has_sickle_cell: bool = Field(..., examples=[False])
    has_hiv: bool = Field(..., examples=[False])
    has_severe_anaemia: bool = Field(..., examples=[False])
    multiple_pregnancy: bool = Field(..., examples=[False])

    # Legacy (kept, score 0)
    late_anc_initiation: bool = Field(..., examples=[False])
    no_prior_anc: bool = Field(..., examples=[False])

    # v2 collected-but-not-scored
    gravidity: Optional[int] = Field(default=None, examples=[3])
    blood_group: Optional[str] = Field(default=None, examples=["O+"])
    distance_close_to_hospital: Optional[bool] = Field(default=None, examples=[True])
    rh_negative: bool = Field(..., examples=[False])

    # Risk output
    risk_level: Optional[str] = Field(default=None, examples=["medium"])
    risk_level_set_at: Optional[datetime] = Field(default=None, examples=["2026-06-04T10:30:00+01:00"])

    # Status
    status: str = Field(..., examples=["active"])
    is_active: bool = Field(..., examples=[True])

    # Messaging opt-out state (PAUSE/STOP/RESUME keywords) — lets the app show
    # a "messages paused" banner and a resume control.
    opt_out_status: Optional[str] = Field(default=None, examples=[None])  # null|paused|stopped
    paused_until: Optional[datetime] = Field(default=None, examples=[None])

    # Missed check-in tracking (used by clinician dashboard)
    consecutive_missed_checkins: int = Field(..., examples=[0])
    missed_checkin_flag: bool = Field(..., examples=[False])

    created_at: datetime = Field(..., examples=["2026-06-04T10:30:00+01:00"])

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [{
                "id": "7c1e9b40-2a3d-4f81-9c6a-1b2c3d4e5f60",
                "name": "Maria Nkeng",
                "phone": "+237679977660",
                "hospital_id": "4f996b23-92d3-4587-857b-038903d4253d",
                "weeks_pregnant_at_signup": 12,
                "lmp": "2026-03-15",
                "edd": "2026-12-20",
                "account_type": "smartphone",
                "age": 29,
                "parity": 1,
                "language": "fr",
                "preferred_support": "faith",
                "previous_loss": True,
                "previous_loss_count": 1,
                "previous_stillbirth": False,
                "previous_caesarean": True,
                "previous_preeclampsia": False,
                "has_hypertension": True,
                "has_diabetes": False,
                "has_sickle_cell": False,
                "has_hiv": False,
                "has_severe_anaemia": False,
                "multiple_pregnancy": False,
                "late_anc_initiation": False,
                "no_prior_anc": False,
                "gravidity": 3,
                "blood_group": "O+",
                "distance_close_to_hospital": True,
                "rh_negative": False,
                "risk_level": "medium",
                "risk_level_set_at": "2026-06-04T10:30:00+01:00",
                "status": "active",
                "is_active": True,
                "consecutive_missed_checkins": 0,
                "missed_checkin_flag": False,
                "created_at": "2026-06-04T10:30:00+01:00",
            }]
        },
    )
