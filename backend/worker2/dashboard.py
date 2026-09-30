import json
import os
import random
import shutil
import subprocess
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.responses import StreamingResponse
from sqlalchemy import update
from sqlalchemy.orm import Session

from cleanup import delete_file
from database import DashboardEffect, User, get_db
from me import get_current_user
from patcher import build_headshot_highlight as build_patched_highlight
from patchless import build_headshot_highlight as build_patchless_highlight
from queue_service import enqueue_uploaded_job, is_server_overloaded, queued_response
from upload_limits import CREDIT_COST, require_processing_credits, save_upload_in_chunks

router = APIRouter(prefix="/api", tags=["dashboard"])
OUTPUT_DIR = Path(os.getenv("JAHVI_OUTPUT_DIR", tempfile.gettempdir())) / "jahvi_outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_GAP_MODES = {"normal", "maximum", "exact"}
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


def _make_filter_expression(timestamps: list[float], duration: float = 1.0) -> str:
    if not timestamps:
        return "0"
    rendered = []
    for ts in timestamps:
        start = ts
        end = start + duration
        rendered.append(f"between(t,{start:.3f},{end:.3f})")
    return "+".join(rendered)


def _has_audio_stream(video_path: Path) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path)],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _apply_stage_2_effects(
    video_path: Path,
    output_path: Path,
    effect: DashboardEffect,
    timestamps: list[float],
    effect_duration: float = 1.0,
) -> None:
    # Pull the effect's own filter string and just substitute {STAMP} —
    # the enable='...' clause (if this effect has a per-beat section at
    # all) is already written into the DB row itself, not built here.
    filter_chain = effect.filter or "eq=contrast=1.1:saturation=1.1:gamma=1.05"
    if DashboardEffect.STAMP_PLACEHOLDER in filter_chain:
        expr = _make_filter_expression(timestamps, effect_duration)
        filter_chain = filter_chain.replace(DashboardEffect.STAMP_PLACEHOLDER, expr)
    has_audio = _has_audio_stream(video_path)
    filter_graph = f"[0:v]{filter_chain}[v]"
    if has_audio:
        filter_graph += ";[0:a]anull[a]"

    cmd = ["ffmpeg", "-y", "-i", str(video_path), "-filter_complex", filter_graph, "-map", "[v]"]
    if has_audio:
        cmd.extend(["-map", "[a]", "-c:a", "aac"])
    else:
        cmd.extend(["-an"])
    cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", str(output_path)])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Stage 2 effect pipeline failed.")


def _build_with_progress(builder, video_path: Path, timestamps: list[float], gap: float, ratio: str, output_path: Path) -> Iterator[str]:
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(
        builder,
        video_path=str(video_path),
        headshot_timestamps=timestamps,
        gap=gap,
        export_ratio=ratio,
        output_path=str(output_path),
    )
    try:
        while True:
            try:
                future.result(timeout=1)
                return
            except FutureTimeoutError:
                yield _event("progress", percent=65, message="Finishing your video")
    finally:
        executor.shutdown(wait=True)


