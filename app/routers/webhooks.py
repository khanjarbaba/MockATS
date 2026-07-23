"""Webhook receiver: where SHL's async notifications land, mimicking the real
ATS's callback endpoint. Point SHL (or the profile's {{callback_url}}) at:

    POST {CALLBACK_BASE_URL}/webhooks/{profile_name}

Processing per the profile's webhook contract:
  1. optional signature verification (hmac)
  2. extract event type + candidate identity
  3. apply mappings (results_link, assessment_link, ...) + status_map
  4. log everything as a Transaction + WebhookEvent
"""

import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..engine.auth import verify_hmac
from ..engine.templating import get_path
from ..models import Candidate, Profile, Transaction, WebhookEvent

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/{profile_name}")
@router.post("/webhooks/{profile_name}/{subpath:path}")
async def receive_webhook(profile_name: str, request: Request, subpath: str = "", db: Session = Depends(get_db)):
    raw = await request.body()
    headers = dict(request.headers)
    try:
        payload = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, ValueError):
        payload = {"_raw": raw.decode(errors="replace")}

    profile = db.query(Profile).filter_by(name=profile_name).first()

    txn = Transaction(
        profile_id=profile.id if profile else None,
        direction="webhook",
        action=f"webhook/{subpath}" if subpath else "webhook",
        method="POST",
        url=str(request.url),
        request_headers=headers,
        request_body=raw.decode(errors="replace"),
        response_status=200,
    )
    db.add(txn)
    db.commit()

    if not profile:
        return {"received": True, "warning": f"no profile named '{profile_name}' — logged only",
                "transaction_id": txn.id}

    contract = (profile.config or {}).get("webhook", {}) or {}

    verified = 0
    verification = contract.get("verification", {}) or {}
    if verification.get("type") == "hmac":
        verified = 1 if verify_hmac(verification, headers, raw) else 0

    event_type = get_path(payload, contract.get("event_type_path", "$.event")) or ""

    candidate = None
    match_value = get_path(payload, contract.get("match_candidate_by", "$.candidate.email"))
    match_field = contract.get("match_field", "email")
    if match_value:
        col = {"email": Candidate.email, "external_ref": Candidate.external_ref,
               "name": Candidate.name}.get(match_field, Candidate.email)
        candidate = (
            db.query(Candidate)
            .filter(Candidate.profile_id == profile.id, col == str(match_value))
            .order_by(Candidate.updated_at.desc())
            .first()
        )

    changes: dict = {}
    if candidate:
        for field, path in (contract.get("mappings") or {}).items():
            value = get_path(payload, path)
            if value is None:
                continue
            if field == "results_link":
                candidate.results_link = str(value)
            elif field == "assessment_link":
                candidate.assessment_link = str(value)
            else:
                candidate.extra = {**(candidate.extra or {}), field: value}
            changes[field] = value

        status_map = contract.get("status_map") or {}
        mapped = status_map.get(str(event_type).lower())
        if mapped:
            candidate.status = mapped
            changes["status"] = mapped

        txn.candidate_id = candidate.id

    event = WebhookEvent(
        profile_id=profile.id,
        candidate_id=candidate.id if candidate else None,
        transaction_id=txn.id,
        event_type=str(event_type),
        verified=verified,
        payload=payload,
        applied_changes=changes,
    )
    db.add(event)
    db.commit()

    return {
        "received": True,
        "event_type": event_type,
        "candidate_matched": candidate.id if candidate else None,
        "applied_changes": changes,
        "signature_verified": bool(verified) if verification.get("type") == "hmac" else None,
        "transaction_id": txn.id,
    }


@router.get("/api/webhook-events")
def list_events(profile: str | None = None, db: Session = Depends(get_db)):
    q = db.query(WebhookEvent).order_by(WebhookEvent.id.desc())
    if profile:
        p = db.query(Profile).filter_by(name=profile).first()
        q = q.filter_by(profile_id=p.id) if p else q.filter(False)
    return [
        {
            "id": e.id, "profile_id": e.profile_id, "candidate_id": e.candidate_id,
            "event_type": e.event_type, "verified": bool(e.verified),
            "payload": e.payload, "applied_changes": e.applied_changes,
            "created_at": str(e.created_at),
        }
        for e in q.limit(200).all()
    ]
