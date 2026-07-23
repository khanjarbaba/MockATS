"""Seed the Greenhouse reference profile (synthetic placeholder shapes).

This is a PLACEHOLDER built from typical assessment-partner integration shapes.
Replace it by uploading your real captured samples via
POST /api/profiles/greenhouse/infer — that will overwrite these drafts with
profiles inferred from real traffic.
"""

from sqlalchemy.orm import Session

from .models import Profile

GREENHOUSE_CONFIG = {
    "actions": {
        "send_invite": {
            "method": "POST",
            "url": "{{env.SHL_BASE_URL}}/assessments/orders",
            "headers": {"Content-Type": "application/json", "Accept": "application/json"},
            "auth": {"type": "api_key", "header": "X-API-Key", "value_env": "SHL_API_KEY"},
            "body_template": {
                "order_id": "{{generated_uuid}}",
                "requested_at": "{{timestamp}}",
                "candidate": {
                    "first_name": "{{candidate.first_name}}",
                    "last_name": "{{candidate.last_name}}",
                    "email": "{{candidate.email}}",
                },
                "requisition_id": "{{candidate.external_ref}}",
                "assessment_package_id": "{{inputs.package_id}}",
                "callback_url": "{{callback_url}}",
            },
            "response_mapping": {
                "assessment_link": "$.assessment.link",
                "provider_ref": "$.assessment.id",
                "expiry": "$.assessment.expires_at",
                "status": "$.status",
            },
            "inputs": [
                {"name": "candidate.first_name", "label": "First Name", "required": True},
                {"name": "candidate.last_name", "label": "Last Name", "required": True},
                {"name": "candidate.email", "label": "Email", "required": True},
                {"name": "candidate.external_ref", "label": "Requisition / Job ID", "required": True},
                {"name": "inputs.package_id", "label": "Assessment Package ID", "required": False},
            ],
            "inference_notes": [{"path": "$", "reason": "SEED PLACEHOLDER — replace via sample upload"}],
        },
        "check_status": {
            "method": "GET",
            "url": "{{env.SHL_BASE_URL}}/assessments/orders/{{candidate.provider_ref}}",
            "headers": {"Accept": "application/json"},
            "auth": {"type": "api_key", "header": "X-API-Key", "value_env": "SHL_API_KEY"},
            "body_template": None,
            "response_mapping": {"status": "$.status", "results_link": "$.results.report_url"},
            "inputs": [{"name": "candidate.provider_ref", "label": "SHL Order/Assessment ID", "required": True}],
            "inference_notes": [{"path": "$", "reason": "SEED PLACEHOLDER — replace via sample upload"}],
        },
    },
    "webhook": {
        "event_type_path": "$.event",
        "match_candidate_by": "$.candidate.email",
        "match_field": "email",
        "mappings": {
            "assessment_link": "$.assessment.link",
            "results_link": "$.results.report_url",
            "score": "$.results.score",
        },
        "status_map": {
            "assessment.invited": "invited",
            "assessment.started": "started",
            "assessment.completed": "completed",
            "assessment.scored": "completed",
            "invited": "invited",
            "started": "started",
            "completed": "completed",
        },
        "verification": {
            "type": "none",
            "note": "If SHL signs webhooks set: type=hmac, header, secret_env=SHL_WEBHOOK_SECRET, algorithm, encoding",
        },
    },
}


def seed(db: Session):
    if not db.query(Profile).filter_by(name="greenhouse").first():
        db.add(Profile(
            name="greenhouse",
            display_name="Greenhouse (reference — replace via sample upload)",
            status="draft",
            config=GREENHOUSE_CONFIG,
        ))
        db.commit()
