import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, status

from database import QueuedJob, SessionLocal, UploadedFileLog
from uploadthing_client import delete_file, get_file_url, upload_file

logger = logging.getLogger(__name__)
WORKER_ID = os.getenv("JAHVI_WORKER_ID")
if not WORKER_ID:
	raise RuntimeError("JAHVI_WORKER_ID must be worker-1 or worker-2")
WORKER_CAPACITY = max(1, int(os.getenv("JAHVI_WORKER_CAPACITY", "1")))
WORKER_SHARED_SECRET = os.getenv("JAHVI_WORKER_SHARED_SECRET", "")
SIGNATURE_MAX_AGE_SECONDS = 60
_CAPACITY = threading.BoundedSemaphore(WORKER_CAPACITY)
_REPLAYED_NONCES = {}
_REPLAY_LOCK = threading.Lock()
app = FastAPI(title=f"Jahvi {WORKER_ID}")


def _now_iso():
	return datetime.now(timezone.utc).isoformat()


def _verify_signature(path, timestamp, nonce, signature):
	if not WORKER_SHARED_SECRET or not nonce:
		return False
	try:
		request_time = int(timestamp)
	except (TypeError, ValueError):
		return False
	if abs(int(time.time()) - request_time) > SIGNATURE_MAX_AGE_SECONDS:
		return False
	message = f"POST\n{path}\n{timestamp}\n{nonce}".encode("utf-8")
	expected = hmac.new(WORKER_SHARED_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()
	if not hmac.compare_digest(expected, signature):
		return False
	now = int(time.time())
	with _REPLAY_LOCK:
		for old_nonce, expiry in list(_REPLAYED_NONCES.items()):
			if expiry < now:
				del _REPLAYED_NONCES[old_nonce]
		if nonce in _REPLAYED_NONCES:
			return False
		_REPLAYED_NONCES[nonce] = request_time + SIGNATURE_MAX_AGE_SECONDS
	return True


def _heartbeat(job_id, stop):
	while not stop.wait(60):
		try:
			with SessionLocal() as db:
				job = db.query(QueuedJob).filter_by(
					id=job_id, status="processing", assigned_worker=WORKER_ID,
				).first()
				if job is None:
					return
				job.updated_at = _now_iso()
				db.commit()
		except Exception:
			logger.exception("Worker heartbeat failed for job %s", job_id)


def _sweep_worker_disk_once():
	cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
	with SessionLocal() as db:
		active_jobs = db.query(QueuedJob).filter_by(status="processing", assigned_worker=WORKER_ID).all()
		active_ids = {job.id for job in active_jobs}

	try:
		entries = Path(tempfile.gettempdir()).iterdir()
		for path in entries:
			if not path.name.startswith(("jahvi_job_", "jahvi_upload_", "jahvi_beatsync_")):
				continue
			job_id = path.name[len("jahvi_job_"):].split("_", 1)[0] if path.name.startswith("jahvi_job_") else None
			if job_id in active_ids or (active_jobs and job_id is None):
				continue
			modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
			if modified >= cutoff:
				continue
			if path.is_dir():
				shutil.rmtree(path, ignore_errors=True)
			else:
				path.unlink(missing_ok=True)
	except OSError:
		logger.exception("Worker disk sweep could not scan temporary files")

	if active_jobs:
		return
	output_dir = Path(os.getenv("JAHVI_OUTPUT_DIR", tempfile.gettempdir())) / "jahvi_outputs"
	try:
		for path in output_dir.iterdir():
			modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
			if modified < cutoff and path.is_file():
				path.unlink(missing_ok=True)
	except OSError:
		return


def _worker_disk_sweep_loop():
	while True:
		time.sleep(60 * 60)
		_sweep_worker_disk_once()


def _process_job(job_id):
	local_input = local_audio = local_output = None
	input_key = audio_key = output_key = None
	completed = False
	with SessionLocal() as db:
		job = db.query(QueuedJob).filter_by(id=job_id).first()
		if job is None or job.status != "processing" or job.assigned_worker != WORKER_ID:
			raise HTTPException(status_code=409, detail="Job is not assigned to this worker")
		input_key = job.input_file_key
		payload = json.loads(job.payload)
		audio_key = payload.get("audio_file_key")
		try:
			if not input_key:
				raise RuntimeError("Queued job has no input file")
			with tempfile.NamedTemporaryFile(prefix=f"jahvi_job_{job_id}_input_", delete=False, suffix=".mp4") as temp:
				local_input = temp.name
			with httpx.stream("GET", get_file_url(input_key), timeout=120.0) as response:
				response.raise_for_status()
				with open(local_input, "wb") as output:
					for chunk in response.iter_bytes(1024 * 1024):
						output.write(chunk)
			if audio_key:
				with tempfile.NamedTemporaryFile(prefix=f"jahvi_job_{job_id}_audio_", delete=False, suffix=".mp3") as temp:
					local_audio = temp.name
				with httpx.stream("GET", get_file_url(audio_key), timeout=120.0) as response:
					response.raise_for_status()
					with open(local_audio, "wb") as output:
						for chunk in response.iter_bytes(1024 * 1024):
							output.write(chunk)

			from queue_service import process_job

			local_output = process_job(job, local_input, db=db, audio_path=local_audio)
			upload_result = upload_file(local_output, f"{job_id}.mp4")
			output_key = upload_result["key"]
			db.add(UploadedFileLog(file_key=output_key, job_id=job_id, uploaded_at=_now_iso()))
			job.output_file_key = output_key
			job.status = "completed"
			job.progress_percent = 100
			job.progress_message = "Video ready"
			job.completed_at = _now_iso()
			job.updated_at = job.completed_at
			db.commit()
			completed = True
		except Exception as error:
			db.rollback()
			logger.exception("Worker %s failed job %s", WORKER_ID, job_id)
			failed = db.query(QueuedJob).filter_by(id=job_id).first()
			if failed and failed.status == "processing":
				failed.status = "failed"
				failed.error_message = "Processing failed. Please try again."
				failed.progress_message = "Processing failed"
				failed.completed_at = _now_iso()
				failed.updated_at = failed.completed_at
				db.commit()
		finally:
			for path in (local_input, local_audio, local_output):
				if path:
					try:
						Path(path).unlink(missing_ok=True)
					except OSError:
						logger.exception("Could not remove worker temp file %s", path)
			for key in (input_key, audio_key):
				if key:
					try:
						delete_file(key)
					except Exception:
						logger.exception("Could not remove source UploadThing file %s", key)
			if output_key and not completed:
				try:
					delete_file(output_key)
				except Exception:
					logger.exception("Could not remove failed output %s", output_key)


@app.get("/health")
def health():
	return {"status": "ok", "worker_id": WORKER_ID}


@app.on_event("startup")
def start_disk_sweeper():
	_sweep_worker_disk_once()
	threading.Thread(target=_worker_disk_sweep_loop, name=f"{WORKER_ID}-disk-sweeper", daemon=True).start()


@app.post("/internal/workers/{worker_id}/jobs/{job_id}")
def receive_job(
	worker_id: str,
	job_id: str,
	request: Request,
	timestamp: str = Header(default="", alias="X-Jahvi-Timestamp"),
	nonce: str = Header(default="", alias="X-Jahvi-Nonce"),
	signature: str = Header(default="", alias="X-Jahvi-Signature"),
):
	if worker_id != WORKER_ID or not _verify_signature(request.url.path, timestamp, nonce, signature):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Worker request authentication failed")
	if not _CAPACITY.acquire(blocking=False):
		raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Worker is at capacity")
	with SessionLocal() as db:
		job = db.query(QueuedJob).filter_by(id=job_id, status="processing", assigned_worker=WORKER_ID).first()
		if job is None:
			_CAPACITY.release()
			raise HTTPException(status_code=409, detail="Job is not assigned to this worker")

	def process_and_release():
		stop = threading.Event()
		threading.Thread(target=_heartbeat, args=(job_id, stop), daemon=True).start()
		try:
			_process_job(job_id)
		finally:
			stop.set()
			_CAPACITY.release()

	threading.Thread(target=process_and_release, name=f"{WORKER_ID}-{job_id[:8]}", daemon=True).start()
	return {"accepted": True, "job_id": job_id}
