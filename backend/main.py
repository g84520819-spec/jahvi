"""
main.py
FastAPI app entrypoint.
"""

import json
import shutil
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from database import DashboardEffect, User, get_db, init_db
from me import router as me_router
from me import get_current_user
from signup import router as signup_router
from login import router as login_router
from paystack_routes import router as paystack_router
from password_reset import router as password_reset_router
from queue_routes import router as queue_router
from headshot_routes import router as headshot_router
from queue_service import enqueue_uploaded_job, parse_timestamps, queued_response, upload_queue_file
from upload_limits import CREDIT_COST, CREDIT_COST_BEATSYNC, require_processing_credits, save_upload_in_chunks
from uploadthing_client import delete_file
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

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"}
ALLOWED_RATIOS = {"16:9", "9:16", "1:1"}


@app.get("/api/effects")
def effects(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(DashboardEffect).order_by(DashboardEffect.id).all()
    return {"classes": [effect.to_payload() for effect in rows]}


@app.post("/api/generate")
def submit_dashboard_job(
    video: UploadFile = File(...),
    headshot_timestamps: str = Form(...),
    effect_class_ids: str = Form(...),
    ratio: str = Form("9:16"),
    gap_mode: str = Form("normal"),
    gap_value: float = Form(3.0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    locked_user = require_processing_credits(db, user, cost=CREDIT_COST)
    if video.content_type and not video.content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="Please upload a video file")
    if ratio not in ALLOWED_RATIOS or gap_mode not in {"normal", "maximum", "exact"}:
        raise HTTPException(status_code=422, detail="Unsupported export settings")
    if gap_mode != "normal" and gap_value <= 0:
        raise HTTPException(status_code=422, detail="Gap value must be positive")
    timestamps = parse_timestamps(headshot_timestamps)
    try:
        effect_ids = [int(value) for value in json.loads(effect_class_ids)]
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=422, detail="Selected effects must be a JSON array of ids") from error
    if not effect_ids or db.query(DashboardEffect).filter(DashboardEffect.id.in_(effect_ids)).count() != len(set(effect_ids)):
        raise HTTPException(status_code=422, detail="Choose valid effect styles")
    source_path = save_upload_in_chunks(video, tempfile.gettempdir(), VIDEO_EXTENSIONS)
    try:
        job = enqueue_uploaded_job(
            db, user_id=locked_user.id, edit_type="dashboard",
            payload={"headshot_timestamps": json.dumps(timestamps), "effect_class_ids": json.dumps(effect_ids),
                     "ratio": ratio, "gap_mode": gap_mode, "gap_value": gap_value},
            source_path=source_path,
        )
        return queued_response(db, job)
    finally:
        source_path.unlink(missing_ok=True)


@app.post("/api/extract")
def submit_extraction_job(
    video: UploadFile = File(...),
    headshot_timestamps: str = Form(...),
    max_gap: float = Form(5.0),
    ratio: str = Form("9:16"),
    patch: bool = Form(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    locked_user = require_processing_credits(db, user, cost=CREDIT_COST)
    if video.content_type and not video.content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="Please upload a video file")
    if max_gap <= 0 or ratio not in ALLOWED_RATIOS:
        raise HTTPException(status_code=422, detail="Invalid extraction settings")
    timestamps = parse_timestamps(headshot_timestamps)
    source_path = save_upload_in_chunks(video, tempfile.gettempdir(), VIDEO_EXTENSIONS)
    try:
        job = enqueue_uploaded_job(
            db, user_id=locked_user.id, edit_type="extraction",
            payload={"headshot_timestamps": json.dumps(timestamps), "max_gap": max_gap,
                     "ratio": ratio, "patch": patch},
            source_path=source_path,
        )
        return queued_response(db, job)
    finally:
        source_path.unlink(missing_ok=True)


@app.post("/api/beatsync")
def submit_beatsync_job(
    video: UploadFile = File(...),
    custom_audio: UploadFile | None = File(None),
    headshot_timestamps: str = Form(...),
    effect_class_ids: str = Form(...),
    ratio: str = Form("9:16"),
    music_volume: float = Form(1.0),
    video_volume: float = Form(1.0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    locked_user = require_processing_credits(db, user, cost=CREDIT_COST_BEATSYNC)
    if video.content_type and not video.content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="Please upload a video file")
    if ratio not in ALLOWED_RATIOS or not 0 <= music_volume <= 1 or not 0 <= video_volume <= 2:
        raise HTTPException(status_code=422, detail="Invalid BeatSync settings")
    timestamps = parse_timestamps(headshot_timestamps)
    try:
        effect_ids = [int(value) for value in json.loads(effect_class_ids)]
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=422, detail="Selected effects must be a JSON array of ids") from error
    if not effect_ids or db.query(DashboardEffect).filter(DashboardEffect.id.in_(effect_ids)).count() != len(set(effect_ids)):
        raise HTTPException(status_code=422, detail="Choose valid effect styles")

    request_dir = Path(tempfile.mkdtemp(prefix="jahvi_beatsync_upload_"))
    audio_key = None
    try:
        source_path = save_upload_in_chunks(video, request_dir, VIDEO_EXTENSIONS)
        payload = {"headshot_timestamps": json.dumps(timestamps), "effect_class_ids": json.dumps(effect_ids),
                   "ratio": ratio, "music_volume": music_volume, "video_volume": video_volume}
        if custom_audio is not None:
            audio_path = save_upload_in_chunks(custom_audio, request_dir, VIDEO_EXTENSIONS | AUDIO_EXTENSIONS)
            audio_key = upload_queue_file(audio_path, prefix="queue-audio", db=db)
            payload["audio_file_key"] = audio_key
        job = enqueue_uploaded_job(
            db, user_id=locked_user.id, edit_type="beatsync", payload=payload, source_path=source_path,
        )
        return queued_response(db, job)
    except Exception:
        if audio_key:
            try:
                delete_file(audio_key)
            except Exception:
                pass
        raise
    finally:
        shutil.rmtree(request_dir, ignore_errors=True)


@app.on_event("startup")
def on_startup():
    # Create missing tables first, then apply versioned schema changes.
    init_db()
    start_queue_worker()
    start_sweeper()


app.include_router(signup_router)
app.include_router(login_router)
app.include_router(me_router)
app.include_router(paystack_router)
app.include_router(password_reset_router)
app.include_router(queue_router)
app.include_router(headshot_router)


@app.get("/health")
def health():
    return {"status": "ok"}
