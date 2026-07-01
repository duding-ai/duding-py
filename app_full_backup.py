from typing import Optional, Set
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware


# ---------------------------------------------------------------------------
# Basic app + paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI()

# Session middleware (cookie-based auth)
app.add_middleware(SessionMiddleware, secret_key="CHANGE_ME_TO_A_RANDOM_SECRET")

# Static files + templates
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def get_leads_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(BASE_DIR / "leads.db"))
    conn.row_factory = sqlite3.Row
    return conn


def parse_created_to_utc(created) -> Optional[datetime]:
    """
    Safely parse a created_at / created value to a timezone-aware UTC datetime.
    Handles:
      - datetime objects
      - ISO strings with or without 'Z'
      - returns None on failure
    """
    if not created:
        return None

    if isinstance(created, datetime):
        if created.tzinfo is None:
            return created.replace(tzinfo=timezone.utc)
        return created.astimezone(timezone.utc)

    s = str(created).strip()
    if not s:
        return None

    # handle ...Z suffix
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    return dt


def require_session(request: Request) -> Optional[int]:
    """Return logged-in user_id or None if not authenticated."""
    user_id = request.session.get("user_id")
    if isinstance(user_id, int):
        return user_id
    try:
        return int(user_id)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public site routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def landing(
    request: Request, thanks: Optional[int] = None, error: Optional[int] = None
):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "thanks": bool(thanks),
            "error": bool(error),
        },
    )


@app.post("/lead")
async def submit_lead(request: Request):
    form = await request.form()

    # Honeypot (bots fill this)
    website = (form.get("website") or "").strip()
    if website:
        return RedirectResponse(url="/?thanks=1", status_code=303)

    name = (form.get("name") or "").strip()
    business = (form.get("business") or "").strip()
    email = (form.get("email") or "").strip().lower()
    budget = (form.get("budget") or "").strip()
    link = (form.get("link") or "").strip()
    role = (form.get("role") or "").strip()
    cadence = (form.get("cadence") or "").strip()
    recent = (form.get("recent") or "").strip()

    created_at = datetime.now(timezone.utc).isoformat()

    try:
        conn = get_leads_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO leads
                (created_at, name, email, business, budget, link, role,
                 cadence, recent, status, run_count, last_run)
            VALUES
                (?,          ?,    ?,     ?,        ?,      ?,    ?,
                 ?,       ?,      ?,      ?,         ?)
            """,
            (
                created_at,
                name,
                email,
                business,
                budget,
                link,
                role,
                cadence,
                recent,
                "new",
                0,
                None,
            ),
        )
        conn.commit()
    except Exception as exc:
        print("[lead] Error inserting lead:", exc)
        return RedirectResponse(url="/?error=1", status_code=303)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return RedirectResponse(url="/?thanks=1", status_code=303)


# ---------------------------------------------------------------------------
# Auth routes  (NO hashing, NO users.db – fixed admin credentials)
# ---------------------------------------------------------------------------

ADMIN_EMAIL = "admin@duding.ai"
ADMIN_PASSWORD = "Mckinnon25$"


@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": None,
        },
    )


@app.post("/login")
async def login_post(request: Request):
    """
    Super-simple login:
        email must be ADMIN_EMAIL
        password must be ADMIN_PASSWORD
    No database, no hashing – avoids all malformed-hash errors.
    """
    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    password = (form.get("password") or "").strip()

    if not (email == ADMIN_EMAIL and password == ADMIN_PASSWORD):
        # Invalid
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Invalid credentials.",
            },
            status_code=401,
        )

    # Success – we just use user_id = 1 and the admin email
    request.session["user_id"] = 1
    request.session["username"] = ADMIN_EMAIL

    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


# ---------------------------------------------------------------------------
# Admin / protected routes
# ---------------------------------------------------------------------------


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    rows = []
    total_leads = 0
    last_7_days = 0
    unique_emails: Set[str] = set()

    try:
        conn = get_leads_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                id,
                created_at AS created,
                name,
                email,
                business,
                budget,
                COALESCE(status, 'new') AS status,
                COALESCE(run_count, 0) AS run_count,
                last_run
            FROM leads
            ORDER BY id DESC
            """
        )
        rows = cur.fetchall()
    except Exception as exc:
        print("[dashboard] DB error:", exc)
        rows = []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    total_leads = len(rows)

    now_utc = datetime.now(timezone.utc)
    seven_days_ago = now_utc - timedelta(days=7)

    for r in rows:
        email = r["email"]
        if email:
            unique_emails.add(email.lower())

        created_dt = parse_created_to_utc(r["created"])
        if created_dt and created_dt >= seven_days_ago:
            last_7_days += 1

    stats = {
        "total": total_leads,
        "last_7_days": last_7_days,
        "unique_emails": len(unique_emails),
    }

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user_id": user_id,
            "leads": rows,
            "stats": stats,
        },
    )


@app.get("/automation", response_class=HTMLResponse)
async def automation(request: Request):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    rows = []
    try:
        conn = get_leads_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                id,
                created_at AS created,
                name,
                email,
                business,
                budget,
                COALESCE(status, 'new') AS status,
                COALESCE(run_count, 0) AS run_count,
                last_run
            FROM leads
            ORDER BY id DESC
            """
        )
        rows = cur.fetchall()
    except Exception as exc:
        print("[automation] DB error:", exc)
        rows = []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return templates.TemplateResponse(
        "automation.html",
        {
            "request": request,
            "user_id": user_id,
            "leads": rows,
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
