from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..engine.auth import AuthError
from ..engine.sender import SendBlocked, execute_action
from ..models import Candidate, Profile

router = APIRouter(prefix="/api", tags=["simulate"])


class SimulateIn(BaseModel):
    profile: str
    action: str
    candidate: dict = {}          # {"name": ..., "email": ..., "external_ref": ...}
    candidate_id: int | None = None
    inputs: dict = {}             # extra template vars
    dry_run: bool = True
    confirm: bool = False


@router.post("/simulate")
def simulate(body: SimulateIn, db: Session = Depends(get_db)):
    profile = db.query(Profile).filter_by(name=body.profile).first()
    if not profile:
        raise HTTPException(404, f"profile '{body.profile}' not found")

    actions = (profile.config or {}).get("actions", {})
    if body.action not in actions:
        raise HTTPException(404, f"action '{body.action}' not in profile (has: {list(actions)})")

    candidate = None
    if body.candidate_id:
        candidate = db.get(Candidate, body.candidate_id)
        if not candidate:
            raise HTTPException(404, "candidate not found")
    elif body.candidate:
        email = body.candidate.get("email", "")
        if email:
            candidate = (
                db.query(Candidate)
                .filter_by(profile_id=profile.id, email=email)
                .first()
            )
        if not candidate:
            candidate = Candidate(
                profile_id=profile.id,
                name=body.candidate.get("name", ""),
                email=email,
                external_ref=body.candidate.get("external_ref", ""),
                extra={k: v for k, v in body.candidate.items()
                       if k not in ("name", "email", "external_ref")},
            )
            db.add(candidate)
            db.commit()

    inputs = {**body.inputs, "candidate": body.candidate}
    try:
        result = execute_action(
            db, profile, body.action, actions[body.action],
            candidate, inputs, dry_run=body.dry_run, confirm=body.confirm,
        )
    except SendBlocked as exc:
        raise HTTPException(403, str(exc))
    except AuthError as exc:
        raise HTTPException(422, f"auth config problem: {exc}")

    if candidate:
        result["candidate_id"] = candidate.id
    return result


@router.get("/candidates")
def list_candidates(profile: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Candidate).order_by(Candidate.updated_at.desc())
    if profile:
        p = db.query(Profile).filter_by(name=profile).first()
        q = q.filter_by(profile_id=p.id) if p else q.filter(False)
    return [
        {
            "id": c.id, "profile_id": c.profile_id, "name": c.name, "email": c.email,
            "external_ref": c.external_ref, "status": c.status,
            "assessment_link": c.assessment_link, "results_link": c.results_link,
            "extra": c.extra, "updated_at": str(c.updated_at),
        }
        for c in q.limit(200).all()
    ]
