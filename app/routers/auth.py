"""Authentication routes for signup and login."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.hospital import Hospital
from app.models.patient import Patient
from app.schemas.common import LoginRequest, TokenResponse
from app.schemas.hospital import HospitalCreate, HospitalResponse
from app.schemas.patient import PatientCreate, PatientResponse
from app.utils.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.utils.pregnancy import compute_lmp_and_edd

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
        personnel_name=hospital_data.personnel_name,
        personnel_contact=hospital_data.personnel_contact,
    )

    db.add(hospital)
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
def patient_signup(
    patient_data: PatientCreate,
    db: Session = Depends(get_db),
):
    """Register a new patient account."""
    # Validate hospital_id exists
    hospital = db.query(Hospital).filter(Hospital.id == patient_data.hospital_id).first()
    if not hospital:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hospital not found",
        )

    # Check if phone already exists in patients table
    existing_patient = db.query(Patient).filter(Patient.phone == patient_data.phone).first()
    if existing_patient:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number already registered",
        )

    # Compute LMP and EDD from weeks pregnant at signup
    lmp, edd = compute_lmp_and_edd(patient_data.weeks_pregnant_at_signup)

    # Hash password and create patient record
    try:
        hashed_password = hash_password(patient_data.password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    patient = Patient(
        name=patient_data.name,
        phone=patient_data.phone,
        hashed_password=hashed_password,
        hospital_id=patient_data.hospital_id,
        weeks_pregnant_at_signup=patient_data.weeks_pregnant_at_signup,
        lmp=lmp,
        edd=edd,
        account_type="smartphone",
        history_of_pregnancy_loss=patient_data.history_of_pregnancy_loss,
        history_of_smoking=patient_data.history_of_smoking,
        known_chronic_conditions=patient_data.known_chronic_conditions,
    )

    db.add(patient)
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

