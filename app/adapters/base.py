"""
Adapter interface that every booking-platform integration implements.

Design goal: the scheduler/worker never knows anything about CourtReserve,
PlayByPoint, etc. It only calls these five methods. That's what lets one
BookingJob work against "whatever platform this facility happens to run on".

Every adapter is driven by Playwright so it can automate a real browser
session against a facility's actual website rather than needing a private
API integration per platform.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import date, time
from typing import Callable

from playwright.async_api import Browser, BrowserContext, Page


@dataclass
class BookingRequest:
    base_url: str
    portal_id: str | None
    username: str
    password: str
    member_id: str | None
    target_date: date
    start_time: time
    end_time: time
    court_preference: str | None
    dry_run: bool
    adapter_config_json: str | None = None
    # Called with (level, message, screenshot_path | None) for every notable step.
    log: Callable[[str, str, str | None], None] = field(default=lambda level, msg, shot: None)


class BookingError(Exception):
    """Raised by an adapter when a step fails. Message should be worth showing a human."""


class SlotNotYetAvailable(BookingError):
    """Raised when the target slot exists but the booking window hasn't opened yet.

    The worker treats this specially: it will keep retrying in a tight loop for a
    short grace period around the expected opening time, since server clocks and
    real-world "midnight" can be off by a second or two.
    """


class BookingAdapter(abc.ABC):
    """One instance is created per job attempt and used for exactly one booking flow."""

    def __init__(self, request: BookingRequest, page: Page, context: BrowserContext, browser: Browser):
        self.request = request
        self.page = page
        self.context = context
        self.browser = browser

    async def log(self, message: str, level: str = "info", screenshot: bool = False) -> None:
        """Record a step. Pass screenshot=True on steps worth debugging visually later."""
        shot_path = await self._snapshot(message) if screenshot else None
        self.request.log(level, message, shot_path)

    async def _snapshot(self, label: str) -> str | None:
        """Best-effort screenshot for debugging; failures here should never crash a job."""
        import os
        import re
        import time as _time

        try:
            from app.config import get_settings

            settings = get_settings()
            os.makedirs(settings.screenshot_dir, exist_ok=True)
            safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "-", label)[:60]
            path = os.path.join(settings.screenshot_dir, f"{int(_time.time() * 1000)}-{safe_label}.png")
            await self.page.screenshot(path=path, full_page=True)
            return path
        except Exception:
            return None

    @abc.abstractmethod
    async def login(self) -> None:
        """Authenticate against the facility site. Raise BookingError on failure."""

    @abc.abstractmethod
    async def navigate_to_booking_calendar(self) -> None:
        """Get to the page that shows available slots for self.request.target_date."""

    @abc.abstractmethod
    async def slot_is_bookable(self) -> bool:
        """Return True once the target slot can actually be clicked/booked.

        Should return False (not raise) while the booking window is still closed --
        the worker uses this to spin-wait right up to and past the opening instant.
        """

    @abc.abstractmethod
    async def book_slot(self) -> str:
        """Perform the actual booking. Returns a human-readable confirmation string.

        Must NOT be called unless self.request.dry_run is False. When dry_run is True
        the worker calls this only far enough to confirm the slot is clickable, then
        stops before the final irreversible submit -- see CourtReserveAdapter for the
        pattern to follow when adding a new platform.
        """
