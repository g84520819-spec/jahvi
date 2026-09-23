import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import update
from sqlalchemy.orm import Session

from database import DashboardEffect, User, get_db
from dashboard import ALLOWED_RATIOS, OUTPUT_DIR, _apply_stage_2_effects, _pick_effect_for_user, _parse_timestamps
from me import get_current_user
from timestamp_sync import synchronize_video
from queue_service import enqueue_uploaded_job, is_server_overloaded, queued_response, upload_queue_file
from upload_limits import CREDIT_COST_BEATSYNC, require_processing_credits, save_upload_in_chunks

router = APIRouter(prefix="/api", tags=["beatsync"])
logger = logging.getLogger(__name__)
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"}
RATIO_SPECS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}


def _event(event, **data):
    return f"data: {json.dumps({'event': event, **data})}\n\n"


def _require_tools():
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise HTTPException(status_code=503, detail=f"Missing video tools: {', '.join(missing)}")


def _save_upload(upload, directory, allowed_extensions):
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in allowed_extensions:
        raise HTTPException(status_code=415, detail="Unsupported upload format")
    path = Path(directory) / f"{uuid.uuid4()}{suffix}"
    try:
        with path.open("wb") as output:
            while chunk := upload.file.read(1024 * 1024):
                output.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _extract_audio(source_path, output_path):
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(source_path), "-vn", "-acodec", "libmp3lame", "-q:a", "2", str(output_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not extract audio")


def _has_audio_stream(video_path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path)],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _video_duration(video_path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not read video duration")
    return float(result.stdout.strip())


def _apply_export_ratio(input_path, output_path, ratio):
    target_width, target_height = RATIO_SPECS[ratio]
    crop_filter = (
        f"crop='min(iw,ih*{target_width}/{target_height})':"
        f"'min(ih,iw*{target_height}/{target_width})',"
        f"scale={target_width}:{target_height}:flags=bicubic,setsar=1"
    )
    filter_graph = f"[0:v]{crop_filter}[v]"
    command = ["ffmpeg", "-y", "-i", str(input_path), "-filter_complex", filter_graph, "-map", "[v]"]
    if _has_audio_stream(input_path):
        command.extend(["-map", "0:a:0", "-c:a", "aac"])
    else:
        command.append("-an")
    command.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", str(output_path)])
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not apply export ratio")


def _mix_audio(video_path, music_path, volume, output_path):
    if _has_audio_stream(video_path):
        filter_graph = (
            f"[1:a]volume={volume:.3f}[music];"
            "[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[a]"
        )
        command = [
            "ffmpeg", "-y", "-i", str(video_path), "-i", str(music_path),
            "-filter_complex", filter_graph, "-map", "0:v:0", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", str(output_path),
        ]
    else:
        duration = _video_duration(video_path)
        command = [
            "ffmpeg", "-y", "-i", str(video_path), "-i", str(music_path),
            "-filter_complex", f"[1:a]volume={volume:.3f}[a]",
            "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
            "-t", f"{duration:.3f}", "-movflags", "+faststart", str(output_path),
        ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not mix music")


@router.post("/beatsync")
def beatsync(
    video: UploadFile = File(...),
    custom_audio: UploadFile | None = File(None),
    headshot_timestamps: str = Form(...),
    effect_class_ids: str = Form(...),
    ratio: str = Form("9:16"),
    music_volume: float = Form(1.0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    locked_user = require_processing_credits(db, user, cost=CREDIT_COST_BEATSYNC)
    _require_tools()
    if video.content_type and not video.content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="Please upload a video file")
    if ratio not in ALLOWED_RATIOS:
        raise HTTPException(status_code=422, detail="Unsupported export ratio")
    if not 0 <= music_volume <= 1:
        raise HTTPException(status_code=422, detail="Music volume must be between 0 and 1")
    timestamps = _parse_timestamps(headshot_timestamps)
    try:
        effect_ids = [int(value) for value in json.loads(effect_class_ids)]
    except (TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=422, detail="Selected effects must be a JSON array of ids")
    if not effect_ids:
        raise HTTPException(status_code=422, detail="Choose at least one effect style")

    request_dir = Path(tempfile.mkdtemp(prefix="jahvi_beatsync_"))
    paths = []
    try:
        source_path = save_upload_in_chunks(video, request_dir, VIDEO_EXTENSIONS)
        paths.append(source_path)
        if custom_audio is None and not _has_audio_stream(source_path):
            raise HTTPException(status_code=422, detail="The source video must contain an audio stream")
        music_source = source_path
        if custom_audio is not None:
            music_source = save_upload_in_chunks(custom_audio, request_dir, VIDEO_EXTENSIONS | AUDIO_EXTENSIONS)
            paths.append(music_source)
        effect = _pick_effect_for_user(db, locked_user, effect_ids)
        synced_path = request_dir / "synced.mp4"
        effected_path = request_dir / "effected.mp4"
        ratioed_path = request_dir / "ratioed.mp4"
        final_path = OUTPUT_DIR / f"{uuid.uuid4()}.mp4"
        paths.extend([synced_path, effected_path, ratioed_path])

        if is_server_overloaded():
            audio_file_key = None
            if custom_audio is not None:
                audio_file_key = upload_queue_file(music_source, prefix="queue-audio")
            payload = {
                "headshot_timestamps": json.dumps(timestamps),
                "effect_class_ids": json.dumps(effect_ids),
                "ratio": ratio,
                "music_volume": music_volume,
            }
            if audio_file_key:
                payload["audio_file_key"] = audio_file_key
            job = enqueue_uploaded_job(
                db,
                user_id=locked_user.id,
                edit_type="beatsync",
                payload=payload,
                source_path=source_path,
            )
            for path in paths:
                path.unlink(missing_ok=True)
            shutil.rmtree(request_dir, ignore_errors=True)
            return queued_response(db, job)

        def stream() -> Iterator[str]:
            completed = False
            stage = "starting"
            try:
                stage = "uploading video"
                yield _event("progress", percent=5, message="Video uploaded")

                normalized_music = request_dir / f"music_{uuid.uuid4().hex}.mp3"
                paths.append(normalized_music)
                stage = "normalizing custom audio"
                if custom_audio is not None:
                    _extract_audio(music_source, normalized_music)
                    analysis_audio = normalized_music
                elif music_source.suffix.lower() in VIDEO_EXTENSIONS:
                    _extract_audio(music_source, normalized_music)
                    analysis_audio = normalized_music
                else:
                    analysis_audio = music_source
                yield _event("progress", percent=18, message="Audio ready for analysis")
                target_duration = _video_duration(analysis_audio) if custom_audio is not None else None
                stage = "detecting beats"
                yield _event("progress", percent=25, message="Analyzing beats", heartbeat=True)
                from rms import detect_beats

                analysis_executor = ThreadPoolExecutor(max_workers=1)
                analysis_future = analysis_executor.submit(detect_beats, str(analysis_audio))
                try:
                    while True:
                        try:
                            beat_times = analysis_future.result(timeout=1.0)
                            break
                        except FutureTimeoutError:
                            yield _event("progress", percent=25, message="Analyzing beats", heartbeat=True)
                finally:
                    analysis_executor.shutdown(wait=True)
                if not beat_times:
                    raise ValueError("No usable beats were detected")
                yield _event("progress", percent=45, message=f"Detected {len(beat_times)} beats")

                stage = "synchronizing video"
                synchronize_video(
                    source_path,
                    timestamps,
                    beat_times,
                    synced_path,
                    target_duration=target_duration,
                )
                yield _event("progress", percent=65, message="Headshots synchronized")

                stage = "applying effects"
                _apply_stage_2_effects(
                    synced_path,
                    effected_path,
                    effect,
                    beat_times,
                    effect_duration=0.4,
                )
                yield _event("progress", percent=75, message="Effects applied")
                stage = "applying export ratio"
                _apply_export_ratio(effected_path, ratioed_path, ratio)
                yield _event("progress", percent=82, message="Export ratio applied")

                if custom_audio is not None:
                    stage = "mixing audio"
                    _mix_audio(ratioed_path, normalized_music, music_volume, final_path)
                else:
                    shutil.copyfile(ratioed_path, final_path)

                if not final_path.is_file() or final_path.stat().st_size == 0:
                    raise RuntimeError("Final video was not created")
                db.execute(update(User).where(User.id == locked_user.id, User.credits >= CREDIT_COST_BEATSYNC).values(credits=User.credits - CREDIT_COST_BEATSYNC))
                db.commit()
                completed = True
                yield _event("completed", percent=95, filename=final_path.name)
            except Exception as error:
                db.rollback()
                final_path.unlink(missing_ok=True)
                logger.exception("BeatSync failed during %s", stage)
                yield _event("error", message=f"BeatSync failed while {stage}: {error}")
            finally:
                for path in paths:
                    path.unlink(missing_ok=True)
                shutil.rmtree(request_dir, ignore_errors=True)
                if not completed:
                    final_path.unlink(missing_ok=True)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception:
        for path in paths:
            path.unlink(missing_ok=True)
        shutil.rmtree(request_dir, ignore_errors=True)
        raise