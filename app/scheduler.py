"""
Owns the APScheduler instance for the whole process. Two responsibilities:

1. compute_run_at(): given a facility + target reservation date, work out the exact
   UTC instant the facility's booking window opens (target_date - advance_days, at
   00:00:00 in the facility's own timezone).
2. schedule_job()/unschedule_job(): add/remove a BookingJob's pre-warm-and-fire task
   from the live scheduler, so newly created jobs take effect immediately without a
   process restart, and cancelled jobs stop running.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.database import SessionLocal
from app.models import BookingJob, JobStatus
from app.worker import execute_job

settings = get_settings()
scheduler = AsyncIOScheduler()


def compute_run_at(target_date, facility_timezone: str, advance_days: int) -> datetime:
    """Return the UTC datetime at which the booking window opens for target_date."""
    tz = ZoneInfo(facility_timezone or settings.default_timezone)
    open_date = target_date - timedelta(days=advance_days)
    local_midnight = datetime.combine(open_date, datetime.min.time(), tzinfo=tz)
    return local_midnight.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _apscheduler_job_id(job_id: int) -> str:
    return f"booking-job-{job_id}"


def schedule_job(job_id: int, run_at: datetime) -> None:
    prewarm_at = run_at - timedelta(seconds=settings.prewarm_lead_seconds)
    now = datetime.utcnow()
    fire_at = max(prewarm_at, now + timedelta(seconds=1))
    scheduler.add_job(
        execute_job,
        trigger="date",
        run_date=fire_at,
        args=[job_id, SessionLocal],
        id=_apscheduler_job_id(job_id),
        replace_existing=True,
        misfire_grace_time=3600,
    )


def unschedule_job(job_id: int) -> None:
    try:
        scheduler.remove_job(_apscheduler_job_id(job_id))
    except Exception:
        pass


def restore_pending_jobs() -> None:
    """Called once at startup: re-arm every job that's still scheduled in the DB.

    Needed because APScheduler's in-memory job list doesn't survive a process restart --
    without this, a job created yesterday would silently never fire after a redeploy.
    """
    db = SessionLocal()
    try:
        pending = db.query(BookingJob).filter(BookingJob.status.in_([JobStatus.SCHEDULED, JobStatus.PREWARMING])).all()
        for job in pending:
            if job.run_at < datetime.utcnow() - timedelta(hours=1):
                job.status = JobStatus.FAILED
                job.last_error = "Missed: server was down when the booking window opened."
                continue
            job.status = JobStatus.SCHEDULED
            schedule_job(job.id, job.run_at)
        db.commit()
    finally:
        db.close()
