import json
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from database import DashboardEffect, User, get_db
from me import get_current_user
from queue_service import enqueue_uploaded_job, parse_timestamps, queued_response, upload_queue_file
from upload_limits import CREDIT_COST_BEATSYNC, require_processing_credits, save_upload_in_chunks
from uploadthing_client import delete_file

router = APIRouter(prefix="/api", tags=["beatsync"])
ALLOWED_RATIOS = {"16:9", "9:16", "1:1"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"}


@router.post("/beatsync")
def beatsync(
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
    if ratio not in ALLOWED_RATIOS:
        raise HTTPException(status_code=422, detail="Unsupported export ratio")
    if not 0 <= music_volume <= 1:
        raise HTTPException(status_code=422, detail="Music volume must be between 0 and 1")
    if not 0 <= video_volume <= 2:
        raise HTTPException(status_code=422, detail="Video volume must be between 0 and 2")
    timestamps = parse_timestamps(headshot_timestamps)
    try:
        effect_ids = [int(value) for value in json.loads(effect_class_ids)]
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=422, detail="Selected effects must be a JSON array of ids") from error
    valid_count = db.query(DashboardEffect).filter(DashboardEffect.id.in_(effect_ids)).count()
    if not effect_ids or valid_count != len(set(effect_ids)):
        raise HTTPException(status_code=422, detail="Choose valid effect styles")

    request_dir = Path(tempfile.mkdtemp(prefix="jahvi_beatsync_upload_"))
    audio_key = None
    try:
        source_path = save_upload_in_chunks(video, request_dir, VIDEO_EXTENSIONS)
        payload = {
            "headshot_timestamps": json.dumps(timestamps),
            "effect_class_ids": json.dumps(effect_ids),
            "ratio": ratio,
            "music_volume": music_volume,
            "video_volume": video_volume,
        }
        if custom_audio is not None:
            audio_path = save_upload_in_chunks(custom_audio, request_dir, VIDEO_EXTENSIONS | AUDIO_EXTENSIONS)
            audio_key = upload_queue_file(audio_path, prefix="queue-audio", db=db)
            payload["audio_file_key"] = audio_key
        job = enqueue_uploaded_job(
            db,
            user_id=locked_user.id,
            edit_type="beatsync",
            payload=payload,
            source_path=source_path,
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
