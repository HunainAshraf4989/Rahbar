"""PostgreSQL account routes added to the existing lecture application."""

import hashlib
import hmac
import os
import re
import secrets
import time
import unicodedata
from contextlib import closing, contextmanager
from pathlib import Path
from threading import BoundedSemaphore, Lock
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg2
from dotenv import dotenv_values
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, SecretStr

from rahbar.api import create_app
from rahbar.config import load_supabase_settings
from rahbar.contracts.http import Account
from rahbar.registration import RegistrationCredentials

COOKIE = "rahbar_session"
SESSION_SECONDS = 8 * 3600


class Credentials(BaseModel):
    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)


class PasswordChange(BaseModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=1, max_length=128)


def token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def create_platform_app(*, project_root: Path | None = None,
                        lecture_directory: Path | None = None) -> FastAPI:
    root = (project_root or Path.cwd()).resolve()
    # Cloud cache filenames include checksums and can exceed MAX_PATH in nested checkouts.
    if os.name == "nt" and not str(root).startswith("\\\\?\\"):
        root_text = str(root)
        root = Path("\\\\?\\UNC\\" + root_text[2:] if root_text.startswith("\\\\") else "\\\\?\\" + root_text)
    app = create_app(project_root=root, lecture_directory=lecture_directory)
    settings = {**dotenv_values(".env"), **dotenv_values(".env.local"), **os.environ}
    dsn = load_supabase_settings(Path.cwd()).database_url
    origin = settings.get("RAHBAR_WEB_ORIGIN") or "http://127.0.0.1:4173"
    url = urlsplit(origin)
    secure = url.scheme == "https"
    if not secure and (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}):
        raise ValueError("Account origin must use HTTPS outside localhost")
    router = APIRouter(prefix="/api/v1")
    auth_lock = Lock()
    hash_slots = BoundedSemaphore(2)
    dummy_salt = secrets.token_bytes(16)
    window = 0
    attempts: dict[tuple[str, ...], int] = {}
    blocked = {"passwordpassword", "password123456789", "123456789012345", "qwertyuiopasdfgh"}
    if settings.get("RAHBAR_PASSWORD_BLOCKLIST"):
        path = Path(str(settings["RAHBAR_PASSWORD_BLOCKLIST"]))
        blocked.update(word.strip().casefold() for word in path.read_text(encoding="utf-8").splitlines())

    @contextmanager
    def database():
        if not dsn:
            raise HTTPException(503, "Account database is unavailable")
        with closing(psycopg2.connect(dsn, connect_timeout=5, sslmode="require")) as connection:
            with connection, connection.cursor() as cursor:
                yield cursor

    def require_origin(request: Request) -> None:
        if request.headers.get("origin") != origin:
            raise HTTPException(403, "Request origin is not allowed")

    def limit(request: Request, operation: str, email: str) -> None:
        nonlocal window
        now = int(time.monotonic() // 60)
        ip = request.client.host if request.client else "unknown"
        with auth_lock:
            if now != window:
                attempts.clear()
                window = now
            for key, maximum in [((operation,), 60), ((operation, ip), 20),
                                 ((operation, "account", email), 5)]:
                count = attempts.get(key, 0)
                if count >= maximum:
                    raise HTTPException(429, "Try again in a minute", headers={"Retry-After": "60"})
                attempts[key] = count + 1

    def password_digest(password: str, salt: bytes) -> bytes:
        if not hash_slots.acquire(blocking=False):
            raise HTTPException(429, "Try again shortly")
        try:
            normalized = unicodedata.normalize("NFC", password).encode()
            return hashlib.scrypt(normalized, salt=salt, n=2**17, r=8, p=1,
                                  maxmem=160 * 1024**2, dklen=32)
        finally:
            hash_slots.release()

    def new_hash(password: str, email: str) -> str:
        normalized = unicodedata.normalize("NFC", password)
        if (len(normalized) < 8 or not any(char.isupper() for char in normalized)
                or not re.search(r"[^\w\s]|_", normalized)
                or normalized.casefold() in blocked | {email, email.split("@")[0]}):
            raise HTTPException(400, "Use at least 8 characters, an uppercase letter and a special character")
        salt = secrets.token_bytes(16)
        return "scrypt-v1:" + salt.hex() + ":" + password_digest(normalized, salt).hex()

    def verify(password: str, encoded: str | None) -> bool:
        salt, expected = dummy_salt, bytes(32)
        valid = False
        if encoded:
            try:
                version, salt_hex, hash_hex = encoded.split(":")
                if version == "scrypt-v1" and len(salt_hex) == 32 and len(hash_hex) == 64:
                    salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(hash_hex)
                    valid = True
            except ValueError:
                pass
        matches = hmac.compare_digest(password_digest(password, salt), expected)
        return matches and valid

    def revoke(db, token: str | None) -> None:
        if token:
            db.execute("UPDATE rahbar.auth_sessions SET revoked_at = now() "
                       "WHERE token_digest = %s AND revoked_at IS NULL", (token_digest(token),))

    def issue_session(db, request: Request, response: Response, principal) -> None:
        revoke(db, request.cookies.get(COOKIE))
        token = secrets.token_urlsafe(32)
        db.execute("INSERT INTO rahbar.auth_sessions (principal_id, token_digest, expires_at) "
                   "VALUES (%s, %s, now() + make_interval(secs => %s))",
                   (principal, token_digest(token), SESSION_SECONDS))
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, path="/",
                            httponly=True, secure=secure, samesite="strict")

    @app.exception_handler(psycopg2.Error)
    async def database_error(_request: Request, _error: psycopg2.Error):
        return JSONResponse({"detail": "Account database is unavailable"}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        if request.url.path in {"/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/auth/password"}:
            return JSONResponse({"detail": "Invalid credentials format"}, status_code=422)
        return await request_validation_exception_handler(request, error)

    @app.middleware("http")
    async def account_responses(request: Request, call_next):
        path = request.url.path
        if not (path.startswith("/api/v1/auth/") or path == "/api/v1/me"):
            return await call_next(request)
        if path in {"/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/auth/password"}:
            length = request.headers.get("content-length", "")
            if not length.isascii() or not length.isdecimal() or len(length) > 4 or int(length) > 4096:
                return JSONResponse({"detail": "Invalid request size"}, status_code=413,
                                    headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post("/auth/register", status_code=201)
    def register(payload: RegistrationCredentials, request: Request, response: Response) -> Account:
        require_origin(request)
        email = str(payload.email).lower()
        limit(request, "register", email)
        principal = str(uuid4())
        encoded = new_hash(payload.password.get_secret_value(), email)
        try:
            with database() as db:
                db.execute("INSERT INTO rahbar.learning_principals "
                           "(principal_id, host_key, subject_key) VALUES (%s, 'web', %s)",
                           (principal, principal))
                db.execute("INSERT INTO rahbar.learner_accounts "
                           "(principal_id, email, password_hash, full_name, phone_number) "
                           "VALUES (%s, %s, %s, %s, %s)",
                           (principal, email, encoded, payload.full_name, payload.phone_number))
                issue_session(db, request, response, principal)
        except psycopg2.errors.UniqueViolation:
            raise HTTPException(409, "Unable to register this account") from None
        return Account(account_id=str(principal), email=email,
                       full_name=payload.full_name, phone_number=payload.phone_number)

    @router.post("/auth/login")
    def login(payload: Credentials, request: Request, response: Response) -> Account:
        require_origin(request)
        email = str(payload.email).lower()
        limit(request, "login", email)
        with database() as db:
            db.execute("SELECT principal_id, password_hash, full_name, phone_number "
                       "FROM rahbar.learner_accounts WHERE lower(email) = %s AND disabled_at IS NULL FOR UPDATE", (email,))
            row = db.fetchone()
            if not verify(payload.password.get_secret_value(), row[1] if row else None) or not row:
                raise HTTPException(401, "Email or password is incorrect")
            issue_session(db, request, response, row[0])
        return Account(account_id=str(row[0]), email=email, full_name=row[2], phone_number=row[3])

    @router.get("/me")
    def me(request: Request) -> Account:
        token = request.cookies.get(COOKIE)
        if not token:
            raise HTTPException(401, "Sign in required")
        with database() as db:
            db.execute(
                "SELECT a.principal_id, a.email, a.full_name, a.phone_number FROM rahbar.auth_sessions s "
                "JOIN rahbar.learner_accounts a USING (principal_id) "
                "WHERE s.token_digest = %s AND s.revoked_at IS NULL "
                "AND s.expires_at > now() AND a.disabled_at IS NULL", (token_digest(token),)
            )
            row = db.fetchone()
        if not row:
            raise HTTPException(401, "Sign in required")
        return Account(account_id=str(row[0]), email=row[1], full_name=row[2], phone_number=row[3])

    @router.post("/auth/password", status_code=204)
    def change_password(payload: PasswordChange, request: Request, response: Response) -> None:
        require_origin(request)
        token = request.cookies.get(COOKIE)
        if not token:
            raise HTTPException(401, "Sign in required")
        account = me(request)
        limit(request, "password", account.email)
        with database() as db:
            db.execute("SELECT principal_id, password_hash FROM rahbar.learner_accounts "
                       "WHERE principal_id = %s AND disabled_at IS NULL FOR UPDATE", (account.account_id,))
            row = db.fetchone()
            db.execute(
                "SELECT 1 FROM rahbar.auth_sessions WHERE token_digest = %s AND principal_id = %s "
                "AND expires_at > now() AND revoked_at IS NULL", (token_digest(token), account.account_id)
            )
            session = db.fetchone()
            if not row or not session:
                raise HTTPException(401, "Sign in required")
            if not verify(payload.current_password.get_secret_value(), row[1]):
                raise HTTPException(403, "Current password is incorrect")
            encoded = new_hash(payload.new_password.get_secret_value(), account.email)
            db.execute("UPDATE rahbar.learner_accounts SET password_hash = %s WHERE principal_id = %s",
                       (encoded, row[0]))
            db.execute("UPDATE rahbar.auth_sessions SET revoked_at = now() "
                       "WHERE principal_id = %s AND revoked_at IS NULL", (row[0],))
            issue_session(db, request, response, row[0])

    @router.post("/auth/logout", status_code=204)
    def logout(request: Request, response: Response) -> None:
        require_origin(request)
        token = request.cookies.get(COOKIE)
        if token:
            with database() as db:
                revoke(db, token)
        response.delete_cookie(COOKIE, path="/", httponly=True, secure=secure, samesite="strict")

    app.include_router(router)
    return app
