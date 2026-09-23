"""
queue_routes.py
The "my videos" page: list this user's queued/processing/completed/failed
jobs (decoded from their JWT via get_current_user), with an actual queue
position number for queued ones and an UploadThing URL for completed
ones. Deleting a job is only allowed while it's still 'queued' — never
once it's 'processing', so it can't be yanked out from under the worker
mid-run.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import QueuedJob, User, get_db
from me import get_current_user
from queue_service import queue_position
from uploadthing_client import delete_file, get_file_url

router = APIRouter(prefix="/api/videos", tags=["queue"])


def _job_to_payload(db: Session, job: QueuedJob) -> dict:
    payload = {
        "id": job.id,
        "edit_type": job.edit_type,
        "status": job.status,
        "created_at": job.created_at,
    }
    if job.status == "queued":
        payload["queue_position"] = queue_position(db, job)
    if job.status == "completed" and job.output_file_key:
        payload["video_url"] = f"/api/videos/{job.id}/output"
    if job.status == "failed" and job.error_message:
        payload["error"] = job.error_message
    return payload


@router.get("")
def list_my_videos(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    jobs = (
        db.query(QueuedJob)
        .filter(QueuedJob.user_id == user.id)
        .order_by(QueuedJob.created_at.desc())
        .all()
    )
    return {"videos": [_job_to_payload(db, job) for job in jobs]}


@router.get("/{job_id}/output")
def stream_completed_video(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.query(QueuedJob).filter(QueuedJob.id == job_id, QueuedJob.user_id == user.id).first()
    if job is None or job.status != "completed" or not job.output_file_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")

    def content():
        with httpx.stream("GET", get_file_url(job.output_file_key), timeout=120.0) as response:
            response.raise_for_status()
            yield from response.iter_bytes(1024 * 1024)

    return StreamingResponse(content(), media_type="video/mp4")


@router.delete("/{job_id}")
def delete_my_video(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.query(QueuedJob).filter(QueuedJob.id == job_id, QueuedJob.user_id == user.id).first()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")
    if job.status == "processing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This video is currently being processed and can't be cancelled.",
        )
    if job.input_file_key:
        try:
            delete_file(job.input_file_key)
        except Exception:
            pass
    if job.output_file_key:
        try:
            delete_file(job.output_file_key)
        except Exception:
            pass
    db.delete(job)
    db.commit()
    return {"message": "Deleted"}
