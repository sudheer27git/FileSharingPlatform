import hashlib
import hmac
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import psycopg
import razorpay
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE_MB", "25")) * 1024 * 1024
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()

if not DATABASE_URL or not SESSION_SECRET:
    raise RuntimeError("Set DATABASE_URL and SESSION_SECRET in your .env file.")

razorpay_client = (
    razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET else None
)

PLANS = {
    "starter": {"name": "Starter", "amount": 0, "currency": "INR", "storage_mb": 250},
    "pro": {"name": "Pro", "amount": 19900, "currency": "INR", "storage_mb": 10240},
    "team": {"name": "Team", "amount": 49900, "currency": "INR", "storage_mb": 51200},
}

@asynccontextmanager
async def lifespan(app: FastAPI):
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                username VARCHAR(40) NOT NULL UNIQUE,
                email VARCHAR(254) NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                plan VARCHAR(20) NOT NULL DEFAULT 'starter',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id UUID PRIMARY KEY,
                owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL UNIQUE,
                content_type TEXT NOT NULL,
                size_bytes BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id BIGSERIAL PRIMARY KEY,
                owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                plan VARCHAR(20) NOT NULL,
                amount_paise BIGINT NOT NULL,
                currency VARCHAR(3) NOT NULL DEFAULT 'INR',
                razorpay_order_id TEXT UNIQUE NOT NULL,
                razorpay_payment_id TEXT UNIQUE,
                status VARCHAR(20) NOT NULL DEFAULT 'created',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                paid_at TIMESTAMPTZ
            )
        """)
    yield

app = FastAPI(title="FileNest — File Sharing Platform", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="filenest_session",
    same_site="lax",
    https_only=False,  # localhost development only; set True behind HTTPS in production
    max_age=60 * 60 * 4,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

def get_db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"{salt.hex()}${digest.hex()}"

def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), 310_000
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False

def current_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in.")
    with get_db() as conn:
        user = conn.execute(
            "SELECT id, username, email, plan FROM users WHERE id=%s", (user_id,)
        ).fetchone()
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Session expired. Sign in again.")
    return user

def clean_filename(name: str) -> str:
    name = (name or "uploaded-file").replace("\\", "/").split("/")[-1]
    name = "".join(c for c in name if c.isprintable()).strip().strip(".")
    return (name or "uploaded-file")[:255]

def json_user(user):
    return {"id": user["id"], "username": user["username"], "email": user["email"],
            "plan": user["plan"], "plan_name": PLANS.get(user["plan"], PLANS["starter"])["name"]}

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})

@app.get("/api/plans")
def get_plans():
    return [
        {"id": key, **value, "amount_rupees": value["amount"] / 100}
        for key, value in PLANS.items()
    ]

@app.get("/api/me")
def me(request: Request):
    user = current_user(request)
    if not request.session.get("csrf"):
        request.session["csrf"] = secrets.token_urlsafe(32)
    return {"user": json_user(user), "csrf": request.session["csrf"]}

@app.post("/api/signup")
def signup(request: Request, username: str = Form(...), email: str = Form(...),
           password: str = Form(...)):
    username, email = username.strip(), email.strip().lower()
    if not 3 <= len(username) <= 40:
        raise HTTPException(400, "Username must be 3–40 characters.")
    if len(password) < 10 or len(password) > 128:
        raise HTTPException(400, "Password must be 10–128 characters.")
    if "@" not in email or len(email) > 254:
        raise HTTPException(400, "Enter a valid email address.")
    try:
        with get_db() as conn:
            user = conn.execute(
                """INSERT INTO users(username,email,password_hash)
                   VALUES(%s,%s,%s) RETURNING id,username,email,plan""",
                (username, email, hash_password(password)),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "That username or email is already registered.")
    request.session.clear()
    request.session["user_id"] = user["id"]
    request.session["csrf"] = secrets.token_urlsafe(32)
    return {"message": "Account created.", "user": json_user(user),
            "csrf": request.session["csrf"]}

@app.post("/api/signin")
def signin(request: Request, username: str = Form(...), password: str = Form(...)):
    with get_db() as conn:
        user = conn.execute(
            """SELECT id,username,email,password_hash,plan FROM users
               WHERE LOWER(username)=LOWER(%s) OR LOWER(email)=LOWER(%s)""",
            (username.strip(), username.strip()),
        ).fetchone()
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(401, "Incorrect username/email or password.")
    request.session.clear()
    request.session["user_id"] = user["id"]
    request.session["csrf"] = secrets.token_urlsafe(32)
    return {"message": "Signed in.", "user": json_user(user),
            "csrf": request.session["csrf"]}

@app.post("/api/signout")
def signout(request: Request):
    request.session.clear()
    return {"message": "Signed out."}

def check_csrf(request: Request, supplied: Optional[str]):
    expected = request.session.get("csrf")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(403, "Session security token is missing or invalid. Refresh and sign in again.")

@app.get("/api/files")
def list_files(request: Request):
    user = current_user(request)
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id,original_name,content_type,size_bytes,created_at
               FROM files WHERE owner_id=%s ORDER BY created_at DESC""",
            (user["id"],),
        ).fetchall()
    return [{"id": str(r["id"]), "name": r["original_name"],
             "content_type": r["content_type"], "size_bytes": r["size_bytes"],
             "created_at": r["created_at"].isoformat()} for r in rows]

