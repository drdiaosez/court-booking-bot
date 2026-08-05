from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models import JobStatus, Platform


class SignupIn(BaseModel):
    email: EmailStr
    password: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class FacilityIn(BaseModel):
    name: str
    platform: Platform = Platform.COURTRESERVE
    base_url: str
    portal_id: str | None = None
    timezone: str = "America/Los_Angeles"
    booking_window_days: int = 7
    notes: str | None = None


class CredentialIn(BaseModel):
    facility_id: int
    username: str
    password: str
    member_id: str | None = None


class JobIn(BaseModel):
    facility_id: int
    credential_id: int
    target_date: date
    start_time: time
    end_time: time
    court_preference: str | None = None
    advance_days_override: int | None = None
    dry_run: bool = False
    instant: bool = False  # skip the scheduled wait entirely and fire (almost) immediately


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    facility_id: int
    target_date: date
    start_time: time
    end_time: time
    court_preference: str | None
    run_at: datetime
    status: JobStatus
    attempts: int
    last_error: str | None
    result_note: str | None
