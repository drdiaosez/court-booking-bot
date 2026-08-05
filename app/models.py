import enum
from datetime import datetime, time

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Platform(str, enum.Enum):
    COURTRESERVE = "courtreserve"
    GENERIC = "generic"  # placeholder for future adapters (PlayByPoint, CourtHive, etc.)


class JobStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    PREWARMING = "prewarming"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    credentials: Mapped[list["FacilityCredential"]] = relationship(back_populates="user")
    jobs: Mapped[list["BookingJob"]] = relationship(back_populates="user")


class Facility(Base):
    """A real-world facility/club. Shared across users; each user attaches their own credential."""

    __tablename__ = "facilities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    platform: Mapped[Platform] = mapped_column(Enum(Platform), default=Platform.COURTRESERVE)
    # e.g. https://app.courtreserve.com/Online/Portal/Index/16314 -> base_url + portal_id
    base_url: Mapped[str] = mapped_column(String(500))
    portal_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Los_Angeles")
    booking_window_days: Mapped[int] = mapped_column(Integer, default=7)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional JSON blob of platform-specific tuning (CSS selectors, URL paths, etc.)
    # that overrides the adapter's built-in defaults. See app/adapters/courtreserve.py.
    adapter_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    credentials: Mapped[list["FacilityCredential"]] = relationship(back_populates="facility")
    jobs: Mapped[list["BookingJob"]] = relationship(back_populates="facility")


class FacilityCredential(Base):
    """A single user's login for a single facility. Password is stored Fernet-encrypted."""

    __tablename__ = "facility_credentials"
    __table_args__ = (UniqueConstraint("user_id", "facility_id", name="uq_user_facility"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    facility_id: Mapped[int] = mapped_column(ForeignKey("facilities.id"))
    username: Mapped[str] = mapped_column(String(255))
    encrypted_password: Mapped[str] = mapped_column(Text)
    member_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="credentials")
    facility: Mapped["Facility"] = relationship(back_populates="credentials")


class BookingJob(Base):
    """One scheduled attempt to book a specific court/time slot at a specific facility."""

    __tablename__ = "booking_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    facility_id: Mapped[int] = mapped_column(ForeignKey("facilities.id"))
    credential_id: Mapped[int] = mapped_column(ForeignKey("facility_credentials.id"))

    target_date: Mapped[datetime] = mapped_column(DateTime)  # date of the court reservation itself
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    court_preference: Mapped[str | None] = mapped_column(String(255), nullable=True)  # e.g. "Court 3" or "any"

    advance_days_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_at: Mapped[datetime] = mapped_column(DateTime)  # UTC instant the booking window opens
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.SCHEDULED)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="jobs")
    facility: Mapped["Facility"] = relationship(back_populates="jobs")
    credential: Mapped["FacilityCredential"] = relationship()
    logs: Mapped[list["JobLog"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobLog(Base):
    """Step-by-step trail for a job run: useful for debugging selector/timing issues."""

    __tablename__ = "job_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("booking_jobs.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    level: Mapped[str] = mapped_column(String(20), default="info")
    message: Mapped[str] = mapped_column(Text)
    screenshot_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    job: Mapped["BookingJob"] = relationship(back_populates="logs")
