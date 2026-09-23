from pathlib import Path
import uuid

from fastapi import HTTPException, status


# Flat cap for everyone — tiers are gone, so this is no longer per-plan.
MAX_UPLOAD_BYTES = int(1.1 * 1024 * 1024 * 1024)  # 1.1GB
CHUNK_SIZE = 1024 * 1024

# Credit cost per render. Dashboard and Headshot Extraction Lab cost 2;
# Beat Sync Lab costs 3 (see CREDIT_COST_BEATSYNC in beatsync.py usage).
CREDIT_COST = 2
CREDIT_COST_BEATSYNC = 3


def friendly_size(byte_count):
    if byte_count >= 1024 * 1024 * 1024:
        return f"{byte_count / (1024 * 1024 * 1024):.1f} GB"
    return f"{byte_count / (1024 * 1024):.0f} MB"


def require_processing_credits(db, user, cost=CREDIT_COST):
    locked_user = db.query(type(user)).filter(type(user).id == user.id).with_for_update().one()
    if locked_user.credits < cost:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"You need at least {cost} credits to process a video. Please add credits and try again.",
        )
    return locked_user


def save_upload_in_chunks(upload, directory, allowed_extensions):
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in allowed_extensions:
        raise HTTPException(status_code=415, detail="Unsupported upload format")

    destination = Path(directory) / f"jahvi_upload_{uuid.uuid4()}{suffix}"
    bytes_written = 0
    try:
        with destination.open("wb") as output:
            while chunk := upload.file.read(CHUNK_SIZE):
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"This file is too large. The maximum upload is {friendly_size(MAX_UPLOAD_BYTES)}.",
                    )
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination
