import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from database import User
from cleanup import register_temp_path, unregister_temp_path
from me import get_current_user
from upload_limits import save_upload_in_chunks

router = APIRouter(prefix="/api", tags=["headshot detection"])
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"}


@router.post("/detect-headshots")
def detect_video_headshots(
    video: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    if video.content_type and not video.content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="Please upload a video file")

    video_path = None
    try:
        video_path = save_upload_in_chunks(
            video,
            tempfile.gettempdir(),
            VIDEO_EXTENSIONS,
            prefix="jahvi_headshot_detect_",
        )
        register_temp_path(video_path)
        try:
            from detect_headshot import detect_headshots
        except ImportError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OpenCV headshot detection is unavailable on this server.",
            ) from error

        events = detect_headshots(str(video_path))
        timestamps = sorted({
            max(0.0, round(float(event["timestamp_seconds"]) - 0.5, 3))
            for event in events
        })
        return {"timestamps": timestamps, "count": len(timestamps)}
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"Could not detect headshots: {error}") from error
    finally:
        if video_path:
            video_path.unlink(missing_ok=True)
            unregister_temp_path(video_path)
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from database import User
from me import get_current_user
from upload_limits import save_upload_in_chunks

router = APIRouter(prefix="/api", tags=["headshot detection"])
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"}


@router.post("/detect-headshots")
def detect_video_headshots(
    video: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    if video.content_type and not video.content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="Please upload a video file")

    request_dir = Path(tempfile.mkdtemp(prefix="jahvi_headshot_detect_"))
    try:
        video_path = save_upload_in_chunks(video, request_dir, VIDEO_EXTENSIONS)
        try:
            from detect_headshot import detect_headshots
        except ImportError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OpenCV headshot detection is unavailable on this server.",
            ) from error

        events = detect_headshots(str(video_path))
        timestamps = sorted({
            max(0.0, round(float(event["timestamp_seconds"]) - 0.5, 3))
            for event in events
        })
        return {"timestamps": timestamps, "count": len(timestamps)}
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"Could not detect headshots: {error}") from error
    finally:
        shutil.rmtree(request_dir, ignore_errors=True)