import json
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from database import User, get_db
from me import get_current_user
from queue_service import enqueue_uploaded_job, parse_timestamps, queued_response
from upload_limits import CREDIT_COST, require_processing_credits, save_upload_in_chunks

router = APIRouter(prefix="/api", tags=["extraction"])
ALLOWED_RATIOS = {"16:9", "9:16", "1:1"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"}


@router.post("/extract")
def extract(
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
    if max_gap <= 0:
        raise HTTPException(status_code=422, detail="Maximum gap must be a positive number of seconds")
    if ratio not in ALLOWED_RATIOS:
        raise HTTPException(status_code=422, detail="Unsupported export ratio")
    timestamps = parse_timestamps(headshot_timestamps)

    source_path = save_upload_in_chunks(video, tempfile.gettempdir(), VIDEO_EXTENSIONS)
    try:
        job = enqueue_uploaded_job(
            db,
            user_id=locked_user.id,
            edit_type="extraction",
            payload={
                "headshot_timestamps": json.dumps(timestamps),
                "max_gap": max_gap,
                "ratio": ratio,
                "patch": patch,
            },
            source_path=source_path,
        )
        return queued_response(db, job)
    finally:
        source_path.unlink(missing_ok=True)
