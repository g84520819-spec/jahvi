"""
sweeper.py
Hourly orphan-cleanup sweeper, separate from the queue dispatcher.

Three passes:
  1. disk    — delete files not referenced by any active (queued/
               processing) QueuedJob row.
  2. UploadThing — list what's actually stored there, delete anything not
               referenced by an active row. Age comes from Jahvi's own
               UploadedFileLog table, not UploadThing's metadata (their
               list-files response doesn't reliably expose an uploaded-at
               timestamp).
    3. DB      — jobs stuck in 'processing' past the worker lease timeout are
                             requeued when attempts remain, or marked 'failed' otherwise.

Files older than one hour are cleanup candidates. The worker recovery timeout
is intentionally longer so ordinary long-running renders are not requeued.
"""

import logging
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cleanup import is_temp_path_active
from database import QueuedJob, SessionLocal, UploadedFileLog
from uploadthing_client import delete_files, list_files

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60 * 60
GRACE_WINDOW = timedelta(hours=1)
STUCK_JOB_TIMEOUT = timedelta(hours=24)
MAX_WORKER_ATTEMPTS = 3

# Where Jahvi writes local scratch files during processing (worker.py,
# extraction.py, dashboard.py, beatsync.py all use temp dirs under here).
DISK_SCAN_DIR = tempfile.gettempdir()

# Dedicated output directory (see OUTPUT_DIR in extraction.py/dashboard.py/
# beatsync.py) — rendered files here are named bare {uuid}.mp4 with no
# "jahvi_" prefix, so they need their own pass: Jahvi fully owns this
# directory, so every file in it is fair game once past the grace window,
# no filename-prefix check needed the way DISK_SCAN_DIR needs one.
OUTPUT_SCAN_DIR = Path(os.getenv("JAHVI_OUTPUT_DIR", tempfile.gettempdir())) / "jahvi_outputs"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _active_file_keys(db) -> set[str]:
    active_jobs = db.query(QueuedJob).filter(QueuedJob.status.in_(["queued", "processing"])).all()
    keys = set()
    for job in active_jobs:
        if job.input_file_key:
            keys.add(job.input_file_key)
        if job.output_file_key:
            keys.add(job.output_file_key)
    return keys


def _sweep_disk(db):
    cutoff = _now() - GRACE_WINDOW
    active_jobs = db.query(QueuedJob).filter(QueuedJob.status.in_(["queued", "processing"])).all()
    active_job_ids = {job.id for job in active_jobs}
    has_processing_jobs = any(job.status == "processing" for job in active_jobs)
    try:
        entries = os.listdir(DISK_SCAN_DIR)
    except OSError:
        entries = []
    for name in entries:
        if not name.startswith("jahvi_"):
            continue  # only touch files Jahvi itself created
        if has_processing_jobs and name.startswith("jahvi_upload_"):
            continue
        if any(job_id in name for job_id in active_job_ids):
            continue
        path = os.path.join(DISK_SCAN_DIR, name)
        if is_temp_path_active(path):
            continue
        try:
            if os.path.isdir(path):
                if not name.startswith("jahvi_beatsync_"):
                    continue
                mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
                if mtime < cutoff:
                    shutil.rmtree(path)
                    logger.info("Sweeper: removed orphaned temp directory %s", name)
                continue
            if not os.path.isfile(path):
                continue
            mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
            if mtime < cutoff:
                os.remove(path)
                logger.info("Sweeper: removed orphaned disk file %s", name)
        except OSError:
            continue

    # Second pass: the dedicated output directory. Files here (rendered
    # exports) are named bare {uuid}.mp4 with no "jahvi_" prefix, but
    # since Jahvi fully owns this whole directory, every file in it is
    # fair game once past the grace window — no prefix check needed.
    try:
        output_entries = os.listdir(OUTPUT_SCAN_DIR)
    except OSError:
        output_entries = []
    if has_processing_jobs:
        output_entries = []
    for name in output_entries:
        path = OUTPUT_SCAN_DIR / name
        try:
            if not path.is_file():
                continue
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                path.unlink()
                logger.info("Sweeper: removed orphaned output file %s", name)
        except OSError:
            continue


def _sweep_uploadthing(db):
    cutoff = _now() - GRACE_WINDOW
    active_keys = _active_file_keys(db)
    try:
        remote_files = list_files()
    except Exception:
        logger.exception("Sweeper: could not list UploadThing files")
        return

    stale_keys = []
    for remote in remote_files:
        key = remote.get("key")
        if not key or key in active_keys:
            continue
        log_row = db.query(UploadedFileLog).filter(UploadedFileLog.file_key == key).first()
        uploaded_at = _parse_iso(log_row.uploaded_at) if log_row else None
        # No log entry at all is itself suspicious (upload succeeded but
        # our own DB write failed right after) — still eligible for
        # cleanup once the grace window has passed on a best-effort basis;
        # without a logged timestamp we can't know its true age, so we
        # only sweep it if there's simply nothing tying it to a live job.
        if uploaded_at is None or uploaded_at < cutoff:
            stale_keys.append(key)

    if stale_keys:
        try:
            delete_files(stale_keys)
            logger.info("Sweeper: removed %d orphaned UploadThing file(s)", len(stale_keys))
        except Exception:
            logger.exception("Sweeper: could not delete stale UploadThing files")


def _sweep_stuck_jobs(db):
    cutoff = _now() - STUCK_JOB_TIMEOUT
    stuck = db.query(QueuedJob).filter(QueuedJob.status == "processing").all()
    for job in stuck:
        last_update = _parse_iso(job.updated_at) or _parse_iso(job.started_at)
        if last_update is not None and last_update < cutoff:
            job.updated_at = datetime.now(timezone.utc).isoformat()
            if (job.attempt_count or 0) < MAX_WORKER_ATTEMPTS:
                job.status = "queued"
                job.assigned_worker = None
                job.progress_percent = 0
                job.progress_message = "Requeued after an interrupted worker run"
                job.error_message = None
            else:
                job.status = "failed"
                job.error_message = "Processing was interrupted. Please try again."
                job.progress_message = "Processing failed"
                job.completed_at = job.updated_at
    db.commit()


def run_sweep_once():
    db = SessionLocal()
    try:
        _sweep_stuck_jobs(db)
        _sweep_disk(db)
        _sweep_uploadthing(db)
    finally:
        db.close()


def _sweep_loop():
    while True:
        time.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            run_sweep_once()
        except Exception:
            logger.exception("Orphan sweeper tick failed")


def start_sweeper():
    thread = threading.Thread(target=_sweep_loop, name="jahvi-orphan-sweeper", daemon=True)
    thread.start()
    return thread
