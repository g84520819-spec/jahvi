"""
worker.py
The 3-minute queue-drain worker. Separate from the submit-time load check
in queue_service.py — this one exists purely to eventually process
whatever's sitting in the queue. Runs as a background thread started from
main.py's startup event, not a separate deployed process (kept simple, on
purpose, matching the rest of this project's "DB instead of Redis" spirit).
"""

import logging
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone

import httpx

from database import QueuedJob, SessionLocal, UploadedFileLog
from queue_service import WORKER_LOAD_THRESHOLD_PERCENT, is_worker_overloaded, process_job
from uploadthing_client import delete_file, get_file_url, upload_file

logger = logging.getLogger(__name__)

DRAIN_INTERVAL_SECONDS = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_uploaded_file(db, file_key: str, job_id: str | None) -> None:
    db.add(UploadedFileLog(file_key=file_key, job_id=job_id, uploaded_at=_now_iso()))
    db.commit()


def _drain_one_job(db) -> bool:
    """Returns True if a job was found and processed (successfully or
    not), False if the queue was empty."""
    job = (
        db.query(QueuedJob)
        .filter(QueuedJob.status == "queued")
        .order_by(QueuedJob.created_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if job is None:
        return False

    job.status = "processing"
    job.started_at = _now_iso()
    db.commit()

    local_input = None
    local_audio = None
    local_output = None
    try:
        if not job.input_file_key:
            raise RuntimeError("Queued job has no input_file_key")

        # Download the source video back from UploadThing for processing.
        with tempfile.NamedTemporaryFile(prefix="jahvi_queue_input_", delete=False, suffix=".mp4") as tmp:
            local_input = tmp.name
        with httpx.stream("GET", get_file_url(job.input_file_key), timeout=120.0) as response:
            response.raise_for_status()
            with open(local_input, "wb") as f:
                for chunk in response.iter_bytes(1024 * 1024):
                    f.write(chunk)

        audio_key = json.loads(job.payload).get("audio_file_key")
        if audio_key:
            with tempfile.NamedTemporaryFile(prefix="jahvi_queue_audio_", delete=False, suffix=".mp3") as tmp:
                local_audio = tmp.name
            with httpx.stream("GET", get_file_url(audio_key), timeout=120.0) as response:
                response.raise_for_status()
                with open(local_audio, "wb") as f:
                    for chunk in response.iter_bytes(1024 * 1024):
                        f.write(chunk)

        local_output = process_job(job, local_input, db=db, audio_path=local_audio)

        upload_result = upload_file(local_output, f"{job.id}.mp4")
        _log_uploaded_file(db, upload_result["key"], job.id)

        job.output_file_key = upload_result["key"]
        job.status = "completed"
        job.completed_at = _now_iso()
        db.commit()

    except Exception as error:
        logger.exception("Queued job %s failed", job.id)
        job.status = "failed"
        job.error_message = str(error)
        job.completed_at = _now_iso()
        db.commit()

    finally:
        # Strict cleanup: input video always removed from disk + UploadThing
        # once a job is done, success or failure — nothing lingers.
        if job.input_file_key:
            try:
                delete_file(job.input_file_key)
            except Exception:
                logger.exception("Could not delete input file %s from UploadThing", job.input_file_key)
        audio_key = json.loads(job.payload).get("audio_file_key")
        if audio_key:
            try:
                delete_file(audio_key)
            except Exception:
                logger.exception("Could not delete queued audio file %s from UploadThing", audio_key)
        for path in (local_input, local_audio, local_output):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    return True


def _drain_loop():
    while True:
        time.sleep(DRAIN_INTERVAL_SECONDS)
        try:
            if is_worker_overloaded():
                logger.info("Queue drain skipped this tick — load at/above %d%%", WORKER_LOAD_THRESHOLD_PERCENT)
                continue
            db = SessionLocal()
            try:
                _drain_one_job(db)
            finally:
                db.close()
        except Exception:
            logger.exception("Queue drain worker tick failed")


def start_queue_worker():
    thread = threading.Thread(target=_drain_loop, name="jahvi-queue-drain", daemon=True)
    thread.start()
    return thread
