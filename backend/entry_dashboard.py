import json
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from database import DashboardEffect, User, get_db
from me import get_current_user
from queue_service import enqueue_uploaded_job, parse_timestamps, queued_response
from upload_limits import CREDIT_COST, require_processing_credits, save_upload_in_chunks

router = APIRouter(prefix="/api", tags=["dashboard"])
ALLOWED_GAP_MODES = {"normal", "maximum", "exact"}
ALLOWED_RATIOS = {"16:9", "9:16", "1:1"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"}


@router.get("/effects")
def effects(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(DashboardEffect).order_by(DashboardEffect.id).all()
    return {"classes": [effect.to_payload() for effect in rows]}


@router.post("/generate")
def generate_dashboard_video(
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
    if ratio not in ALLOWED_RATIOS:
        raise HTTPException(status_code=422, detail="Unsupported export ratio")
    if gap_mode not in ALLOWED_GAP_MODES:
        raise HTTPException(status_code=422, detail="Unsupported gap mode")
    if gap_mode in {"maximum", "exact"} and gap_value <= 0:
        raise HTTPException(status_code=422, detail="Gap value must be a positive number of seconds")
    timestamps = parse_timestamps(headshot_timestamps)
    try:
        effect_ids = [int(value) for value in json.loads(effect_class_ids)]
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=422, detail="Selected effects must be a JSON array of ids") from error
    if not effect_ids:
        raise HTTPException(status_code=422, detail="Choose at least one effect style")
    valid_count = db.query(DashboardEffect).filter(DashboardEffect.id.in_(effect_ids)).count()
    if valid_count != len(set(effect_ids)):
        raise HTTPException(status_code=422, detail="One or more selected effects are unavailable")

    source_path = save_upload_in_chunks(video, tempfile.gettempdir(), VIDEO_EXTENSIONS)
    try:
        job = enqueue_uploaded_job(
            db,
            user_id=locked_user.id,
            edit_type="dashboard",
            payload={
                "headshot_timestamps": json.dumps(timestamps),
                "effect_class_ids": json.dumps(effect_ids),
                "ratio": ratio,
                "gap_mode": gap_mode,
                "gap_value": gap_value,
            },
            source_path=source_path,
        )
        return queued_response(db, job)
    finally:
        source_path.unlink(missing_ok=True)
