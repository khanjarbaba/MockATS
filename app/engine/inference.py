"""Infer a draft ATS Profile action from uploaded real sample traffic.

Input samples are either:
  * a JSON object: {"kind": "request"|"response"|"webhook", "action": "...",
    "method": "...", "url": "...", "headers": {...}, "body": {...}}
  * raw HTTP text (request line + headers + blank line + body)

Output: a draft action config with dynamic values replaced by {{placeholders}}
plus an ``inputs`` list, and classification notes so the user can review every
decision in the editable profile UI.
"""

import json
import re
from typing import Any
from urllib.parse import urlsplit

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
EPOCH_RE = re.compile(r"^1[5-9]\d{8,11}$")  # plausible unix epoch (s or ms)
JWT_RE = re.compile(r"^eyJ[\w-]+\.[\w-]+\.[\w-]*$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9+/_\-=]{24,}$")
URL_RE = re.compile(r"^https?://", re.IGNORECASE)

AUTH_HEADERS = {"authorization", "x-api-key", "api-key", "x-auth-token"}
SENSITIVE_HEADERS = AUTH_HEADERS | {"cookie", "x-signature", "x-hub-signature", "x-shl-signature"}
SKIP_HEADERS = {"content-length", "host", "connection", "accept-encoding", "user-agent"}

# field-name hints → template variable
NAME_HINTS = [
    (re.compile(r"e[-_]?mail", re.I), "candidate.email"),
    (re.compile(r"first[-_ ]?name", re.I), "candidate.first_name"),
    (re.compile(r"last[-_ ]?name|surname", re.I), "candidate.last_name"),
    (re.compile(r"full[-_ ]?name|candidate[-_ ]?name|^name$", re.I), "candidate.name"),
    (re.compile(r"requisition|req[-_ ]?id|job[-_ ]?id|position", re.I), "candidate.external_ref"),
    (re.compile(r"callback|webhook|notif", re.I), "callback_url"),
    (re.compile(r"idempoten", re.I), "idempotency_key"),
]


def classify_value(key: str, value: Any) -> tuple[str | None, str]:
    """Return (template_var or None, reason). None means keep static."""
    key_l = key.lower() if isinstance(key, str) else ""
    sval = value if isinstance(value, str) else None

    for pattern, var in NAME_HINTS:
        if pattern.search(key_l):
            if var == "callback_url" and sval and not URL_RE.match(sval):
                continue
            return var, f"field name matches '{pattern.pattern}'"

    if sval is None:
        return None, "non-string value, kept static"
    if UUID_RE.match(sval):
        return "generated_uuid", "value is a UUID"
    if EMAIL_RE.match(sval):
        return "candidate.email", "value is an email address"
    if ISO_TS_RE.match(sval):
        return "timestamp", "value is an ISO timestamp"
    if EPOCH_RE.match(sval):
        return "timestamp_epoch", "value looks like a unix epoch"
    if JWT_RE.match(sval):
        return None, "JWT detected — handled by auth config, not template"
    if URL_RE.match(sval) and ("token" in sval or "callback" in key_l or "webhook" in key_l):
        return "callback_url", "URL containing token / callback-ish field"
    if TOKEN_RE.match(sval) and len(sval) >= 32 and any(h in key_l for h in ("token", "key", "secret", "signature")):
        return None, "opaque token — likely auth material, handled by auth config"
    return None, "kept static"


def templatize_body(body: Any, notes: list[dict], path: str = "$") -> Any:
    if isinstance(body, dict):
        out = {}
        for k, v in body.items():
            child_path = f"{path}.{k}"
            if isinstance(v, (dict, list)):
                out[k] = templatize_body(v, notes, child_path)
            else:
                var, reason = classify_value(k, v)
                if var:
                    out[k] = "{{" + var + "}}"
                    notes.append({"path": child_path, "sample_value": v, "mapped_to": var, "reason": reason})
                else:
                    out[k] = v
                    notes.append({"path": child_path, "sample_value": v, "mapped_to": None, "reason": reason})
        return out
    if isinstance(body, list):
        return [templatize_body(v, notes, f"{path}.{i}") for i, v in enumerate(body)]
    return body


def infer_auth(headers: dict) -> dict:
    for name, value in headers.items():
        n = name.lower()
        if n == "authorization" and isinstance(value, str):
            if value.lower().startswith("basic "):
                return {"type": "basic", "username_env": "SHL_CLIENT_ID", "password_env": "SHL_CLIENT_SECRET"}
            if value.lower().startswith("bearer "):
                token = value.split(" ", 1)[1]
                if JWT_RE.match(token):
                    return {
                        "type": "oauth2_cc",
                        "token_url_env": "SHL_OAUTH_TOKEN_URL",
                        "client_id_env": "SHL_CLIENT_ID",
                        "client_secret_env": "SHL_CLIENT_SECRET",
                        "scope": "",
                    }
                return {"type": "bearer_static", "token_env": "SHL_API_KEY"}
        if n in ("x-api-key", "api-key", "x-auth-token"):
            return {"type": "api_key", "header": name, "value_env": "SHL_API_KEY"}
        if "signature" in n:
            return {
                "type": "hmac",
                "header": name,
                "secret_env": "SHL_HMAC_SECRET",
                "algorithm": "sha256",
                "payload": "body",
                "encoding": "hex",
            }
    return {"type": "none"}


