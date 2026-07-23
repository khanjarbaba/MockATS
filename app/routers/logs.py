from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Profile, Transaction

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
def list_logs(
    profile: str | None = None,
    direction: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    q = db.query(Transaction).order_by(Transaction.id.desc())
    if profile:
        p = db.query(Profile).filter_by(name=profile).first()
        q = q.filter_by(profile_id=p.id) if p else q.filter(False)
    if direction:
        q = q.filter_by(direction=direction)
    return [
        {
            "id": t.id, "profile_id": t.profile_id, "candidate_id": t.candidate_id,
            "direction": t.direction, "action": t.action, "method": t.method,
            "url": t.url, "request_headers": t.request_headers,
            "request_body": t.request_body, "response_status": t.response_status,
            "response_headers": t.response_headers, "response_body": t.response_body,
            "dry_run": bool(t.dry_run), "error": t.error, "created_at": str(t.created_at),
        }
        for t in q.limit(min(limit, 500)).all()
    ]
