"""
CourtReserve adapter.

CourtReserve (app.courtreserve.com) is a white-labeled, multi-tenant SaaS booking
platform used by many separate clubs/facilities -- each facility just gets its own
"portal id" in the URL (e.g. .../Online/Portal/Index/16314). That means ONE adapter
here can drive bookings for every facility that happens to run on CourtReserve; the
only per-facility difference is the portal id, and occasionally small DOM/skin
differences that the `adapter_config` override on the Facility model exists to handle.

IMPORTANT / HONESTY NOTE:
This adapter was written without being able to inspect the live, logged-in DOM of
app.courtreserve.com (browser tooling wasn't available in the session that generated
this code, and Claude does not and should not log into a real member account with a
real password on your behalf). The selectors/text below are best-effort, based on how
CourtReserve's public-facing pages and typical ASP.NET MVC member portals are
structured, and are written to degrade gracefully (try several strategies, fail with
a clear error + screenshot rather than silently misclicking).

Before trusting this against a real booking window, run a job with dry_run=True.
Dry-run goes all the way through login + navigating to the target date + locating the
slot, and stops just before the final "confirm booking" click, taking a screenshot at
every step. Use those screenshots (see data/screenshots/ or the job's log in the UI)
to correct `adapter_config` on the Facility if any step didn't do what you expected.
See README.md "Calibrating a new facility" for the walkthrough.
"""

from __future__ import annotations

import json
from datetime import date as date_cls

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.adapters.base import BookingAdapter, BookingError, SlotNotYetAvailable

DEFAULT_CONFIG = {
    # Login page. {base_url} is substituted at runtime.
    "login_url": "{base_url}/Online/Account/Login",
    "email_selectors": [
        "input[name='Email']",
        "input#Email",
        "input[name='UserNameOrEmail']",
        "input[type='email']",
    ],
    "password_selectors": ["input[name='Password']", "input#Password", "input[type='password']"],
    "login_submit_selectors": [
        "button:has-text('Log In')",
        "button:has-text('Login')",
        "button[type='submit']",
        "input[type='submit']",
    ],
    "post_login_indicator_selectors": ["text=Logout", "text=Log Out", "text=My Account"],
    # Reservations / bookings module. Adjust once you've found the real path for your club.
    "reservations_url": "{base_url}/Online/Reservations/Bookings/{portal_id}",
    "reservations_nav_link_text": ["Reservations", "Book a Court", "Book Now", "Court Reservations"],
    # Date picker: CourtReserve typically exposes a date field or forward/back day arrows.
    "date_input_selectors": ["input.date-picker", "input[name='SelectedDate']", "input[type='date']"],
    "next_day_selectors": ["a.next-day", "button[aria-label='Next day']", "text=›"],
    # Slot grid: each bookable cell usually has a click handler and shows court + time.
    "slot_cell_selector": "[data-court], .reservation-slot, .fc-timegrid-slot, td.available",
    "confirm_button_selectors": [
        "button:has-text('Book')",
        "button:has-text('Reserve')",
        "button:has-text('Confirm')",
        "button:has-text('Save')",
    ],
    "already_booked_text": ["already reserved", "not available", "fully booked"],
    "booking_window_closed_text": ["not yet open", "cannot book more than", "booking opens"],
}