@app.post("/api/files/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    user = current_user(request)
    check_csrf(request, request.headers.get("X-CSRF-Token"))
    original_name = clean_filename(file.filename)
    file_id = uuid.uuid4()
    stored_name = f"{file_id}.bin"
    path = UPLOAD_DIR / stored_name
    size = 0
    try:
        with path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise HTTPException(413, f"Maximum file size is {MAX_FILE_SIZE // (1024*1024)} MB.")
                out.write(chunk)
        if size == 0:
            raise HTTPException(400, "Empty files are not allowed.")
        with get_db() as conn:
            conn.execute(
                """INSERT INTO files(id,owner_id,original_name,stored_name,content_type,size_bytes)
                   VALUES(%s,%s,%s,%s,%s,%s)""",
                (file_id, user["id"], original_name, stored_name,
                 file.content_type or "application/octet-stream", size),
            )
        return {"message": "File uploaded.", "id": str(file_id)}
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

@app.get("/api/files/{file_id}/download")
def download_file(request: Request, file_id: uuid.UUID):
    user = current_user(request)
    with get_db() as conn:
        row = conn.execute(
            """SELECT original_name,stored_name,content_type FROM files
               WHERE id=%s AND owner_id=%s""", (file_id, user["id"])
        ).fetchone()
    if not row:
        raise HTTPException(404, "File not found.")
    path = UPLOAD_DIR / row["stored_name"]
    if not path.is_file():
        raise HTTPException(404, "Stored file is missing.")
    return FileResponse(path, filename=row["original_name"],
                        media_type=row["content_type"],
                        content_disposition_type="attachment")

@app.delete("/api/files/{file_id}")
def delete_file(request: Request, file_id: uuid.UUID):
    user = current_user(request)
    check_csrf(request, request.headers.get("X-CSRF-Token"))
    with get_db() as conn:
        row = conn.execute(
            """DELETE FROM files WHERE id=%s AND owner_id=%s
               RETURNING stored_name""", (file_id, user["id"])
        ).fetchone()
    if not row:
        raise HTTPException(404, "File not found.")
    (UPLOAD_DIR / row["stored_name"]).unlink(missing_ok=True)
    return {"message": "File deleted."}

@app.get("/api/payments/config")
def payment_config(request: Request):
    current_user(request)
    return {"enabled": bool(razorpay_client), "key_id": RAZORPAY_KEY_ID if razorpay_client else ""}

@app.post("/api/payments/create-order")
def create_payment_order(request: Request, plan_id: str = Form(...)):
    user = current_user(request)
    check_csrf(request, request.headers.get("X-CSRF-Token"))
    if plan_id not in PLANS or plan_id == "starter":
        raise HTTPException(400, "Choose a paid plan.")
    if not razorpay_client:
        raise HTTPException(503, "Payments are not configured. Add Razorpay TEST keys to .env.")
    plan = PLANS[plan_id]
    try:
        order = razorpay_client.order.create({
            "amount": plan["amount"], "currency": plan["currency"],
            "receipt": f"u{user['id']}-{uuid.uuid4().hex[:18]}",
            "notes": {"user_id": str(user["id"]), "plan_id": plan_id}
        })
    except Exception:
        raise HTTPException(502, "Could not create a Razorpay order. Check test keys and connectivity.")
    with get_db() as conn:
        conn.execute(
            """INSERT INTO payments(owner_id,plan,amount_paise,currency,razorpay_order_id,status)
               VALUES(%s,%s,%s,%s,%s,'created')""",
            (user["id"], plan_id, plan["amount"], plan["currency"], order["id"]),
        )
    return {"order_id": order["id"], "amount": plan["amount"],
            "currency": plan["currency"], "key_id": RAZORPAY_KEY_ID,
            "plan_name": plan["name"], "user": {"name": user["username"], "email": user["email"]}}

@app.post("/api/payments/verify")
def verify_payment(request: Request, razorpay_order_id: str = Form(...),
                   razorpay_payment_id: str = Form(...),
                   razorpay_signature: str = Form(...)):
    user = current_user(request)
    check_csrf(request, request.headers.get("X-CSRF-Token"))
    if not RAZORPAY_KEY_SECRET:
        raise HTTPException(503, "Payments are not configured.")
    with get_db() as conn:
        payment = conn.execute(
            """SELECT id,plan,status FROM payments
               WHERE owner_id=%s AND razorpay_order_id=%s""",
            (user["id"], razorpay_order_id),
        ).fetchone()
    if not payment:
        raise HTTPException(404, "Payment order not found.")
    if payment["status"] == "paid":
        return {"message": "Payment already verified."}
    message = f"{razorpay_order_id}|{razorpay_payment_id}".encode()
    expected = hmac.new(RAZORPAY_KEY_SECRET.encode(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, razorpay_signature):
        raise HTTPException(400, "Payment signature verification failed.")
    with get_db() as conn:
        conn.execute(
            """UPDATE payments SET status='paid',razorpay_payment_id=%s,paid_at=NOW()
               WHERE id=%s""", (razorpay_payment_id, payment["id"])
        )
        conn.execute("UPDATE users SET plan=%s WHERE id=%s", (payment["plan"], user["id"]))
    return {"message": "Payment verified. Your plan is now active."}

@app.post("/api/payments/webhook")
async def payment_webhook(request: Request):
    # Configure this endpoint in Razorpay with the exact webhook secret.
    if not RAZORPAY_WEBHOOK_SECRET:
        raise HTTPException(503, "Webhook secret is not configured.")
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    expected = hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise HTTPException(400, "Invalid webhook signature.")
    import json
    try:
        event = json.loads(body)
        if event.get("event") == "payment.captured":
            entity = event["payload"]["payment"]["entity"]
            order_id = entity.get("order_id")
            payment_id = entity.get("id")
            with get_db() as conn:
                payment = conn.execute(
                    "SELECT owner_id,plan FROM payments WHERE razorpay_order_id=%s",
                    (order_id,),
                ).fetchone()
                if payment:
                    conn.execute(
                        """UPDATE payments SET status='paid',razorpay_payment_id=%s,paid_at=COALESCE(paid_at,NOW())
                           WHERE razorpay_order_id=%s""", (payment_id, order_id)
                    )
                    conn.execute("UPDATE users SET plan=%s WHERE id=%s",
                                 (payment["plan"], payment["owner_id"]))
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "Malformed webhook payload.")
    return {"received": True}

@app.get("/api/payments/history")
def payment_history(request: Request):
    user = current_user(request)
    with get_db() as conn:
        rows = conn.execute(
            """SELECT plan,amount_paise,currency,status,razorpay_order_id,created_at,paid_at
               FROM payments WHERE owner_id=%s ORDER BY created_at DESC""",
            (user["id"],),
        ).fetchall()
    return [{"plan": r["plan"], "amount": r["amount_paise"]/100,
             "currency": r["currency"], "status": r["status"],
             "order_id": r["razorpay_order_id"], "created_at": r["created_at"].isoformat(),
             "paid_at": r["paid_at"].isoformat() if r["paid_at"] else None} for r in rows]
