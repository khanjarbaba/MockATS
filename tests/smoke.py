"""End-to-end smoke test using FastAPI's TestClient. No real SHL calls.

Run:  python -m tests.smoke   (from mock-ats/)
"""

import hashlib
import hmac
import json
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")
os.environ["SHL_BASE_URL"] = "https://api.shl.example.com"
os.environ["CALLBACK_BASE_URL"] = "https://mock.ngrok-free.app"
os.environ["SHL_API_KEY"] = "test-key-123"
os.environ["SHL_WEBHOOK_SECRET"] = "whsec"
os.environ["ARMED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)
PASS = []


def check(name, cond, detail=""):
    PASS.append((name, bool(cond)))
    print(("✓" if cond else "✗ FAIL"), name, ("— " + str(detail)[:200] if detail and not cond else ""))


# 1. status + seed
r = client.get("/api/status")
check("status endpoint", r.status_code == 200 and r.json()["armed"] is False, r.text)

r = client.get("/api/profiles")
check("greenhouse seeded", r.status_code == 200 and any(p["name"] == "greenhouse" for p in r.json()), r.text)

# 2. dry-run simulate on seeded greenhouse
r = client.post("/api/simulate", json={
    "profile": "greenhouse", "action": "send_invite",
    "candidate": {"first_name": "Jane", "last_name": "Doe", "name": "Jane Doe",
                  "email": "jane.doe@example.com", "external_ref": "REQ-42"},
    "inputs": {"inputs": {"package_id": "PKG-7"}},
    "dry_run": True,
})
body = r.json()
req_body = body.get("request", {}).get("body", {}) if r.status_code == 200 else {}
check("dry-run simulate 200", r.status_code == 200 and body.get("dry_run"), r.text)
check("template: email substituted", req_body.get("candidate", {}).get("email") == "jane.doe@example.com", req_body)
check("template: callback_url injected",
      req_body.get("callback_url") == "https://mock.ngrok-free.app/webhooks/greenhouse", req_body)
check("template: uuid generated", len(str(req_body.get("order_id", ""))) == 36, req_body)
check("template: package_id via inputs", req_body.get("assessment_package_id") == "PKG-7", req_body)
check("auth header applied+redacted",
      "X-API-Key" in body.get("request", {}).get("headers", {}), body.get("request", {}).get("headers"))
check("url from env", body["request"]["url"].startswith("https://api.shl.example.com/"), body["request"]["url"])

candidate_id = body.get("candidate_id")
check("candidate created", isinstance(candidate_id, int), body)

# 3. real send blocked when not armed
r = client.post("/api/simulate", json={
    "profile": "greenhouse", "action": "send_invite",
    "candidate": {"email": "jane.doe@example.com"}, "dry_run": False, "confirm": True,
})
check("real send blocked when unarmed", r.status_code == 403, r.text)

# 4. webhook: completed event updates candidate
payload = {"event": "assessment.completed",
           "candidate": {"email": "jane.doe@example.com"},
           "results": {"report_url": "https://reports.shl.example.com/r/xyz", "score": 87},
           "assessment": {"link": "https://take.shl.example.com/a/abc"}}
r = client.post("/webhooks/greenhouse", json=payload)
j = r.json()
check("webhook received", r.status_code == 200 and j.get("received"), r.text)
check("webhook matched candidate", j.get("candidate_matched") == candidate_id, j)
check("webhook status mapped", j.get("applied_changes", {}).get("status") == "completed", j)

r = client.get("/api/candidates")
cand = next((c for c in r.json() if c["id"] == candidate_id), {})
check("candidate results_link set", cand.get("results_link") == "https://reports.shl.example.com/r/xyz", cand)
check("candidate status completed", cand.get("status") == "completed", cand)

# 5. HMAC-verified webhook
gh = client.get("/api/profiles/greenhouse").json()
cfg = gh["config"]
cfg["webhook"]["verification"] = {"type": "hmac", "header": "X-SHL-Signature",
                                  "secret_env": "SHL_WEBHOOK_SECRET",
                                  "algorithm": "sha256", "encoding": "hex"}
r = client.put("/api/profiles/greenhouse", json={"name": "greenhouse", "display_name": gh["display_name"],
                                                 "status": "active", "config": cfg})
check("profile update (add hmac verification)", r.status_code == 200, r.text)

raw = json.dumps({"event": "assessment.started", "candidate": {"email": "jane.doe@example.com"}}).encode()
sig = hmac.new(b"whsec", raw, hashlib.sha256).hexdigest()
r = client.post("/webhooks/greenhouse", content=raw,
                headers={"Content-Type": "application/json", "X-SHL-Signature": sig})
check("hmac webhook verified", r.status_code == 200 and r.json().get("signature_verified") is True, r.text)
r2 = client.post("/webhooks/greenhouse", content=raw,
                 headers={"Content-Type": "application/json", "X-SHL-Signature": "bad"})
check("bad hmac flagged unverified", r2.json().get("signature_verified") is False, r2.text)

# 6. teach a NEW profile from samples (the extensibility path)
r = client.post("/api/profiles", json={"name": "workday", "display_name": "Workday"})
check("create workday profile", r.status_code == 200, r.text)

samples = [
    {"kind": "request", "action": "send_invite", "method": "POST",
     "url": "https://api.shl.example.com/v2/invites",
     "headers": {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.sig",
                 "Content-Type": "application/json", "X-Request-Id": "7c9e6679-7425-40de-944b-e07fc1f90ae7"},
     "body": {"invite_id": "123e4567-e89b-12d3-a456-426614174000",
              "candidateEmail": "bob@corp.com", "firstName": "Bob", "lastName": "Stone",
              "requisitionId": "R-1001", "notificationUrl": "https://ats.corp.com/hooks/shl",
              "sentAt": "2026-07-01T10:00:00Z", "locale": "en-US"}},
    {"kind": "response", "action": "send_invite",
     "body": {"status": "created", "assessment": {"url": "https://take.shl.example.com/t/999",
                                                  "id": "ord-999", "expires_at": "2026-08-01T00:00:00Z"}}},
    {"kind": "webhook",
     "body": {"event_type": "completed", "candidate": {"email": "bob@corp.com"},
              "report": {"report_url": "https://reports.shl.example.com/r/bob"}}},
]
r = client.post("/api/profiles/workday/infer", json={"samples": samples})
j = r.json() if r.status_code == 200 else {}
check("inference 200", r.status_code == 200, r.text)
action = j.get("inferred", {}).get("actions", {}).get("send_invite", {})
bt = action.get("body_template", {})
check("inference: email → placeholder", bt.get("candidateEmail") == "{{candidate.email}}", bt)
check("inference: uuid → placeholder", bt.get("invite_id") == "{{generated_uuid}}", bt)
check("inference: callback url detected", bt.get("notificationUrl") == "{{callback_url}}", bt)
check("inference: timestamp detected", bt.get("sentAt") == "{{timestamp}}", bt)
check("inference: requisition mapped", bt.get("requisitionId") == "{{candidate.external_ref}}", bt)
check("inference: static kept", bt.get("locale") == "en-US", bt)
check("inference: oauth2 auth detected", action.get("auth", {}).get("type") == "oauth2_cc", action.get("auth"))
check("inference: url env-templated", action.get("url", "").startswith("{{env.SHL_BASE_URL}}"), action.get("url"))
check("inference: response mapping link",
      action.get("response_mapping", {}).get("assessment_link") == "$.assessment.url", action.get("response_mapping"))
wh = j.get("inferred", {}).get("webhook", {})
check("inference: webhook event path", wh.get("event_type_path") == "$.event_type", wh)
check("inference: webhook match by email", wh.get("match_candidate_by") == "$.candidate.email", wh)
check("inference: inputs generated", any(i["name"] == "candidate.email" for i in action.get("inputs", [])), action.get("inputs"))

# 7. raw HTTP sample parsing
raw_http = ("POST /v1/orders HTTP/1.1\nHost: api.shl.example.com\nX-API-Key: sk_test_abc\n"
            "Content-Type: application/json\n\n{\"email\": \"amy@x.io\", \"job_id\": \"J-9\"}")
r = client.post("/api/profiles/workday/infer",
                json={"samples": [{"kind": "request", "action": "check_status", "raw": raw_http}]})
j = r.json() if r.status_code == 200 else {}
a2 = j.get("inferred", {}).get("actions", {}).get("check_status", {})
check("raw http parsed", a2.get("method") == "POST" and "{{env.SHL_BASE_URL}}" in a2.get("url", ""), a2)
check("raw http: api_key auth", a2.get("auth", {}).get("type") == "api_key", a2.get("auth"))

# 8. logs
r = client.get("/api/logs")
logs = r.json()
check("logs recorded", len(logs) >= 4, len(logs))
check("log directions", {t["direction"] for t in logs} >= {"outbound", "webhook"}, {t["direction"] for t in logs})
r = client.get("/api/webhook-events")
check("webhook events recorded", len(r.json()) >= 3, r.text)

# 9. frontend served
r = client.get("/")
check("frontend served", r.status_code == 200 and "Mock ATS" in r.text)

failed = [n for n, ok in PASS if not ok]
print(f"\n{len(PASS) - len(failed)}/{len(PASS)} checks passed")
if failed:
    raise SystemExit("FAILED: " + ", ".join(failed))
print("ALL GREEN")
