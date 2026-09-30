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
from fastapi import APIRouter, Depends, HTTPException, Request, status
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
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "updated_at": job.updated_at,
        "progress_percent": job.progress_percent,
        "progress_message": job.progress_message,
    }
    if job.assigned_worker:
        payload["worker"] = job.assigned_worker
    if job.status == "queued":
        payload["queue_position"] = queue_position(db, job)
    if job.status == "completed" and job.output_file_key:
        payload["video_url"] = f"/api/videos/{job.id}/output"
    if job.status == "failed":
        payload["error"] = "Processing failed. Please try again."
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
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.query(QueuedJob).filter(QueuedJob.id == job_id, QueuedJob.user_id == user.id).first()
    if job is None or job.status != "completed" or not job.output_file_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")

    request_headers = {}
    if range_header := request.headers.get("range"):
        request_headers["Range"] = range_header
    upstream_context = httpx.stream(
        "GET",
        get_file_url(job.output_file_key),
        headers=request_headers,
        timeout=120.0,
    )
    try:
        upstream = upstream_context.__enter__()
        if upstream.status_code >= 400:
            upstream.close()
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not stream the completed video")
    except HTTPException:
        raise
    except httpx.HTTPError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not stream the completed video") from error

    response_headers = {
        name: upstream.headers[name]
        for name in ("accept-ranges", "content-length", "content-range", "etag", "last-modified")
        if name in upstream.headers
    }

    def content():
        try:
            yield from upstream.iter_bytes(1024 * 1024)
        finally:
            upstream.close()

    return StreamingResponse(
        content(),
        status_code=upstream.status_code,
        media_type="video/mp4",
        headers=response_headers,
    )


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
