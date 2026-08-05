from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import BookingJob, Facility, FacilityCredential, JobStatus, User
from app.schemas import JobIn, JobOut
from app.scheduler import compute_run_at, schedule_job, unschedule_job

router = APIRouter()


@router.get("/api/jobs", response_model=list[JobOut])
def list_jobs(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    jobs = (
        db.query(BookingJob)
        .filter(BookingJob.user_id == user.id)
        .order_by(BookingJob.run_at.desc())
        .all()
    )
    return jobs


@router.get("/api/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    job = db.get(BookingJob, job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id,
        "facility": job.facility.name,
        "target_date": job.target_date.date().isoformat(),
        "start_time": job.start_time.isoformat(),
        "end_time": job.end_time.isoformat(),
        "court_preference": job.court_preference,
        "run_at": job.run_at.isoformat(),
        "status": job.status,
        "attempts": job.attempts,
        "last_error": job.last_error,
        "result_note": job.result_note,
        "dry_run": job.dry_run,
        "logs": [
            {
                "timestamp": log.timestamp.isoformat(),
                "level": log.level,
                "message": log.message,
                "screenshot_path": log.screenshot_path,
            }
            for log in sorted(job.logs, key=lambda l: l.timestamp)
        ],
    }


@router.post("/api/jobs", response_model=JobOut)
def create_job(payload: JobIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    facility = db.get(Facility, payload.facility_id)
    if not facility:
        raise HTTPException(status_code=404, detail="Facility not found")
    credential = db.get(FacilityCredential, payload.credential_id)
    if not credential or credential.user_id != user.id or credential.facility_id != facility.id:
        raise HTTPException(status_code=404, detail="Credential not found for this user/facility")

    advance_days = payload.advance_days_override or facility.booking_window_days
    run_at = compute_run_at(payload.target_date, facility.timezone, advance_days)

    if run_at < datetime.utcnow():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Computed booking-window open time {run_at.isoformat()} UTC is already in the "
                "past for this target date/advance window. Double check the target date and the "
                "facility's booking_window_days."
            ),
        )

    job = BookingJob(
        user_id=user.id,
        facility_id=facility.id,
        credential_id=credential.id,
        target_date=datetime.combine(payload.target_date, datetime.min.time()),
        start_time=payload.start_time,
        end_time=payload.end_time,
        court_preference=payload.court_preference,
        advance_days_override=payload.advance_days_override,
        run_at=run_at,
        dry_run=payload.dry_run,
        status=JobStatus.SCHEDULED,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    schedule_job(job.id, job.run_at)
    return job


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    job = db.get(BookingJob, job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    job.status = JobStatus.CANCELLED
    db.commit()
    unschedule_job(job.id)
    return {"ok": True}
