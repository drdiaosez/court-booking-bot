"""
Executes a single BookingJob: pre-warms a browser session ahead of the exact booking
window opening time, spin-waits for that precise instant, then repeatedly attempts the
booking for a short grace window (facility/site clocks are never perfectly in sync with
the atomic clock, so "exactly midnight" in practice means "midnight, plus or minus a
couple seconds of jitter").
"""

from __future__ import annotations

import asyncio
import time as time_module
from datetime import datetime, timezone

from playwright.async_api import async_playwright
from sqlalchemy.orm import Session

from app.adapters.base import BookingRequest, BookingError, SlotNotYetAvailable
from app.adapters.registry import get_adapter_class
from app.config import get_settings
from app.models import BookingJob, JobLog, JobStatus

settings = get_settings()

# Once we hit the scheduled instant, keep retrying for this long before giving up --
# covers small clock skew between this server and the facility's booking system.
POST_OPEN_GRACE_SECONDS = 25
RETRY_INTERVAL_SECONDS = 1.0

# Hard ceilings so a stuck browser launch/login/navigation fails loudly and frees whatever
# it was holding, instead of hanging the job (and the process it spawned) forever. This is
# what was missing the first time this was written -- a launch that never returns used to
# just sit there consuming memory indefinitely with nothing to time it out.
LAUNCH_TIMEOUT_SECONDS = 30
LOGIN_TIMEOUT_SECONDS = 45
NAVIGATE_TIMEOUT_SECONDS = 45


def _add_log(db: Session, job: BookingJob, level: str, message: str, screenshot_path: str | None) -> None:
    db.add(JobLog(job_id=job.id, level=level, message=message, screenshot_path=screenshot_path))
    job.updated_at = datetime.utcnow()
    db.commit()


async def execute_job(job_id: int, session_factory) -> None:
    db: Session = session_factory()
    try:
        job = db.get(BookingJob, job_id)
        if job is None or job.status == JobStatus.CANCELLED:
            return

        job.status = JobStatus.PREWARMING
        db.commit()

        facility = job.facility
        credential = job.credential
        from app.security import decrypt_secret

        password = decrypt_secret(credential.encrypted_password)

        def log_cb(level: str, message: str, screenshot_path: str | None) -> None:
            _add_log(db, job, level, message, screenshot_path)

        request = BookingRequest(
            base_url=facility.base_url,
            portal_id=facility.portal_id,
            username=credential.username,
            password=password,
            member_id=credential.member_id,
            target_date=job.target_date.date(),
            start_time=job.start_time,
            end_time=job.end_time,
            court_preference=job.court_preference,
            dry_run=job.dry_run,
            adapter_config_json=facility.adapter_config,
            log=log_cb,
        )

        adapter_cls = get_adapter_class(facility.platform)

        async with async_playwright() as pw:
            browser = None
            try:
                try:
                    browser = await asyncio.wait_for(
                        pw.chromium.launch(headless=settings.playwright_headless),
                        timeout=LAUNCH_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    job.status = JobStatus.FAILED
                    job.last_error = (
                        f"Browser launch did not return within {LAUNCH_TIMEOUT_SECONDS}s. This usually means "
                        "the host is out of memory/resources for Chromium (check `free -h` and consider "
                        "adding swap, resizing the server, or adding a systemd MemoryMax= limit so this "
                        "fails fast instead of taking the whole box down)."
                    )
                    db.commit()
                    return

                context = await browser.new_context()
                page = await context.new_page()
                adapter = adapter_cls(request, page, context, browser)

                try:
                    await asyncio.wait_for(adapter.login(), timeout=LOGIN_TIMEOUT_SECONDS)
                    await asyncio.wait_for(adapter.navigate_to_booking_calendar(), timeout=NAVIGATE_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    job.status = JobStatus.FAILED
                    job.last_error = "Login or navigation timed out -- the facility site may be slow, unreachable, or the selectors are stuck waiting on something that never appears."
                    db.commit()
                    return
                except BookingError as exc:
                    job.status = JobStatus.FAILED
                    job.last_error = str(exc)
                    db.commit()
                    return

                # Spin-wait for the exact run_at instant (stored as UTC).
                run_at_utc = job.run_at.replace(tzinfo=timezone.utc) if job.run_at.tzinfo is None else job.run_at
                _add_log(db, job, "info", f"Pre-warmed; waiting until {run_at_utc.isoformat()} to attempt booking", None)
                while True:
                    now = datetime.now(timezone.utc)
                    remaining = (run_at_utc - now).total_seconds()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(remaining, 0.25))

                job.status = JobStatus.RUNNING
                db.commit()

                deadline = time_module.monotonic() + POST_OPEN_GRACE_SECONDS
                last_error: str | None = None
                success = False
                while time_module.monotonic() < deadline and job.attempts < job.max_attempts:
                    job.attempts += 1
                    db.commit()
                    try:
                        bookable = await adapter.slot_is_bookable()
                        if not bookable:
                            last_error = "Slot not yet bookable (booking window may not be open yet)."
                            await asyncio.sleep(RETRY_INTERVAL_SECONDS)
                            continue
                        result = await adapter.book_slot()
                        job.status = JobStatus.SUCCESS
                        job.result_note = result
                        db.commit()
                        _add_log(db, job, "info", result, None)
                        success = True
                        break
                    except SlotNotYetAvailable as exc:
                        last_error = str(exc)
                        await asyncio.sleep(RETRY_INTERVAL_SECONDS)
                    except BookingError as exc:
                        last_error = str(exc)
                        _add_log(db, job, "error", str(exc), None)
                        await asyncio.sleep(RETRY_INTERVAL_SECONDS)

                if not success:
                    job.status = JobStatus.FAILED
                    job.last_error = last_error or "Exhausted retries without a confirmed booking."
                    db.commit()
            finally:
                # Always attempted, even on timeout/exception/early-return above -- this is
                # the fix for the freeze: a hung job no longer leaves its browser dangling
                # indefinitely with nothing responsible for cleaning it up.
                if browser is not None:
                    try:
                        await asyncio.wait_for(browser.close(), timeout=10)
                    except Exception:
                        pass
    finally:
        db.close()
