"""
uploadthing_client.py
UploadThing v7 client using prepareUpload and a signed multipart PUT.

*** IMPORTANT — READ BEFORE DEPLOYING ***
UploadThing v7 (their current major version) changed the server
integration model in a way that matters here:
  - The env var is now UPLOADTHING_TOKEN (a base64-encoded JSON blob
    containing your app id, region, and API key), not the older
    UPLOADTHING_SECRET plain string.
  - Presigned upload URLs are now generated on YOUR server using logic
    from their official SDK, rather than fetched via a simple REST call
    with an API-key header the way this file does it.

This file was written against the older (v6-era), simpler REST pattern —
a plain API-key header and directly POSTing file bytes — because that
was the reliably documented shape available to check. It has NOT been
verified against a real v7 UploadThing app, and the v7 token/presigned-
URL scheme is not something I could confidently reproduce from scratch
in Python without their SDK's exact signing logic. Before relying on
this in production:
  1. Check which version your UploadThing dashboard/token is on.
  2. If it's v7, either use their Node SDK in a tiny sidecar service
     called from Python, or confirm the current REST-only path (if any)
     from https://docs.uploadthing.com directly — don't assume this
     file's HTTP calls will work unmodified.

The one thing confirmed independently of version: PUBLIC FILE URLS live
on the utfs.io CDN domain (https://utfs.io/f/<FILE_KEY>), never on
api.uploadthing.com — that part of this file is correct.
"""

import base64
import json
import mimetypes
import os
from pathlib import Path
from uuid import uuid4

import httpx

UPLOADTHING_TOKEN = os.getenv("UPLOADTHING_SECRET_KEY") or os.getenv("UPLOADTHING_TOKEN") or os.getenv("UPLOADTHING_SECRET", "")
UPLOADTHING_BASE_URL = "https://api.uploadthing.com"
UPLOADTHING_FILE_CDN = "https://utfs.io"


def _api_key() -> str:
    if not UPLOADTHING_TOKEN:
        raise RuntimeError(
            "UPLOADTHING_SECRET_KEY, UPLOADTHING_TOKEN, or UPLOADTHING_SECRET is not set."
        )
    try:
        padding = "=" * (-len(UPLOADTHING_TOKEN) % 4)
        token = json.loads(base64.b64decode(UPLOADTHING_TOKEN + padding))
        return token["apiKey"]
    except (ValueError, KeyError, json.JSONDecodeError) as error:
        raise RuntimeError("UploadThing token is not a valid v7 token") from error


def _api_headers() -> dict:
    return {
        "x-uploadthing-api-key": _api_key(),
        "Content-Type": "application/json",
    }


def upload_file(file_path: str, file_name: str) -> dict:
    """Upload a local file using UploadThing's v7 prepare-and-PUT flow."""
    path = Path(file_path)
    file_name = f"{uuid4()}-{file_name}"
    file_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    response = httpx.post(
        f"{UPLOADTHING_BASE_URL}/v7/prepareUpload",
        headers=_api_headers(),
        json={
            "fileName": file_name,
            "fileSize": path.stat().st_size,
            "fileType": file_type,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    upload = response.json()

    with open(file_path, "rb") as f:
        response = httpx.put(
            upload["url"],
            files={"file": (file_name, f, file_type)},
            timeout=120.0,
        )
    response.raise_for_status()
    return {"key": upload["key"], "url": get_file_url(upload["key"])}


def delete_file(file_key: str) -> bool:
    return delete_files([file_key])


def delete_files(file_keys: list[str]) -> bool:
    if not file_keys:
        return True
    response = httpx.post(
        f"{UPLOADTHING_BASE_URL}/api/deleteFile",
        headers=_api_headers(),
        json={"fileKeys": file_keys},
        timeout=30.0,
    )
    response.raise_for_status()
    body = response.json()
    return bool(body.get("success", True))


def list_files() -> list[dict]:
    """Returns every file currently stored on UploadThing for this app —
    used by the orphan sweeper to cross-reference against Jahvi's own
    UploadedFileLog table (UploadThing's list response doesn't reliably
    expose an uploaded-at timestamp, so age is tracked on our side, not
    theirs)."""
    response = httpx.post(
        f"{UPLOADTHING_BASE_URL}/api/listFiles",
        headers=_api_headers(),
        json={},
        timeout=30.0,
    )
    response.raise_for_status()
    body = response.json()
    return body.get("files", [])


def get_file_url(file_key: str) -> str:
    # Confirmed via UploadThing's own docs: public files are served from
    # the utfs.io CDN domain, never api.uploadthing.com.
    return f"{UPLOADTHING_FILE_CDN}/f/{file_key}"
