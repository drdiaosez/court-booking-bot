from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import auth, facilities, jobs
from app.config import get_settings
from app.database import SessionLocal, init_db
from app.models import User
from app.scheduler import restore_pending_jobs, scheduler

settings = get_settings()
templates = Jinja2Templates(directory="app/templates")


def _current_user(request: Request) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with SessionLocal() as db:
        return db.get(User, user_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.start()
    restore_pending_jobs()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Court Booking Bot", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret_key)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth.router)
app.include_router(facilities.router)
app.include_router(jobs.router)


@app.get("/")
def home(request: Request):
    user = _current_user(request)
    if not user:
        return RedirectResponse("/login")
    return RedirectResponse("/jobs")


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/signup")
def signup_page(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request})


@app.get("/facilities")
def facilities_page(request: Request):
    user = _current_user(request)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("facilities.html", {"request": request, "user": user})


@app.get("/credentials")
def credentials_page(request: Request):
    user = _current_user(request)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("credentials.html", {"request": request, "user": user})


@app.get("/jobs")
def jobs_page(request: Request):
    user = _current_user(request)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("jobs.html", {"request": request, "user": user})


@app.get("/jobs/new")
def job_new_page(request: Request):
    user = _current_user(request)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("job_new.html", {"request": request, "user": user})


@app.get("/jobs/{job_id}")
def job_detail_page(request: Request, job_id: int):
    user = _current_user(request)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("job_detail.html", {"request": request, "user": user, "job_id": job_id})
