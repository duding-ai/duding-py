from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import sqlite3

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


app.add_middleware(
    SessionMiddleware,
    secret_key="CHANGE_ME_TO_A_LONG_RANDOM_STRING_32+_CHARS",
    session_cookie="duding_session",
    max_age=60 * 60 * 24 * 7,  # 7 days
    same_site="lax",  # good default
    https_only=False,  # keep False for local dev; set True in production over HTTPS
)


# -----------------------------------
# Database setup
# -----------------------------------
def init_db():
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            password TEXT
        )
        """
    )
    conn.commit()
    conn.close()


# ⬇️ THIS LINE MUST START IN COLUMN 0 (no spaces)
init_db()


# -----------------------------------
# Setup Route (Sign Up)
# -----------------------------------
@app.get("/setup", response_class=HTMLResponse)
async def setup_form(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/setup", response_class=HTMLResponse)
async def setup_user(request: Request, user: str = Form(...), passw: str = Form(...)):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (username, password) VALUES (?, ?)", (user, passw)
    )

    conn.commit()
    conn.close()

    # ✅ THIS return must be indented to stay inside the functionS
    return HTMLResponse(f"<h2>User {user} created! Go to /auth/login to log in.</h2>")


from fastapi import Form


@app.post("/setup")
async def setup_user(username: str = Form(...), password: str = Form(...)):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (username, password) VALUES (?, ?)", (username, password)
    )
    conn.commit()
    conn.close()
    return {"message": f"User {username} created successfully!"}


from fastapi import Form


# Login form (GET)
@app.get("/auth/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


# Login submisson (POST)
@app.post("/auth/login")
async def login_user(
    request: Request, username: str = Form(...), password: str = Form(...)
):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM users WHERE username=? AND password=?", (username, password)
    )
    user = cursor.fetchone()
    conn.close()

    if user:
        # Save user in session and go to dashboard
        request.session["user"] = username
        return RedirectResponse(url="/dashboard", status_code=303)
    else:
        # Show error inline + link retry login
        return HTMLResponse(
            "<h3>Invalid username or password</h3>"
            "<p><a href='/auth/login'>Try again</a></p>"
        )

    # Dashboard route (protected page)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = request.session.get("user", {"first_name": "Tommy"})  # temporary fallback
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "brand": {
                "name": "Duding",
                "tagline": "Create the Culture · Share the Story · Scale the Brand",
            },
            "user": user,
        },
    )