def _pick_effect_for_user(db: Session, user: User, effect_ids: list[int]) -> DashboardEffect:
    # One unified effect pool for every user — no more per-plan filtering.
    allowed = db.query(DashboardEffect).filter(DashboardEffect.id.in_(effect_ids)).all()
    if not allowed:
        raise HTTPException(status_code=422, detail="No valid dashboard effect selected.")
    return random.choice(allowed)


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
    locked_user = require_processing_credits(db, user)
    _require_ffmpeg()
    if video.content_type and not video.content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="Please upload a video file")
    if ratio not in ALLOWED_RATIOS:
        raise HTTPException(status_code=422, detail="Unsupported export ratio")
    if gap_mode not in ALLOWED_GAP_MODES:
        raise HTTPException(status_code=422, detail="Unsupported gap mode")
    if gap_mode in {"maximum", "exact"} and gap_value <= 0:
        raise HTTPException(status_code=422, detail="Gap value must be a positive number of seconds")

    timestamps = _parse_timestamps(headshot_timestamps)
    source_path = save_upload_in_chunks(video, tempfile.gettempdir(), {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"})

    try:
        duration = _video_duration(source_path)
        if any(timestamp >= duration for timestamp in timestamps):
            raise HTTPException(status_code=422, detail="Every timestamp must be within the video duration")

        try:
            parsed_effect_ids = [int(value) for value in json.loads(effect_class_ids)]
        except (TypeError, ValueError, json.JSONDecodeError):
            raise HTTPException(status_code=422, detail="Selected effects must be a JSON array of ids")

        if not parsed_effect_ids:
            raise HTTPException(status_code=422, detail="Choose at least one effect style")

        if is_server_overloaded():
            job = enqueue_uploaded_job(
                db,
                user_id=locked_user.id,
                edit_type="dashboard",
                payload={
                    "headshot_timestamps": json.dumps(timestamps),
                    "effect_class_ids": json.dumps(parsed_effect_ids),
                    "ratio": ratio,
                    "gap_mode": gap_mode,
                    "gap_value": gap_value,
                },
                source_path=source_path,
            )
            source_path.unlink(missing_ok=True)
            return queued_response(db, job)

        effect = _pick_effect_for_user(db, user, parsed_effect_ids)
        stage2_path = OUTPUT_DIR / f"{uuid.uuid4()}.mp4"
        # NOTE: raw timestamps are passed straight through — the +1s trim
        # lead (so the full headshot moment stays visible) is applied
        # internally by patcher.py/patchless.py itself now. This used to
        # have its own separate +0.4s offset applied here, which would
        # have stacked with the newer +1s logic and cut later than
        # intended — removed.
        final_path = stage2_path
        generated_paths = {stage2_path}
        completed = False

        def stream() -> Iterator[str]:
            nonlocal completed, final_path
            try:
                yield _event("progress", percent=5, message="Video uploaded")
                yield _event("progress", percent=20, message="Applying style")
                _apply_stage_2_effects(source_path, stage2_path, effect, timestamps)
                yield _event("progress", percent=55, message="Stage 2 complete")

                final_path = stage2_path
                if gap_mode == "maximum":
                    final_path = OUTPUT_DIR / f"{uuid.uuid4()}.mp4"
                    generated_paths.add(final_path)
                    yield from _build_with_progress(
                        build_patched_highlight, stage2_path, timestamps,
                        float(gap_value), ratio, final_path,
                    )
                    yield _event("progress", percent=75, message="Maximum gap processing")
                elif gap_mode == "exact":
                    final_path = OUTPUT_DIR / f"{uuid.uuid4()}.mp4"
                    generated_paths.add(final_path)
                    yield from _build_with_progress(
                        build_patchless_highlight, stage2_path, timestamps,
                        float(gap_value), ratio, final_path,
                    )
                    yield _event("progress", percent=75, message="Exact gap processing")
                else:
                    final_path = stage2_path
                    yield _event("progress", percent=75, message="Normal mode")

                yield _event("progress", percent=95, message="Finalizing export")
                db.execute(
                    update(User)
                    .where(User.id == locked_user.id, User.credits >= CREDIT_COST)
                    .values(credits=User.credits - CREDIT_COST)
                )
                db.commit()
                completed = True
                yield _event("completed", percent=95, filename=final_path.name)
            except Exception as error:
                db.rollback()
                yield _event("error", message=str(error))
            finally:
                source_path.unlink(missing_ok=True)
                if completed:
                    if stage2_path != final_path:
                        stage2_path.unlink(missing_ok=True)
                else:
                    for generated_path in generated_paths:
                        generated_path.unlink(missing_ok=True)

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
    return FileResponse(path, media_type="video/mp4", filename=filename, background=background_tasks)
