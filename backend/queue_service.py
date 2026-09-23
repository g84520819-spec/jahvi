"""
queue_service.py
Shared queue logic — isolated here per the project's own rule (anything
used across multiple pages/files goes in its own standalone file).

Two separate mechanisms, deliberately kept apart:
  (a) submit-time check (is_server_overloaded) — called synchronously when
      a user submits an edit, decides "process now" vs "queue it".
  (b) the 3-minute drain worker (see worker.py) — wakes up on its own
      schedule and pulls the next queued job if load allows.
"""

import json
import logging
import asyncio
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

import psutil
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from database import QueuedJob, User
from uploadthing_client import upload_file

logger = logging.getLogger(__name__)

# A zero threshold routes every normal submission through the queue.
LOAD_THRESHOLD_PERCENT = 0.0
WORKER_LOAD_THRESHOLD_PERCENT = 85
_processing_queued_job = ContextVar("processing_queued_job", default=False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_server_overloaded() -> bool:
    """CPU load check via psutil. interval=0.3 gives a real (non-zero-on-
    first-call) reading without stalling the request for too long."""
    if _processing_queued_job.get():
        return False
    return psutil.cpu_percent(interval=0.3) >= LOAD_THRESHOLD_PERCENT


@contextmanager
def queued_job_processing():
    token = _processing_queued_job.set(True)
    try:
        yield
    finally:
        _processing_queued_job.reset(token)


def is_worker_overloaded() -> bool:
    return psutil.cpu_percent(interval=0.3) >= WORKER_LOAD_THRESHOLD_PERCENT


def enqueue_job(db: Session, *, user_id: int, edit_type: str, payload: dict, input_file_key: str | None) -> QueuedJob:
    job = QueuedJob(
        id=str(uuid.uuid4()),
        user_id=user_id,
        edit_type=edit_type,
        payload=json.dumps(payload),
        input_file_key=input_file_key,
        status="queued",
        created_at=_now_iso(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def enqueue_uploaded_job(
    db: Session,
    *,
    user_id: int,
    edit_type: str,
    payload: dict,
    source_path: Path,
) -> QueuedJob:
    """Persist a queued request after moving its source into durable storage."""
    try:
        upload_result = upload_file(str(source_path), f"queue-{uuid.uuid4()}{source_path.suffix}")
        job = enqueue_job(
            db,
            user_id=user_id,
            edit_type=edit_type,
            payload=payload,
            input_file_key=upload_result["key"],
        )
        return job
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not place this edit in the queue: {error}",
        ) from error


def upload_queue_file(source_path: Path, *, prefix: str) -> str:
    try:
        return upload_file(str(source_path), f"{prefix}-{uuid.uuid4()}{source_path.suffix}")["key"]
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not store this queued file: {error}",
        ) from error


def queued_response(db: Session, job: QueuedJob) -> dict:
    return {
        "queued": True,
        "job_id": job.id,
        "status": job.status,
        "queue_position": queue_position(db, job),
        "message": "Your edit is queued and will start when the server is available.",
    }


def queue_position(db: Session, job: QueuedJob) -> int:
    """0-indexed count of still-queued jobs ahead of this one, FIFO by
    created_at. Global — no per-edit-type lanes."""
    ahead = (
        db.query(QueuedJob)
        .filter(QueuedJob.status == "queued", QueuedJob.created_at < job.created_at)
        .count()
    )
    return ahead


async def _consume_stream(response) -> str:
    filename = None
    async for chunk in response.body_iterator:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        for line in text.splitlines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event.get("event") == "error":
                raise RuntimeError(event.get("message", "Queued edit failed"))
            if event.get("event") == "completed":
                filename = event.get("filename")
    if not filename:
        raise RuntimeError("Queued edit finished without an output file")
    return filename


def _run_route(route, *, db, user, local_input_path: str, payload: dict, audio_path: str | None = None) -> str:
    from fastapi import UploadFile

    with open(local_input_path, "rb") as video_file:
        video_upload = UploadFile(file=video_file, filename="queued-input.mp4", headers={"content-type": "video/mp4"})
        kwargs = {"video": video_upload, "user": user, "db": db, **payload}
        if audio_path:
            with open(audio_path, "rb") as audio_file:
                kwargs["custom_audio"] = UploadFile(
                    file=audio_file,
                    filename="queued-audio.mp3",
                    headers={"content-type": "audio/mpeg"},
                )
                response = route(**kwargs)
                return asyncio.run(_consume_stream(response))
        response = route(**kwargs)
        return asyncio.run(_consume_stream(response))


def _run_dashboard(local_input_path: str, payload: dict, *, db, user) -> str:
    from dashboard import generate_dashboard_video

    return _run_route(generate_dashboard_video, db=db, user=user, local_input_path=local_input_path, payload=payload)


def _run_extraction(local_input_path: str, payload: dict, *, db, user) -> str:
    from extraction import extract

    return _run_route(extract, db=db, user=user, local_input_path=local_input_path, payload=payload)


def _run_beatsync(local_input_path: str, payload: dict, *, db, user, audio_path: str | None = None) -> str:
    from beatsync import beatsync

    if audio_path is None:
        payload = {**payload, "custom_audio": None}
    return _run_route(
        beatsync,
        db=db,
        user=user,
        local_input_path=local_input_path,
        payload=payload,
        audio_path=audio_path,
    )


def process_job(job: QueuedJob, local_input_path: str, *, db: Session, audio_path: str | None = None) -> str:
    """Runs the job's actual edit pipeline. Returns the local path to the
    finished output file (caller is responsible for uploading it to
    UploadThing and cleaning up both local paths)."""
    payload = json.loads(job.payload)
    user = db.query(User).filter_by(id=job.user_id).one()
    with queued_job_processing():
        if job.edit_type == "dashboard":
            output_name = _run_dashboard(local_input_path, payload, db=db, user=user)
        elif job.edit_type == "extraction":
            output_name = _run_extraction(local_input_path, payload, db=db, user=user)
        elif job.edit_type == "beatsync":
            payload.pop("audio_file_key", None)
            output_name = _run_beatsync(local_input_path, payload, db=db, user=user, audio_path=audio_path)
        else:
            raise ValueError(f"Unknown queued edit type: {job.edit_type!r}")

    from dashboard import OUTPUT_DIR

    output_path = OUTPUT_DIR / Path(output_name).name
    if not output_path.is_file():
        raise FileNotFoundError(f"Queued output was not created: {output_path}")
    return str(output_path)
