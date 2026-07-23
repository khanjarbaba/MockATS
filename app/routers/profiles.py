import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..engine.inference import infer_action_from_samples, infer_webhook_contract, parse_raw_http
from ..models import Profile

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ProfileIn(BaseModel):
    name: str
    display_name: str = ""
    status: str = "draft"
    config: dict = {}


class SampleUpload(BaseModel):
    """Upload real captured traffic to teach/extend a profile.

    Each sample: {"kind": "request"|"response"|"webhook", "action": "send_invite",
                  "raw": "<raw HTTP text>"}  OR structured
                 {"kind": ..., "action": ..., "method": ..., "url": ...,
                  "headers": {...}, "body": {...}}
    """

    samples: list[dict]


def serialize(p: Profile) -> dict:
    return {
        "id": p.id, "name": p.name, "display_name": p.display_name,
        "status": p.status, "config": p.config,
        "created_at": str(p.created_at), "updated_at": str(p.updated_at),
    }


@router.get("")
def list_profiles(db: Session = Depends(get_db)):
    return [serialize(p) for p in db.query(Profile).order_by(Profile.name).all()]


@router.post("")
def create_profile(body: ProfileIn, db: Session = Depends(get_db)):
    if db.query(Profile).filter_by(name=body.name).first():
        raise HTTPException(409, f"profile '{body.name}' already exists")
    p = Profile(name=body.name, display_name=body.display_name or body.name.title(),
                status=body.status, config=body.config or {"actions": {}, "webhook": {}})
    db.add(p)
    db.commit()
    return serialize(p)


@router.get("/{name}")
def get_profile(name: str, db: Session = Depends(get_db)):
    p = db.query(Profile).filter_by(name=name).first()
    if not p:
        raise HTTPException(404, "profile not found")
    return serialize(p)


@router.put("/{name}")
def update_profile(name: str, body: ProfileIn, db: Session = Depends(get_db)):
    p = db.query(Profile).filter_by(name=name).first()
    if not p:
        raise HTTPException(404, "profile not found")
    p.display_name = body.display_name or p.display_name
    p.status = body.status or p.status
    p.config = body.config
    db.commit()
    return serialize(p)


@router.delete("/{name}")
def delete_profile(name: str, db: Session = Depends(get_db)):
    p = db.query(Profile).filter_by(name=name).first()
    if not p:
        raise HTTPException(404, "profile not found")
    db.delete(p)
    db.commit()
    return {"deleted": name}


def _normalize_sample(sample: dict) -> dict:
    if "raw" in sample:
        parsed = parse_raw_http(sample["raw"])
        parsed["action"] = sample.get("action", "")
        parsed["kind"] = sample.get("kind", parsed.get("kind"))
        return parsed
    if isinstance(sample.get("body"), str):
        try:
            sample = {**sample, "body": json.loads(sample["body"])}
        except (json.JSONDecodeError, ValueError):
            pass
    return sample


@router.post("/{name}/infer")
def infer_from_samples(name: str, upload: SampleUpload, db: Session = Depends(get_db)):
    """Teach the profile: infer draft actions/webhook contract from real samples.

    Pairs request+response samples sharing the same 'action'. Returns the draft
    and saves it into the profile config (still editable via PUT).
    """
    p = db.query(Profile).filter_by(name=name).first()
    if not p:
        raise HTTPException(404, "profile not found")

    samples = [_normalize_sample(s) for s in upload.samples]
    requests = {s.get("action") or "default": s for s in samples if s.get("kind") == "request"}
    responses = {s.get("action") or "default": s for s in samples if s.get("kind") == "response"}
    webhooks = [s for s in samples if s.get("kind") == "webhook"]

    if not requests and not webhooks:
        raise HTTPException(400, "no 'request' or 'webhook' samples found")

    config = dict(p.config or {})
    actions = dict(config.get("actions", {}))
    inferred: dict = {"actions": {}, "webhook": None}

    for action_name, req in requests.items():
        resp = responses.get(action_name)
        draft = infer_action_from_samples(action_name, req, resp)
        actions[action_name] = draft
        inferred["actions"][action_name] = draft

    config["actions"] = actions

    if webhooks:
        contract = infer_webhook_contract(webhooks[0])
        config["webhook"] = contract
        inferred["webhook"] = contract

    p.config = config
    p.status = p.status or "draft"
    db.commit()
    return {"profile": serialize(p), "inferred": inferred,
            "note": "Draft saved. Review inference_notes and edit via PUT /api/profiles/{name}."}
