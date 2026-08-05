# Court Booking Bot

A small multi-user web app that schedules court reservations to fire the instant a
facility's booking window opens (e.g. "book Thursday 10am-12pm at Cali Smash the
moment its 7-day-ahead window opens at midnight").

It is **not** hardcoded to one facility or one booking platform. A `Facility` row just
says which platform it runs on (currently: CourtReserve) plus its own portal id, time
zone, and how many days in advance it opens bookings. Each user attaches their own
login credentials to a facility, then creates `BookingJob`s ("book me this date/time
at this facility") that the scheduler fires automatically.

## Read this first: risk and legal disclaimer

Automating login and booking against a third-party website may violate that site's or
facility's Terms of Service, and some platforms actively detect and block bot traffic
(rate limits, CAPTCHAs, IP bans, account suspension). You are running this against
your own accounts at your own risk. Nothing here bypasses a CAPTCHA or any other
bot-detection mechanism -- if a facility's site presents one, the job will fail and
log the error rather than attempt to defeat it.

## How the timing works

1. Each `Facility` has a `booking_window_days` (default 7) and a `timezone`.
2. When you create a job for a target reservation date, the app computes the exact
   UTC instant the window opens: `target_date - booking_window_days`, at `00:00:00`
   in the facility's own timezone.
3. `PREWARM_LEAD_SECONDS` (default 90) before that instant, a worker opens a real
   browser, logs in, and navigates to the target date's calendar -- so login/page-load
   latency doesn't eat into your shot at a contested slot.
4. The worker then spin-waits to the exact instant and retries booking for up to
   ~25 seconds afterward (`POST_OPEN_GRACE_SECONDS` in `app/worker.py`), since no two
   clocks (yours, the server's, the facility's) agree to the millisecond.
5. Every step is logged (with screenshots) to the job's detail page so you can see
   exactly what happened if a booking fails.

## Adding a new facility (including non-CourtReserve platforms)

Most facilities you'll encounter that use CourtReserve (`app.courtreserve.com/Online/Portal/Index/<id>`)
can reuse the existing adapter -- just add a `Facility` with `platform=courtreserve`,
the base URL, and the portal id from the site's own URL.

For a platform this app doesn't support yet:

1. Create `app/adapters/<platform>.py` with a class implementing the `BookingAdapter`
   interface in `app/adapters/base.py` (`login`, `navigate_to_booking_calendar`,
   `slot_is_bookable`, `book_slot`).
2. Register it in `app/adapters/registry.py`.
3. Add the platform to the `Platform` enum in `app/models.py` and the `<select>` in
   `app/templates/facilities.html`.

## Calibrating a new facility (important)

The CourtReserve adapter (`app/adapters/courtreserve.py`) was written without access
to a live, logged-in CourtReserve session, so its selectors are best-effort defaults,
not verified against a real facility's DOM. Before relying on it for a real booking
window:

1. Create the facility and save your login.
2. Create a job for any near-future date with **Dry run** checked. Dry run logs in,
   navigates, locates the slot, and clicks it, but stops before the final confirm --
   it never places a real reservation.
3. Open the job's detail page and read the log + screenshots (saved under
   `data/screenshots/`). Each failed step tells you which selector list didn't match.
4. Fix mismatches by setting a facility's `adapter_config` (a JSON blob, currently
   editable directly in the database -- there's no UI for it yet) to override any key
   in `DEFAULT_CONFIG` at the top of `courtreserve.py`, e.g.:
   ```json
   {"email_selectors": ["input#LoginEmail"], "reservations_url": "{base_url}/Online/Reservations/Bookings/{portal_id}"}
   ```
5. Re-run the dry run until it cleanly reaches "clicked target slot" in the log, then
   let a real job run.

## Running locally (development)

```bash
cd court-booking-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # then fill in SESSION_SECRET_KEY and CREDENTIAL_ENCRYPTION_KEY
uvicorn app.main:app --reload
```

Visit http://localhost:8000. SQLite (`app.db`) is used automatically if `DATABASE_URL`
isn't set to a Postgres URL.

## Deploying (so jobs fire even when your laptop is off)

`docker-compose.yml` bundles the app, a Postgres database, and installs Playwright's
Chromium inside the image. On any small VPS (DigitalOcean, Hetzner, Fly.io, etc.):

```bash
git clone <this repo> && cd court-booking-bot
cp .env.example .env   # fill in real secrets
docker compose up -d --build
```

The app serves on port 8000 (put it behind a reverse proxy like Caddy/nginx with TLS
if you're exposing it to the internet -- session cookies are not marked `Secure` by
default for local dev; set that up before exposing this publicly with real
credentials in play).

## Preventing a stuck job from taking down the whole server

Headless Chromium can use several hundred MB. On a small droplet already running other
services, a stuck browser launch can exhaust memory badly enough to freeze the entire
box, not just this app. Two layers of defense:

1. **Code-level (`app/worker.py`):** browser launch, login, and navigation are all wrapped
   in timeouts (`LAUNCH_TIMEOUT_SECONDS`, `LOGIN_TIMEOUT_SECONDS`, `NAVIGATE_TIMEOUT_SECONDS`),
   and the browser is always closed in a `finally` block. A hung step now fails the job
   cleanly instead of hanging forever. `restore_pending_jobs()` also only auto-resumes jobs
   that never started (`scheduled`); a job that was mid-attempt (`prewarming`/`running`) when
   the process died is marked failed rather than blindly retried on every restart.
2. **Infrastructure-level (systemd cgroup limit):** add a hard memory ceiling to the unit so
   the kernel kills *this service* if it goes over, instead of the whole system thrashing
   into unresponsiveness. Edit `/etc/systemd/system/court-booking-bot.service` and add under
   `[Service]`:
   ```ini
   MemoryMax=768M
   MemoryHigh=512M
   ```
   Then `sudo systemctl daemon-reload && sudo systemctl restart court-booking-bot`. Tune the
   numbers to your droplet's actual free RAM (leave headroom for whatever else runs on the
   same box). If Chromium actually needs more than this ceiling to run at all, that's a sign
   the droplet itself is undersized for running a real browser -- add swap or resize rather
   than raising the limit indefinitely.

## Known limitations / good next steps

- Single process handles both the web UI and the scheduler; fine for a hobby project,
  but a crash restarts both.
- No email/SMS notification on job success/failure yet -- check the job detail page.
- No UI for editing `Facility.adapter_config` yet; edit it directly in the database.
- The CourtReserve selectors need the calibration pass described above before your
  first real (non-dry-run) job.
- Only one job runs at a time in-process right now -- if you end up scheduling many
  concurrent jobs across facilities, consider capping concurrency explicitly rather than
  relying on the timeouts above as the only guard rail.
