"""User models using SQLAlchemy single-table inheritance."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, String, Boolean, UUID

from app.core.database import Base


class BaseUser(Base):
    """
    Base user class using single-table inheritance with discriminator column.
    All users (Hospital and Patient) are stored in the 'users' table.
    """

    __tablename__ = "users"

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Common fields
    name = Column(String, nullable=False)
    phone = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    user_type = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # For polymorphic queries
    __mapper_args__ = {
        "polymorphic_on": user_type,
        "polymorphic_identity": "user",
    }


class Hospital(BaseUser):
    """Hospital user with location and staff contact information."""

    gps_lat = Column(Float, nullable=True)
    gps_lng = Column(Float, nullable=True)
    address = Column(String, nullable=True)
    personnel_name = Column(String, nullable=True)
    personnel_contact = Column(String, nullable=True)

    __mapper_args__ = {
        "polymorphic_identity": "hospital",
    }


class Patient(BaseUser):
    """
    Patient user with health profile and pregnancy-related information.
    """

    # Account type
    account_type = Column(String, nullable=True)  # "smartphone" or "choronko"

    # TODO: Replace with Dr Elvira's clinical questionnaire
    history_of_pregnancy_loss = Column(Boolean, default=False, nullable=False)
    history_of_smoking = Column(Boolean, default=False, nullable=False)
    known_chronic_conditions = Column(String, nullable=True)
    # TODO: Replace with Dr Elvira's clinical questionnaire
    # TODO: Replace with Dr Elvira's clinical questionnaire

    __mapper_args__ = {
        "polymorphic_identity": "patient",
    }