def parse_raw_http(raw: str) -> dict:
    """Parse a raw HTTP request/response capture into structured form."""
    head, _, body_text = raw.replace("\r\n", "\n").partition("\n\n")
    lines = [line for line in head.split("\n") if line.strip()]
    first = lines[0].strip()
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()

    body: Any = body_text.strip()
    if body:
        try:
            body = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            pass

    if first.upper().startswith("HTTP/"):
        parts = first.split(" ")
        return {"kind": "response", "status": int(parts[1]), "headers": headers, "body": body}

    method, _, rest = first.partition(" ")
    target = rest.rsplit(" HTTP/", 1)[0].strip()
    if not URL_RE.match(target):
        host = headers.get("Host", headers.get("host", "unknown.host"))
        target = f"https://{host}{target}"
    return {"kind": "request", "method": method.upper(), "url": target, "headers": headers, "body": body}


def infer_action_from_samples(action_name: str, request_sample: dict, response_sample: dict | None) -> dict:
    """Build a draft action config from a request (+ optional response) sample."""
    notes: list[dict] = []
    headers_in = request_sample.get("headers", {}) or {}
    auth = infer_auth(headers_in)

    headers_out: dict[str, str] = {}
    for name, value in headers_in.items():
        n = name.lower()
        if n in SKIP_HEADERS or n in SENSITIVE_HEADERS:
            continue  # auth headers are regenerated by auth config
        var, reason = classify_value(name, value)
        if var:
            headers_out[name] = "{{" + var + "}}"
            notes.append({"path": f"header:{name}", "sample_value": value, "mapped_to": var, "reason": reason})
        else:
            headers_out[name] = value

    url = request_sample.get("url", "")
    split = urlsplit(url)
    base = f"{split.scheme}://{split.netloc}"
    templated_url = url.replace(base, "{{env.SHL_BASE_URL}}") if base != "://" else url
    # templatize UUIDs embedded in the path
    templated_url = re.sub(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        "{{candidate.external_ref}}",
        templated_url,
    )

    body_template = templatize_body(request_sample.get("body"), notes)

    response_mapping: dict[str, str] = {}
    if response_sample:
        resp_body = response_sample.get("body")
        if isinstance(resp_body, dict):
            response_mapping = infer_response_mapping(resp_body)

    from .templating import find_placeholders

    placeholders = find_placeholders({"url": templated_url, "headers": headers_out, "body": body_template})
    inputs = sorted(p for p in placeholders if p.startswith("candidate."))

    return {
        "method": request_sample.get("method", "POST"),
        "url": templated_url,
        "headers": headers_out,
        "auth": auth,
        "body_template": body_template,
        "response_mapping": response_mapping,
        "inputs": [{"name": p, "label": p.split(".", 1)[1].replace("_", " ").title(), "required": True} for p in inputs],
        "inference_notes": notes,
        "sample": {
            "request": request_sample,
            "response": response_sample,
        },
    }


def infer_response_mapping(resp_body: dict, prefix: str = "$") -> dict:
    """Guess which response fields matter: assessment link, status, expiry."""
    mapping: dict[str, str] = {}

    def walk(node: Any, path: str):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}.{i}")
        elif isinstance(node, str):
            k = path.rsplit(".", 1)[-1].lower()
            if URL_RE.match(node) and ("link" in k or "url" in k):
                if "result" in k or "report" in k:
                    mapping.setdefault("results_link", path)
                else:
                    mapping.setdefault("assessment_link", path)
            elif k in ("status", "state"):
                mapping.setdefault("status", path)
            elif "expir" in k or ISO_TS_RE.match(node) and "expir" in path.lower():
                mapping.setdefault("expiry", path)
            elif k in ("id", "assessmentid", "assessment_id", "order_id", "orderid"):
                mapping.setdefault("provider_ref", path)

    walk(resp_body, prefix)
    return mapping


def infer_webhook_contract(webhook_sample: dict) -> dict:
    """Draft the webhook contract from a real SHL→ATS webhook payload."""
    body = webhook_sample.get("body") if isinstance(webhook_sample, dict) else webhook_sample
    if not isinstance(body, dict):
        body = {}

    event_path = ""
    match_path = ""
    mapping: dict[str, str] = {}

    def walk(node: Any, path: str):
        nonlocal event_path, match_path
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}.{i}")
        elif isinstance(node, str):
            k = path.rsplit(".", 1)[-1].lower()
            if not event_path and k in ("event", "event_type", "eventtype", "type", "status"):
                event_path = path
            if not match_path and EMAIL_RE.match(node):
                match_path = path
            if URL_RE.match(node) and ("result" in k or "report" in k):
                mapping.setdefault("results_link", path)
            elif URL_RE.match(node) and ("link" in k or "url" in k):
                mapping.setdefault("assessment_link", path)

    walk(body, "$")

    return {
        "event_type_path": event_path or "$.event",
        "match_candidate_by": match_path or "$.candidate.email",
        "match_field": "email",
        "mappings": mapping,
        "status_map": {
            "invited": "invited",
            "started": "started",
            "in_progress": "started",
            "completed": "completed",
            "scored": "completed",
        },
        "verification": {"type": "none", "note": "set to hmac + header + secret_env if SHL signs webhooks"},
        "sample": body,
    }
