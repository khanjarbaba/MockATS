"""Build and send the real HTTP request to SHL, exactly as the profile defines.

Safety model (there is NO SHL sandbox — every send is real):
  * dry_run=True builds and logs the request without sending. Default in UI.
  * A real send requires BOTH env ARMED=true and confirm=True on the call.
"""

import json
import os
from typing import Any

import httpx
from sqlalchemy.orm import Session

from ..models import Candidate, Transaction
from .auth import apply_auth
from .templating import get_path, render


class SendBlocked(Exception):
    pass


def build_request(action_cfg: dict, context: dict) -> dict:
    url = render(action_cfg.get("url", ""), context)
    headers = render(action_cfg.get("headers", {}), context)
    body = render(action_cfg.get("body_template"), context)
    body_bytes = b""
    if body is not None:
        body_bytes = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    headers = apply_auth(action_cfg.get("auth"), headers, body_bytes)
    return {
        "method": action_cfg.get("method", "POST").upper(),
        "url": url,
        "headers": headers,
        "body": body,
        "body_bytes": body_bytes,
    }


def redact(headers: dict) -> dict:
    out = {}
    for k, v in headers.items():
        if k.lower() in ("authorization", "x-api-key", "api-key", "x-auth-token", "cookie"):
            out[k] = v[:12] + "…(redacted)" if isinstance(v, str) and len(v) > 12 else "(redacted)"
        else:
            out[k] = v
    return out


def apply_response_mapping(candidate: Candidate | None, mapping: dict, resp_json: Any) -> dict:
    changes: dict = {}
    if not candidate or not isinstance(mapping, dict) or resp_json is None:
        return changes
    for field, path in mapping.items():
        value = get_path(resp_json, path)
        if value is None:
            continue
        if field == "assessment_link":
            candidate.assessment_link = str(value)
        elif field == "results_link":
            candidate.results_link = str(value)
        elif field == "status":
            candidate.status = str(value)
        else:
            candidate.extra = {**(candidate.extra or {}), field: value}
        changes[field] = value
    return changes


def execute_action(
    db: Session,
    profile,
    action_name: str,
    action_cfg: dict,
    candidate: Candidate | None,
    inputs: dict,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict:
    context = {
        "candidate": {
            **(candidate.extra or {} if candidate else {}),
            **inputs.get("candidate", {}),
            **(
                {
                    "id": candidate.id,
                    "name": candidate.name,
                    "email": candidate.email,
                    "external_ref": candidate.external_ref,
                }
                if candidate
                else {}
            ),
        },
        "callback_url": (
            os.getenv("CALLBACK_BASE_URL")
            or os.getenv("RENDER_EXTERNAL_URL")  # set automatically on Render
            or "http://localhost:8000"
        ).rstrip("/")
        + f"/webhooks/{profile.name}",
        **{k: v for k, v in inputs.items() if k != "candidate"},
    }

    built = build_request(action_cfg, context)

    txn = Transaction(
        profile_id=profile.id,
        candidate_id=candidate.id if candidate else None,
        direction="outbound",
        action=action_name,
        method=built["method"],
        url=built["url"],
        request_headers=redact(built["headers"]),
        request_body=built["body_bytes"].decode() if built["body_bytes"] else "",
        dry_run=1 if dry_run else 0,
    )

    if dry_run:
        db.add(txn)
        db.commit()
        return {"dry_run": True, "transaction_id": txn.id, "request": {
            "method": built["method"], "url": built["url"],
            "headers": redact(built["headers"]), "body": built["body"],
        }}

    armed = os.getenv("ARMED", "false").lower() == "true"
    if not (armed and confirm):
        raise SendBlocked(
            "Real send blocked. Set ARMED=true in .env AND pass confirm=true. "
            "There is no SHL sandbox — this would hit a real endpoint."
        )

    resp_json: Any = None
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.request(
                built["method"], built["url"], headers=built["headers"],
                content=built["body_bytes"] or None,
            )
        txn.response_status = resp.status_code
        txn.response_headers = dict(resp.headers)
        txn.response_body = resp.text
        try:
            resp_json = resp.json()
        except (json.JSONDecodeError, ValueError):
            resp_json = None
    except httpx.HTTPError as exc:
        txn.error = str(exc)
        db.add(txn)
        db.commit()
        return {"error": str(exc), "transaction_id": txn.id}

    changes = apply_response_mapping(candidate, action_cfg.get("response_mapping", {}), resp_json)
    if candidate and txn.response_status and 200 <= txn.response_status < 300 and "status" not in changes:
        if action_name in ("send_invite", "create_invite", "assessment_invite"):
            candidate.status = "invited"

    db.add(txn)
    db.commit()
    return {
        "transaction_id": txn.id,
        "status_code": txn.response_status,
        "response": resp_json if resp_json is not None else txn.response_body,
        "candidate_changes": changes,
        "request": {"method": built["method"], "url": built["url"],
                    "headers": redact(built["headers"]), "body": built["body"]},
    }