class CourtReserveAdapter(BookingAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = dict(DEFAULT_CONFIG)
        # Facility.adapter_config (JSON) can override/extend any key above without a code change.
        raw = getattr(self.request, "adapter_config_json", None)
        if raw:
            try:
                overrides = json.loads(raw)
                self.config.update(overrides)
            except json.JSONDecodeError:
                pass

    def _url(self, key: str) -> str:
        return self.config[key].format(
            base_url=self.request.base_url.rstrip("/"), portal_id=self.request.portal_id or ""
        )

    async def _first_visible(self, selectors: list[str], timeout_ms: int = 4000):
        for sel in selectors:
            try:
                locator = self.page.locator(sel).first
                await locator.wait_for(state="visible", timeout=timeout_ms)
                return locator
            except PlaywrightTimeoutError:
                continue
        return None

    async def login(self) -> None:
        await self.log(f"Navigating to login page", screenshot=False)
        await self.page.goto(self._url("login_url"), wait_until="domcontentloaded")

        email_field = await self._first_visible(self.config["email_selectors"])
        password_field = await self._first_visible(self.config["password_selectors"])
        if not email_field or not password_field:
            await self.log("Could not find login form fields", level="error", screenshot=True)
            raise BookingError(
                "Login form fields not found. The default CourtReserve selectors didn't match "
                "this facility's page -- inspect the login page manually and set "
                "'email_selectors'/'password_selectors' in the facility's adapter_config."
            )

        await email_field.fill(self.request.username)
        await password_field.fill(self.request.password)

        submit = await self._first_visible(self.config["login_submit_selectors"])
        if not submit:
            raise BookingError("Login submit button not found; adjust 'login_submit_selectors'.")
        await submit.click()

        indicator = await self._first_visible(self.config["post_login_indicator_selectors"], timeout_ms=8000)
        if not indicator:
            await self.log("No post-login indicator found after submitting credentials", level="error", screenshot=True)
            raise BookingError(
                "Login may have failed (no logout/account link appeared). Double-check the "
                "stored username/password, and verify 'post_login_indicator_selectors' matches "
                "something that actually appears on this facility's site once logged in."
            )
        await self.log("Logged in successfully", screenshot=True)

    async def navigate_to_booking_calendar(self) -> None:
        target = self._url("reservations_url")
        await self.log(f"Navigating to reservations page", screenshot=False)
        await self.page.goto(target, wait_until="domcontentloaded")

        # If the direct URL guess 404s or redirects somewhere unhelpful, fall back to
        # clicking a nav link by visible text.
        for text in self.config["reservations_nav_link_text"]:
            link = self.page.get_by_role("link", name=text)
            if await link.count() > 0:
                try:
                    await link.first.click(timeout=3000)
                    break
                except PlaywrightTimeoutError:
                    continue

        await self._set_target_date()
        await self.log(f"On booking calendar for {self.request.target_date.isoformat()}", screenshot=True)

    async def _set_target_date(self) -> None:
        target: date_cls = self.request.target_date
        date_input = await self._first_visible(self.config["date_input_selectors"], timeout_ms=3000)
        if date_input:
            try:
                await date_input.fill(target.strftime("%m/%d/%Y"))
                await self.page.keyboard.press("Enter")
                return
            except Exception:
                pass
        # Fallback: click "next day" repeatedly from today until the target date. Fragile,
        # but works even when the exact date-input selector is wrong, as long as the
        # next-day control is findable.
        today = date_cls.today()
        days_ahead = (target - today).days
        if days_ahead <= 0:
            return
        next_btn = await self._first_visible(self.config["next_day_selectors"], timeout_ms=3000)
        if not next_btn:
            raise BookingError(
                "Could not find a date picker or 'next day' control to reach the target date. "
                "Set 'date_input_selectors' or 'next_day_selectors' in adapter_config."
            )
        for _ in range(days_ahead):
            await next_btn.click()
            await self.page.wait_for_timeout(200)

    async def _locate_slot(self):
        wanted_time = self.request.start_time.strftime("%-I:%M %p") if hasattr(self.request.start_time, "strftime") else str(self.request.start_time)
        candidates = self.page.locator(self.config["slot_cell_selector"])
        count = await candidates.count()
        for i in range(count):
            cell = candidates.nth(i)
            try:
                text = (await cell.inner_text()).strip()
            except Exception:
                continue
            if wanted_time.lower() not in text.lower():
                continue
            if self.request.court_preference and self.request.court_preference.lower() != "any":
                if self.request.court_preference.lower() not in text.lower():
                    continue
            return cell
        return None

    async def slot_is_bookable(self) -> bool:
        page_text = (await self.page.content()).lower()
        if any(phrase in page_text for phrase in self.config["booking_window_closed_text"]):
            return False
        slot = await self._locate_slot()
        if slot is None:
            return False
        try:
            classes = (await slot.get_attribute("class")) or ""
        except Exception:
            classes = ""
        if any(bad in classes.lower() for bad in ["disabled", "unavailable", "booked"]):
            return False
        return True

    async def book_slot(self) -> str:
        slot = await self._locate_slot()
        if slot is None:
            raise SlotNotYetAvailable("Target slot not found on the calendar yet.")

        await slot.click()
        await self.log("Clicked target slot", screenshot=True)

        if self.request.dry_run:
            await self.log("DRY RUN: stopping before final confirm click", screenshot=True)
            return "Dry run reached the confirm step without submitting a real booking."

        confirm = await self._first_visible(self.config["confirm_button_selectors"], timeout_ms=5000)
        if not confirm:
            raise BookingError(
                "Slot was clicked but no confirm/book button appeared. Set "
                "'confirm_button_selectors' in adapter_config once you've seen the real modal/UI."
            )
        await confirm.click()

        page_text = (await self.page.content()).lower()
        if any(phrase in page_text for phrase in self.config["already_booked_text"]):
            raise BookingError("Facility reported the slot is already reserved/unavailable.")

        await self.log("Booking confirmed", screenshot=True)
        return f"Booked {self.request.court_preference or 'a court'} on {self.request.target_date} " \
               f"{self.request.start_time}-{self.request.end_time}."
