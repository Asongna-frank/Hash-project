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
from app.utils.phone import normalize_phone, normalize_phone_or_422
from app.services.patient_onboarding import onboard_patient
from app.services.audit import write_audit
from app.utils.pregnancy import compute_lmp_and_edd

from datetime import datetime, timezone, date as date_type
from app.models.pregnancy import Pregnancy
from app.models.risk_assessment import RiskAssessment
from app.services.risk_scoring import compute_risk
from app.schemas.patient import PatientCreate, PatientResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/hospital/signup",
    response_model=HospitalResponse,
    status_code=201,
    summary="Register a new hospital",
    description=(
        "Creates a hospital account plus its first personnel record in one "
        "transaction. Public endpoint — no authentication. The hospital phone "
        "is normalized to E.164 and becomes the hospital's unique login id; a "
        "duplicate phone returns 400 and an invalid phone returns 422."
    ),
)
def hospital_signup(
    hospital_data: HospitalCreate,
    db: Session = Depends(get_db),
):
    """Register a new hospital account."""
    # Normalize phones to E.164 before any lookup or storage
    hospital_phone = normalize_phone_or_422(hospital_data.phone, "hospital phone")
    personnel_phone = normalize_phone_or_422(
        hospital_data.first_personnel.phone, "personnel phone"
    )

    # Check if phone already exists in hospitals table
    existing_hospital = db.query(Hospital).filter(Hospital.phone == hospital_phone).first()
    if existing_hospital:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number already registered",
        )

    # Optional email — normalized lowercase, must be unique when provided
    hospital_email = (hospital_data.email or "").strip().lower() or None
    if hospital_email and db.query(Hospital).filter(Hospital.email == hospital_email).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
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
        phone=hospital_phone,
        email=hospital_email,
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
        phone=personnel_phone,
        email=fp.email,
        role=fp.role,
    )
    db.add(personnel)
    db.commit()
    db.refresh(hospital)

    return hospital


@router.post(
    "/hospital/login",
    response_model=TokenResponse,
    summary="Hospital login",
    description=(
        "Authenticates a hospital by PHONE or EMAIL + password and returns a "
        "JWT. Public endpoint. Send either {\"phone\", \"password\"} or "
        "{\"email\", \"password\"}. The phone is normalized before lookup. "
        "Wrong credentials return a generic 401 (never reveals which part failed)."
    ),
)
def hospital_login(
    login_request: LoginRequest,
    db: Session = Depends(get_db),
):
    """Login endpoint for hospitals. Accepts phone OR email + password."""
    hospital = None
    if login_request.email:
        hospital = (
            db.query(Hospital)
            .filter(Hospital.email == login_request.email.strip().lower())
            .first()
        )
    elif login_request.phone:
        # Normalize the phone to E.164 so any valid input format matches the
        # stored number. Invalid input falls back to raw → no match → 401.
        try:
            lookup_phone = normalize_phone(login_request.phone)
        except ValueError:
            lookup_phone = login_request.phone
        hospital = db.query(Hospital).filter(Hospital.phone == lookup_phone).first()

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


@router.post(
    "/patient/signup",
    response_model=PatientResponse,
    status_code=201,
    summary="Register a new smartphone patient",
    description=(
        "Self-registers a smartphone patient under an existing hospital. Computes "
        "and stores LMP and EDD from weeks pregnant, runs baseline risk scoring, "
        "and creates the initial pregnancy + risk-assessment audit records. Public "
        "endpoint. Phone is normalized to E.164 and is the patient's unique id. "
        "Unknown hospital → 404, duplicate phone → 400, invalid phone → 422."
    ),
)
def patient_signup(body: PatientCreate, db: Session = Depends(get_db)):

    # 1. Validate hospital exists
    hospital = db.query(Hospital).filter(Hospital.id == body.hospital_id).first()
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")

    # 2. Normalize phone to E.164, then check duplicate
    patient_phone = normalize_phone_or_422(body.phone, "phone")
    existing = db.query(Patient).filter(Patient.phone == patient_phone).first()
    if existing:
        raise HTTPException(status_code=400, detail="Phone number already registered")

    # 3. Onboard via the shared service (smartphone, with a password).
    data = {**body.model_dump(), "phone": patient_phone}
    patient = onboard_patient(
        db, data,
        hospital_id=body.hospital_id,
        account_type="smartphone",
        hashed_password=hash_password(body.password),
    )

    # 4. Audit: self-signup (actor is the patient herself), then one commit.
    write_audit(
        db, actor_type="patient", actor_id=patient.id,
        action="patient.signup", target_type="patient", target_id=patient.id,
        details={"account_type": "smartphone"},
    )
    db.commit()
    db.refresh(patient)
    return patient


@router.post(
    "/patient/login",
    response_model=TokenResponse,
    summary="Patient login",
    description=(
        "Authenticates a patient by phone + password and returns a JWT. Public "
        "endpoint. The phone is normalized before lookup. Wrong phone or password "
        "returns a generic 401 (never reveals whether the phone exists)."
    ),
)
def patient_login(
    login_request: LoginRequest,
    db: Session = Depends(get_db),
):
    """Login endpoint for patients. Returns JWT token."""
    if not login_request.phone:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or password",
        )
    # Normalize the phone to E.164 so any valid input format matches the stored
    # number. Invalid input falls back to raw → no match → generic 401 (rule 7).
    try:
        lookup_phone = normalize_phone(login_request.phone)
    except ValueError:
        lookup_phone = login_request.phone

    # Look up patient by phone in patients table only
    patient = db.query(Patient).filter(Patient.phone == lookup_phone).first()

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


@router.get(
    "/me",
    summary="Current authenticated identity",
    description=(
        "Returns the caller's identity decoded from the JWT: phone, user_id, and "
        "user_type ('hospital' or 'patient'). Requires a valid bearer token."
    ),
)
def get_me(current_user: dict = Depends(get_current_user)):
    """Protected endpoint: returns current user info from JWT."""
    return {
        "phone": current_user.get("sub"),
        "user_id": current_user.get("user_id"),
        "user_type": current_user.get("type"),
    }

