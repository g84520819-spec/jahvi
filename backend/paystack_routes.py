"""
paystack_routes.py
Credit-purchase flow: initialize -> Paystack hosted checkout -> webhook
(signature-verified, idempotent) credits the user. A backup
verify-transaction call covers the case where the webhook is delayed.

Email used for Paystack is simply the user's own signup email — no
synthetic email needed now that auth is email-based.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from credit_packs import CREDIT_PACKS, get_pack, naira_to_kobo
from database import CreditPurchase, User, get_db
from me import get_current_user
from paystack_client import initialize_transaction, verify_transaction, verify_webhook_signature

router = APIRouter(prefix="/api/credits", tags=["credits"])
logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/packs")
def list_packs():
    return {
        "packs": [
            {"pack": pack_id, "naira": info["naira"], "credits": info["credits"]}
            for pack_id, info in sorted(CREDIT_PACKS.items())
        ]
    }


@router.post("/purchase")
def start_purchase(
    pack: int = Query(..., description="Pack id 1-5, sent as a query param: POST /api/credits/purchase?pack=1"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pack_info = get_pack(pack)
    if pack_info is None:
        raise HTTPException(status_code=422, detail="Unknown credit pack")

    reference = str(uuid.uuid4())
    amount_kobo = naira_to_kobo(pack_info["naira"])

    purchase = CreditPurchase(
        user_id=user.id,
        reference=reference,
        pack=pack,
        credits=pack_info["credits"],
        amount_kobo=amount_kobo,
        status="pending",
        created_at=_now_iso(),
    )
    db.add(purchase)
    db.commit()

    try:
        paystack_data = initialize_transaction(user.email, amount_kobo, reference)
    except Exception as error:
        purchase.status = "failed"
        db.commit()
        raise HTTPException(status_code=502, detail=f"Could not start payment: {error}")

    return {
        "reference": reference,
        "authorization_url": paystack_data.get("authorization_url"),
    }


def _credit_user_for_purchase(db: Session, purchase: CreditPurchase) -> None:
    """Idempotent AND concurrency-safe: Paystack can (and does, by design,
    as a reliability feature) deliver the same webhook more than once. A
    plain 'if status == success: return' read-then-write check has a race
    — two near-simultaneous deliveries could both read 'not yet success'
    before either commits, and both add credits. Guarding the status
    transition itself with an atomic conditional UPDATE closes that: only
    whichever request actually flips the row from pending -> success gets
    to credit the user."""
    result = db.execute(
        update(CreditPurchase)
        .where(CreditPurchase.id == purchase.id, CreditPurchase.status != "success")
        .values(status="success", verified_at=_now_iso())
    )
    if result.rowcount == 0:
        # Someone else already credited this purchase — nothing more to do.
        db.commit()
        return
    db.execute(
        update(User).where(User.id == purchase.user_id).values(credits=User.credits + purchase.credits)
    )
    db.commit()


@router.post("/webhook")
async def paystack_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature")

    if not verify_webhook_signature(raw_body, signature):
        # Never trust an unsigned/incorrectly-signed webhook.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    payload = await request.json()
    event = payload.get("event")
    data = payload.get("data", {})
    reference = data.get("reference")

    if not reference:
        return {"status": "ignored"}

    purchase = db.query(CreditPurchase).filter(CreditPurchase.reference == reference).first()
    if purchase is None:
        logger.warning("Paystack webhook for unknown reference %s", reference)
        return {"status": "ignored"}

    if event == "charge.success":
        _credit_user_for_purchase(db, purchase)
    elif event == "charge.failed":
        if purchase.status != "success":
            purchase.status = "failed"
            db.commit()

    return {"status": "ok"}


@router.get("/verify/{reference}")
def verify_purchase(
    reference: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Backup path: called right after the user is redirected back from
    Paystack, in case the webhook hasn't landed yet."""
    purchase = db.query(CreditPurchase).filter(
        CreditPurchase.reference == reference, CreditPurchase.user_id == user.id
    ).first()
    if purchase is None:
        raise HTTPException(status_code=404, detail="Purchase not found")

    if purchase.status == "success":
        return {"status": "success", "credits": purchase.credits}

    try:
        paystack_data = verify_transaction(reference)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Could not verify payment: {error}")

    if paystack_data.get("status") == "success":
        _credit_user_for_purchase(db, purchase)
        return {"status": "success", "credits": purchase.credits}

    return {"status": purchase.status}
