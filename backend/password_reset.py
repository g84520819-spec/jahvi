"""
password_reset.py
Token-based, time-limited (30 min), single-use password reset, via Resend.
"""

import re
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import HashingError
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from database import PasswordReset, User, get_db
from resend_client import send_password_reset_email

router = APIRouter(prefix="/auth", tags=["auth"])
ph = PasswordHasher()

RESET_TOKEN_TTL_MINUTES = 30
FRONTEND_RESET_URL = "https://jahvi.com/reset.html"  # adjust to your real deployed frontend URL


class ForgotPasswordRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        email_pattern = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
        if len(v) > 255 or not re.match(email_pattern, v):
            raise ValueError("Enter a valid email address")
        return v


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not re.search(r"[A-Za-z]", v) or not re.search(r"[0-9]", v):
            raise ValueError("Password must contain at least one letter and one number")
        return v


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    # Always return the same response whether or not the email exists —
    # don't leak which emails are registered.
    if user is not None:
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)).isoformat()
        db.add(PasswordReset(token=token, user_id=user.id, expires_at=expires_at, used=False))
        db.commit()
        try:
            send_password_reset_email(user.email, f"{FRONTEND_RESET_URL}?token={token}")
        except Exception:
            # Don't reveal delivery failures to the caller either.
            pass
    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    reset_row = db.query(PasswordReset).filter(PasswordReset.token == payload.token).first()
    if reset_row is None or reset_row.used:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This reset link is invalid or has already been used.")

    expires_at = datetime.fromisoformat(reset_row.expires_at)
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This reset link has expired. Please request a new one.")

    user = db.query(User).filter(User.id == reset_row.user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This reset link is invalid.")

    try:
        user.password_hash = ph.hash(payload.new_password)
    except HashingError:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not process password")

    reset_row.used = True
    db.commit()
    return {"message": "Password updated. You can now log in with your new password."}
