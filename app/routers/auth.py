"""Authentication routes for signup and login."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.hospital import Hospital
from app.models.patient import Patient
from app.schemas.common import LoginRequest, TokenResponse
from app.models.personnel import Personnel
from app.schemas.hospital import HospitalCreate, HospitalResponse
from app.schemas.patient import PatientCreate, PatientResponse
from app.utils.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.utils.pregnancy import compute_lmp_and_edd

from datetime import datetime, timezone, date as date_type
from app.models.pregnancy import Pregnancy
from app.models.risk_assessment import RiskAssessment
from app.services.risk_scoring import compute_risk
from app.schemas.patient import PatientCreate, PatientResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/hospital/signup", response_model=HospitalResponse, status_code=201)
def hospital_signup(
    hospital_data: HospitalCreate,
    db: Session = Depends(get_db),
):
    """Register a new hospital account."""
    # Check if phone already exists in hospitals table
    existing_hospital = db.query(Hospital).filter(Hospital.phone == hospital_data.phone).first()
    if existing_hospital:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number already registered",
        )

    # Hash password and create hospital record
    try:
        hashed_password = hash_password(hospital_data.password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    hospital = Hospital(
        name=hospital_data.name,
        phone=hospital_data.phone,
        hashed_password=hashed_password,
        gps_lat=hospital_data.gps_lat,
        gps_lng=hospital_data.gps_lng,
        address=hospital_data.address,
        is_active=True,
    )
    db.add(hospital)
    db.flush()  # get hospital.id before committing

    # Create first personnel in the same transaction — rolls back with hospital on failure
    fp = hospital_data.first_personnel
    personnel = Personnel(
        hospital_id=hospital.id,
        name=fp.name,
        phone=fp.phone,
        email=fp.email,
        role=fp.role,
    )
    db.add(personnel)
    db.commit()
    db.refresh(hospital)

    return hospital


@router.post("/hospital/login", response_model=TokenResponse)
def hospital_login(
    login_request: LoginRequest,
    db: Session = Depends(get_db),
):
    """Login endpoint for hospitals. Returns JWT token."""
    # Look up hospital by phone in hospitals table only
    hospital = db.query(Hospital).filter(Hospital.phone == login_request.phone).first()

    # Verify password (generic error message for security)
    if not hospital or not verify_password(login_request.password, hospital.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or password",
        )

    # Create JWT token with user info
    token_data = {
        "sub": hospital.phone,
        "type": "hospital",
        "user_id": str(hospital.id),
    }
    access_token = create_access_token(token_data)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        user_type="hospital",
    )


@router.post("/patient/signup", response_model=PatientResponse, status_code=201)
def patient_signup(body: PatientCreate, db: Session = Depends(get_db)):

    # 1. Validate hospital exists
    hospital = db.query(Hospital).filter(Hospital.id == body.hospital_id).first()
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")

    # 2. Check duplicate phone
    existing = db.query(Patient).filter(Patient.phone == body.phone).first()
    if existing:
        raise HTTPException(status_code=400, detail="Phone number already registered")

    # 3. Compute LMP and EDD
    from app.utils.pregnancy import compute_lmp_and_edd
    lmp, edd = compute_lmp_and_edd(body.weeks_pregnant_at_signup)

    # 4. Hash password
    from app.utils.auth import hash_password
    hashed = hash_password(body.password)

    # 5. Derive rh_negative from blood_group
    rh_negative = (
        body.blood_group is not None and body.blood_group.endswith("-")
    )

    # 5. Create patient record
    patient = Patient(
        name=body.name,
        phone=body.phone,
        hashed_password=hashed,
        hospital_id=body.hospital_id,
        weeks_pregnant_at_signup=body.weeks_pregnant_at_signup,
        lmp=lmp,
        edd=edd,
        age=body.age,
        parity=body.parity,
        language=body.language,
        preferred_support=body.preferred_support,
        # loss — keep boolean for back-compat, store count for v2 scoring
        previous_loss=body.previous_loss,
        previous_loss_count=body.previous_loss_count,
        previous_stillbirth=body.previous_stillbirth,
        previous_caesarean=body.previous_caesarean,
        previous_preeclampsia=body.previous_preeclampsia,
        has_hypertension=body.has_hypertension,
        has_diabetes=body.has_diabetes,
        has_sickle_cell=body.has_sickle_cell,
        has_hiv=body.has_hiv,
        has_severe_anaemia=body.has_severe_anaemia,
        multiple_pregnancy=body.multiple_pregnancy,
        late_anc_initiation=body.late_anc_initiation,
        no_prior_anc=body.no_prior_anc,
        # v2 collected-but-not-scored
        gravidity=body.gravidity,
        blood_group=body.blood_group,
        distance_close_to_hospital=body.distance_close_to_hospital,
        rh_negative=rh_negative,
        account_type="smartphone",
        status="active",
    )
    db.add(patient)
    db.flush()  # get patient.id without committing

    # 6. Compute risk level (v2 rubric — graded age + loss bands)
    answers = {
        "age":                    body.age,
        "previous_loss_count":    body.previous_loss_count,
        "weeks_pregnant_at_signup": body.weeks_pregnant_at_signup,
        "parity":                 body.parity,
        "previous_stillbirth":    body.previous_stillbirth,
        "previous_caesarean":     body.previous_caesarean,
        "previous_preeclampsia":  body.previous_preeclampsia,
        "has_hypertension":       body.has_hypertension,
        "has_diabetes":           body.has_diabetes,
        "has_sickle_cell":        body.has_sickle_cell,
        "has_hiv":                body.has_hiv,
        "has_severe_anaemia":     body.has_severe_anaemia,
        "multiple_pregnancy":     body.multiple_pregnancy,
    }
    result = compute_risk(answers)
    level = result["level"]
    score = result["score"]
    rubric_version = result["rubric_version"]
    breakdown = result["breakdown"]

    patient.risk_level = level
    patient.risk_level_set_at = datetime.now(timezone.utc)
    patient.risk_level_set_by = "system"

    # 7. Write RiskAssessment audit record (inputs includes breakdown for transparency)
    risk_record = RiskAssessment(
        patient_id=patient.id,
        computed_by="system",
        inputs={**answers, "_breakdown": breakdown},
        rubric_version=rubric_version,
        result_level=level,
        score=score,
    )
    db.add(risk_record)

    # 8. Create initial Pregnancy record
    pregnancy = Pregnancy(
        patient_id=patient.id,
        lmp=lmp,
        edd=edd,
        outcome="ongoing",
        routine_paused=False,
    )
    db.add(pregnancy)

    # 9. Commit everything in one transaction
    db.commit()
    db.refresh(patient)
    return patient


@router.post("/patient/login", response_model=TokenResponse)
def patient_login(
    login_request: LoginRequest,
    db: Session = Depends(get_db),
):
    """Login endpoint for patients. Returns JWT token."""
    # Look up patient by phone in patients table only
    patient = db.query(Patient).filter(Patient.phone == login_request.phone).first()

    # Verify password (generic error message for security)
    if not patient or not verify_password(login_request.password, patient.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or password",
        )

    # Create JWT token with user info
    token_data = {
        "sub": patient.phone,
        "type": "patient",
        "user_id": str(patient.id),
    }
    access_token = create_access_token(token_data)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        user_type="patient",
    )


@router.get("/me")
def get_me(current_user: dict = Depends(get_current_user)):
    """Protected endpoint: returns current user info from JWT."""
    return {
        "phone": current_user.get("sub"),
        "user_id": current_user.get("user_id"),
        "user_type": current_user.get("type"),
    }

