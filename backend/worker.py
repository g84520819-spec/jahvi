"""Entry-backend queue dispatcher and shared worker HMAC helpers."""

import hashlib
import hmac
import logging
import os
import random
import secrets
import threading
import time
from datetime import datetime, timezone

import httpx

from database import QueuedJob, SessionLocal

logger = logging.getLogger(__name__)
DRAIN_INTERVAL_SECONDS = 2
SIGNATURE_MAX_AGE_SECONDS = 60
MAX_WORKER_ATTEMPTS = 3
WORKER_SHARED_SECRET = os.getenv("JAHVI_WORKER_SHARED_SECRET", "")
_REPLAYED_SIGNATURES = {}
_REPLAY_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def configured_workers() -> list[dict]:
    workers = []
    for number in (1, 2):
        capacity = max(1, int(os.getenv(f"JAHVI_WORKER_{number}_CAPACITY", "1")))
        workers.append({
            "id": f"worker-{number}",
            "url": os.getenv(f"JAHVI_WORKER_{number}_URL", f"http://127.0.0.1:{8100 + number}").rstrip("/"),
            "capacity": capacity,
        })
    return workers


def _signature(path: str, timestamp: str, nonce: str) -> str:
    if not WORKER_SHARED_SECRET:
        raise RuntimeError("JAHVI_WORKER_SHARED_SECRET is not configured")
    message = f"POST\n{path}\n{timestamp}\n{nonce}".encode("utf-8")
    return hmac.new(WORKER_SHARED_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()


def sign_worker_request(path: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    return {
        "X-Jahvi-Timestamp": timestamp,
        "X-Jahvi-Nonce": nonce,
        "X-Jahvi-Signature": _signature(path, timestamp, nonce),
    }


def verify_worker_request(path: str, timestamp: str, nonce: str, signature: str) -> bool:
    try:
        request_time = int(timestamp)
        expected = _signature(path, timestamp, nonce)
    except (TypeError, ValueError, RuntimeError):
        return False
    if abs(int(time.time()) - request_time) > SIGNATURE_MAX_AGE_SECONDS:
        return False
    if not hmac.compare_digest(expected, signature):
        return False

    now = int(time.time())
    with _REPLAY_LOCK:
        for old_signature, expiry in list(_REPLAYED_SIGNATURES.items()):
            if expiry < now:
                del _REPLAYED_SIGNATURES[old_signature]
        if nonce in _REPLAYED_SIGNATURES:
            return False
        _REPLAYED_SIGNATURES[nonce] = request_time + SIGNATURE_MAX_AGE_SECONDS
    return True


def _available_workers(db) -> list[dict]:
    available = []
    for worker in configured_workers():
        active_count = (
            db.query(QueuedJob)
            .filter(QueuedJob.assigned_worker == worker["id"], QueuedJob.status == "processing")
            .count()
        )
        if active_count < worker["capacity"]:
            available.append(worker)
    random.shuffle(available)
    return available


def _recover_unaccepted_job(job_id: str, worker_id: str, error: str, *, retryable: bool) -> None:
    with SessionLocal() as db:
        job = db.query(QueuedJob).filter_by(id=job_id).with_for_update().first()
        if job is None or job.status != "processing" or job.assigned_worker != worker_id:
            return
        job.assigned_worker = None
        job.progress_percent = 0
        if retryable and (job.attempt_count or 0) < MAX_WORKER_ATTEMPTS:
            job.status = "queued"
            job.progress_message = "Waiting for an available worker"
            job.error_message = None
        else:
            job.status = "failed"
            job.error_message = "The processing service could not complete this job. Please try again."
            job.completed_at = _now_iso()
            job.progress_message = "Processing failed"
        job.updated_at = _now_iso()
        db.commit()
        logger.error("Worker request for job %s did not complete: %s", job_id, error)


def _dispatch(worker: dict, job_id: str) -> None:
    path = f"/internal/workers/{worker['id']}/jobs/{job_id}"
    try:
        response = httpx.post(
            f"{worker['url']}{path}",
            headers=sign_worker_request(path),
            timeout=httpx.Timeout(connect=5.0, read=3600.0, write=30.0, pool=5.0),
        )
        if response.status_code == 429:
            _recover_unaccepted_job(job_id, worker["id"], "Worker is at capacity", retryable=True)
        elif response.status_code >= 500:
            _recover_unaccepted_job(job_id, worker["id"], f"Worker returned {response.status_code}", retryable=True)
        elif response.is_error:
            _recover_unaccepted_job(job_id, worker["id"], f"Worker returned {response.status_code}", retryable=False)
    except (httpx.ConnectError, httpx.ConnectTimeout) as error:
        _recover_unaccepted_job(job_id, worker["id"], str(error), retryable=True)
    except httpx.RequestError as error:
        logger.exception("Worker request for job %s lost its connection", job_id)
        with SessionLocal() as db:
            job = db.query(QueuedJob).filter_by(id=job_id).first()
            accepted = job is not None and job.status in {"completed", "failed"}
        if not accepted:
            _recover_unaccepted_job(job_id, worker["id"], str(error), retryable=False)


def _dispatch_one() -> bool:
    with SessionLocal() as db:
        workers = _available_workers(db)
        if not workers:
            return False
        job = (
            db.query(QueuedJob)
            .filter(QueuedJob.status == "queued")
            .order_by(QueuedJob.created_at.asc(), QueuedJob.id.asc())
            .with_for_update(skip_locked=True)
            .first()
        )
        if job is None:
            return False
        worker = workers[0]
        job.status = "processing"
        job.assigned_worker = worker["id"]
        job.attempt_count = (job.attempt_count or 0) + 1
        job.started_at = job.started_at or _now_iso()
        job.updated_at = _now_iso()
        job.progress_message = f"Assigned to {worker['id']}"
        db.commit()
        job_id = job.id

    threading.Thread(
        target=_dispatch,
        args=(worker, job_id),
        name=f"jahvi-dispatch-{job_id[:8]}",
        daemon=True,
    ).start()
    return True


def _dispatch_loop() -> None:
    while True:
        try:
            dispatched = _dispatch_one()
        except Exception:
            logger.exception("Queue dispatch tick failed")
            dispatched = False
        if not dispatched:
            time.sleep(DRAIN_INTERVAL_SECONDS)


def start_queue_worker():
    if not WORKER_SHARED_SECRET:
        logger.error("Queue dispatch disabled: JAHVI_WORKER_SHARED_SECRET is not configured")
        return None
    dispatchers = []
    for index in range(sum(worker["capacity"] for worker in configured_workers())):
        thread = threading.Thread(target=_dispatch_loop, name=f"jahvi-queue-dispatch-{index + 1}", daemon=True)
        thread.start()
        dispatchers.append(thread)
    return dispatchers
