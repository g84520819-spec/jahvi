"""
main.py
FastAPI app entrypoint.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_db
from extraction import router as extraction_router
from me import router as me_router
from signup import router as signup_router
from login import router as login_router
from dashboard import router as dashboard_router
from beatsync import router as beatsync_router
from paystack_routes import router as paystack_router
from password_reset import router as password_reset_router
from queue_routes import router as queue_router
from worker import start_queue_worker
from sweeper import start_sweeper

app = FastAPI(title="Auth Service")

# Explicit origins are required when credentials are enabled for the refresh-token cookie.
FRONTEND_ORIGINS = [
    "http://localhost:5000",
    "http://127.0.0.1:5000",
]
FRONTEND_ORIGIN_REGEX = r"(?:https?://(?:localhost|127\.0\.0\.1)(?::\d+)?|https?://.*\.(?:github\.dev|githubpreview\.dev))"

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_origin_regex=FRONTEND_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    # Create missing tables first, then apply versioned schema changes.
    init_db()
    start_queue_worker()
    start_sweeper()


app.include_router(signup_router)
app.include_router(login_router)
app.include_router(me_router)
app.include_router(extraction_router)
app.include_router(dashboard_router)
app.include_router(beatsync_router)
app.include_router(paystack_router)
app.include_router(password_reset_router)
app.include_router(queue_router)


@app.get("/health")
def health():
    return {"status": "ok"}
