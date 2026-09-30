import json
import os
import shutil
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi import BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import update
from sqlalchemy.orm import Session

from database import User, get_db
from cleanup import delete_file
from me import get_current_user
from patcher import build_headshot_highlight as build_patched_highlight
from patchless import build_headshot_highlight as build_patchless_highlight
from queue_service import enqueue_uploaded_job, is_server_overloaded, queued_response
from upload_limits import CREDIT_COST, require_processing_credits, save_upload_in_chunks

router = APIRouter(prefix="/api", tags=["extraction"])
OUTPUT_DIR = Path(os.getenv("JAHVI_OUTPUT_DIR", tempfile.gettempdir())) / "jahvi_outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_RATIOS = {"16:9", "9:16", "1:1"}


def _require_ffmpeg() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Missing backend video tools: {', '.join(missing)}. Install FFmpeg and restart the backend.",
        )


def _event(event: str, **data) -> str:
    return f"data: {json.dumps({'event': event, **data})}\n\n"


def _parse_timestamps(raw: str) -> list[float]:
    try:
        timestamps = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=422, detail="Headshot timestamps must be a JSON array")

    if not isinstance(timestamps, list) or not timestamps:
        raise HTTPException(status_code=422, detail="Add at least one headshot before exporting.")

    try:
        values = [float(value) for value in timestamps]
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Headshot timestamps must be numeric")

    if any(value < 0 for value in values):
        raise HTTPException(status_code=422, detail="Headshot timestamps cannot be negative")
    if any(value != value or value in (float("inf"), float("-inf")) for value in values):
        raise HTTPException(status_code=422, detail="Headshot timestamps are malformed")
    return sorted(set(values))


def _video_duration(video_path: Path) -> float:
    from patchless import _get_video_duration

    return _get_video_duration(str(video_path))


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
    locked_user = require_processing_credits(db, user)
    _require_ffmpeg()
    if video.content_type and not video.content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="Please upload a video file")
    if max_gap <= 0:
        raise HTTPException(status_code=422, detail="Maximum gap must be a positive number of seconds")
    if ratio not in ALLOWED_RATIOS:
        raise HTTPException(status_code=422, detail="Unsupported export ratio")

    timestamps = _parse_timestamps(headshot_timestamps)
    source_path = save_upload_in_chunks(video, tempfile.gettempdir(), {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"})
    try:
        duration = _video_duration(source_path)
        if any(timestamp >= duration for timestamp in timestamps):
            raise HTTPException(status_code=422, detail="Every timestamp must be within the video duration")

        if is_server_overloaded():
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
            source_path.unlink(missing_ok=True)
            return queued_response(db, job)

        # Hold the row lock through processing. This lets the successful export
        # deduct exactly two credits without allowing concurrent overspending.
        output_name = f"{uuid.uuid4()}.mp4"
        output_path = OUTPUT_DIR / output_name

        def stream() -> Iterator[str]:
            try:
                yield _event("progress", percent=5, message="Video uploaded")
                yield _event("progress", percent=15, message="Preparing headshot timestamps")
                builder = build_patched_highlight if patch else build_patchless_highlight
                executor = ThreadPoolExecutor(max_workers=1)
                future = executor.submit(
                    builder,
                    video_path=str(source_path),
                    headshot_timestamps=timestamps,
                    gap=max_gap,
                    export_ratio=ratio,
                    output_path=str(output_path),
                )
                try:
                    while True:
                        try:
                            future.result(timeout=1)
                            break
                        except FutureTimeoutError:
                            yield _event("progress", percent=20, message="Rendering headshot reel")
                finally:
                    executor.shutdown(wait=True)
                yield _event("progress", percent=90, message="Finalizing export")
                db.execute(
                    update(User)
                    .where(User.id == locked_user.id, User.credits >= CREDIT_COST)
                    .values(credits=User.credits - CREDIT_COST)
                )
                db.commit()
                yield _event("completed", percent=95, filename=output_name)
            except Exception as error:
                db.rollback()
                output_path.unlink(missing_ok=True)
                yield _event("error", message=str(error))
            finally:
                source_path.unlink(missing_ok=True)

        return StreamingResponse(stream(), media_type="text/event-stream")
    except HTTPException:
        source_path.unlink(missing_ok=True)
        raise
    except Exception as error:
        source_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Could not read video: {error}")


@router.get("/video/{filename}")
def get_video(
    filename: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
):
    if Path(filename).name != filename or not filename.endswith(".mp4"):
        raise HTTPException(status_code=404, detail="Video not found")
    path = OUTPUT_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Video not found")
    background_tasks.add_task(delete_file, path)
    return FileResponse(path, media_type="video/mp4", filename=filename)
