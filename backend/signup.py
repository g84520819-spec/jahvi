"""
signup.py
Signup endpoint + supporting device-id endpoint.

Security notes:
- IP is read from the FastAPI Request object only. It is never trusted
  from any value the frontend sends.
- Passwords are hashed with Argon2 (argon2-cffi) before storage; the
  plaintext password never touches the database.
- Signup limits (max 3 successful signups per IP, max 3 per device) are
  enforced inside a single DB transaction using Postgres advisory locks,
  so two concurrent requests from the same IP/device can't both read the
  same "count so far" and both slip in under the limit.
"""

import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import HashingError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, field_validator
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from database import DEFAULT_CREDITS, SignupSecurity, User, get_db
from jwt_handler import create_access_token, create_refresh_token

router = APIRouter(prefix="/auth", tags=["auth"])

ph = PasswordHasher()

MAX_SIGNUPS_PER_IP = 3
MAX_SIGNUPS_PER_DEVICE = 3

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    full_name: str
    email: str
    password: str
    device_id: str

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2 or len(v) > 120:
            raise ValueError("Full name must be between 2 and 120 characters")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        # Deliberately simple, strict-enough format check — no disposable-
        # domain blocklist for MVP; IP + device_id limits are the spam
        # defense for now.
        email_pattern = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
        if len(v) > 255 or not re.match(email_pattern, v):
            raise ValueError("Enter a valid email address")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not re.search(r"[A-Za-z]", v) or not re.search(r"[0-9]", v):
            raise ValueError("Password must contain at least one letter and one number")
        return v

    @field_validator("device_id")
    @classmethod
    def validate_device_id(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 16 or len(v) > 128:
            raise ValueError("Invalid device id")
        return v


class UserOut(BaseModel):
    id: str
    full_name: str
    email: str
    credits: int


class SignupResponse(BaseModel):
    user: UserOut
    access_token: str | None = None
    token_type: str = "bearer"


class DeviceIdResponse(BaseModel):
    device_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_client_ip(request: Request) -> str:
    """
    Real client IP as seen by this server. We deliberately do NOT read
    any IP the frontend might send in the request body — only the
    connection info FastAPI/Starlette gives us.

    If you're behind a trusted reverse proxy (nginx, a load balancer)
    that sets X-Forwarded-For, uncomment the block below — but only do
    this if you control/trust that proxy, since the header itself can
    otherwise be spoofed by the client.
    """
    # forwarded = request.headers.get("x-forwarded-for")
    # if forwarded:
    #     return forwarded.split(",")[0].strip()
    if request.client is None:
        return "unknown"
    return request.client.host


def _lock_key(value: str) -> str:
    """Advisory locks take a bigint; hashtext(...) in Postgres gives us one from a string."""
    return value


def record_attempt(
    db: Session,
    *,
    ip_address: str,
    device_id: str,
    success: bool,
) -> None:
    db.add(
        SignupSecurity(
            ip_address=ip_address,
            device_id=device_id,
            success=success,
        )
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/device-id", response_model=DeviceIdResponse)
def issue_device_id():
    """
    Backend generates a cryptographically secure device id. The frontend
    calls this once, then caches the value in localStorage and reuses it
    for every future signup attempt from this browser.
    """
    return DeviceIdResponse(device_id=secrets.token_urlsafe(32))


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    ip_address = get_client_ip(request)

    # --- duplicate email check (outside the locked section — cheap, and a
    # duplicate email is a validation failure, not a rate-limit decision) ---
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        record_attempt(
            db,
            ip_address=ip_address,
            device_id=payload.device_id,
            success=False,
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    # --- hash password up front, outside the lock, so we hold the lock for
    # as little time as possible ---
    try:
        password_hash = ph.hash(payload.password)
    except HashingError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not process password",
        )

    # --- concurrency-safe limit check + insert ---
    # Postgres advisory locks are transaction-scoped (xact) and released
    # automatically on commit/rollback. Two concurrent requests for the
    # same IP (or same device) will serialize here: the second one waits
    # until the first commits its new SignupSecurity row, so its COUNT()
    # is guaranteed to see the first one's signup. This is what stops
    # rapid concurrent requests from all reading "2 so far" and all
    # squeaking in under the limit of 3.
    #
    # Lock ip before device_id (consistent global order) to avoid deadlocks
    # between two requests that share an IP but have different device ids.
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": _lock_key(f"ip:{ip_address}")})
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": _lock_key(f"device:{payload.device_id}")})

    ip_success_count = (
        db.query(func.count(SignupSecurity.id))
        .filter(SignupSecurity.ip_address == ip_address, SignupSecurity.success.is_(True))
        .scalar()
    )
    device_success_count = (
        db.query(func.count(SignupSecurity.id))
        .filter(SignupSecurity.device_id == payload.device_id, SignupSecurity.success.is_(True))
        .scalar()
    )

    if ip_success_count >= MAX_SIGNUPS_PER_IP:
        record_attempt(
            db,
            ip_address=ip_address,
            device_id=payload.device_id,
            success=False,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signup limit reached for this network. Please try again later.",
        )

    if device_success_count >= MAX_SIGNUPS_PER_DEVICE:
        record_attempt(
            db,
            ip_address=ip_address,
            device_id=payload.device_id,
            success=False,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signup limit reached for this device. Please try again later.",
        )

    # --- create the account ---
    user = User(
        full_name=payload.full_name,
        email=payload.email,
        password_hash=password_hash,
        credits=DEFAULT_CREDITS,
    )
    db.add(user)
    db.flush()  # get user.id before we reference it below

    record_attempt(
        db,
        ip_address=ip_address,
        device_id=payload.device_id,
        success=True,
    )

    db.commit()
    db.refresh(user)

    # --- issue tokens ---
    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    # Refresh token goes in a secure, HttpOnly cookie — JS on the frontend
    # never sees it. Access token goes in the JSON body for the frontend
    # to attach as an Authorization header on subsequent requests.
    is_https = request.headers.get("origin", "").startswith("https://") or request.url.scheme == "https"
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=is_https,
        samesite="none" if is_https else "lax",
        max_age=60 * 60 * 24 * 7,
        path="/auth",
    )
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=is_https,
        samesite="none" if is_https else "lax",
        max_age=60 * 60,
        path="/",
    )

    return SignupResponse(
        user=UserOut(
            id=str(user.id),
            full_name=user.full_name,
            email=user.email,
            credits=user.credits,
        ),
    )
