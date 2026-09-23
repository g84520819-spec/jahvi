import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, field_validator

from database import User, get_db
from jwt_handler import create_access_token, create_refresh_token, decode_token

router = APIRouter(prefix="/auth", tags=["auth"])
password_hasher = PasswordHasher()


def _refresh_cookie_options(request: Request, path: str = "/auth", max_age: int = 60 * 60 * 24 * 7) -> dict:
    is_https = request.headers.get("origin", "").startswith("https://") or request.url.scheme == "https"
    return {
        "httponly": True,
        "secure": is_https,
        "samesite": "none" if is_https else "lax",
        "max_age": max_age,
        "path": path,
    }


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        email_pattern = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
        if len(value) > 255 or not re.match(email_pattern, value):
            raise ValueError("Enter a valid email address")
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not value:
            raise ValueError("Enter your password")
        return value


class LoginUser(BaseModel):
    id: str
    full_name: str
    email: str
    credits: int


class LoginResponse(BaseModel):
    user: LoginUser
    access_token: str | None = None
    token_type: str = "bearer"


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, response: Response, db=Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    try:
        password_hasher.verify(user.password_hash, payload.password)
    except (VerifyMismatchError, InvalidHashError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))
    response.set_cookie(key="refresh_token", value=refresh_token, **_refresh_cookie_options(request))
    response.set_cookie(key="access_token", value=access_token, **_refresh_cookie_options(request, path="/", max_age=60 * 60))

    return LoginResponse(
        user=LoginUser(
            id=str(user.id),
            full_name=user.full_name,
            email=user.email,
            credits=user.credits,
        ),
    )


@router.post("/refresh", response_model=LoginResponse)
def refresh(request: Request, response: Response, db=Depends(get_db)):
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token missing")

    payload = decode_token(refresh_token, expected_type="refresh")
    subject = payload.get("sub")
    if not subject or not str(subject).isdigit():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user identity")

    user = db.get(User, int(subject))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    access_token = create_access_token(str(user.id))
    response.set_cookie(key="access_token", value=access_token, **_refresh_cookie_options(request, path="/", max_age=60 * 60))
    response.set_cookie(
        key="refresh_token",
        value=create_refresh_token(str(user.id)),
        **_refresh_cookie_options(request),
    )
    return LoginResponse(
        user=LoginUser(
            id=str(user.id),
            full_name=user.full_name,
            email=user.email,
            credits=user.credits,
        ),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response):
    response.delete_cookie(key="refresh_token", path="/auth")
    response.delete_cookie(key="access_token", path="/")
