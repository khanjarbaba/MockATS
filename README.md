# Mock ATS Simulator

Stands in for a real ATS (Greenhouse first; Workday/Oracle/SAP later) talking to the **real** SHL API. The ATS side is mocked; SHL is not.

## Quick start

```bash
cd mock-ats
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in SHL endpoints/creds when you have them
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000. A seed **greenhouse** profile (placeholder shapes) is created on first run.

For webhooks, expose the app and put the tunnel URL in `.env`:

```bash
ngrok http 8000
# .env → CALLBACK_BASE_URL=https://<your-id>.ngrok-free.app
```

SHL should call `POST {CALLBACK_BASE_URL}/webhooks/greenhouse` — that URL is auto-injected into outgoing payloads wherever a profile uses `{{callback_url}}`.

## Deploy to Render (shared/hosted)

The repo includes a [render.yaml](render.yaml) blueprint: web service + free Postgres, no code changes.

1. Push to GitHub, then in Render: **New → Blueprint** → pick this repo → Apply.
2. Render provisions Postgres, wires `DATABASE_URL`, and generates a random `APP_PASSWORD` — find it under the service's **Environment** tab and share it with colleagues (they log in with **any username** + that password).
3. Webhooks need no tunnel: the public service URL is used as the callback base automatically (`RENDER_EXTERNAL_URL`). Point SHL at `https://<service>.onrender.com/webhooks/greenhouse`.
4. Fill in the `SHL_*` env vars in the dashboard when you have credentials. Keep `ARMED=false` until you mean it.

Caveats: the free web service sleeps when idle (~30 s cold start on the next request), and Render's **free Postgres expires after 30 days** — upgrade the database plan if the data needs to outlive a demo. `/webhooks/*` is intentionally not behind the password (SHL can't authenticate); use profile HMAC verification instead.

## Safety model (read this)

There is **no SHL sandbox** — every non-dry-run send hits a real endpoint. Two independent switches must both be on:

1. `.env`: `ARMED=true`
2. Per request: the "confirm real send" checkbox (API: `confirm: true`)

Otherwise `/api/simulate` only builds + logs the request (`dry_run`). The UI defaults to dry-run.

## Teaching a profile from real samples

Profiles tab → select profile → paste a JSON array of samples → **Infer draft from samples**. Sample formats:

```json
[
  {"kind": "request", "action": "send_invite", "method": "POST",
   "url": "https://api.shl.com/v1/orders",
   "headers": {"X-API-Key": "sk_live_...", "Content-Type": "application/json"},
   "body": {"candidate": {"email": "jane@x.com"}, "callback_url": "https://ats.example/hook"}},
  {"kind": "response", "action": "send_invite", "status": 201,
   "body": {"assessment": {"link": "https://take.shl.com/a/abc123", "id": "9f3..."}}},
  {"kind": "webhook",
   "body": {"event": "assessment.completed", "candidate": {"email": "jane@x.com"},
            "results": {"report_url": "https://reports.shl.com/r/xyz"}}}
]
```

Raw HTTP captures also work: `{"kind":"request","action":"send_invite","raw":"POST /v1/orders HTTP/1.1\nHost: api.shl.com\nX-API-Key: ...\n\n{...}"}`.

The inference engine classifies dynamic vs static fields (UUIDs, emails, timestamps, tokens), strips auth headers into an `auth` config, and produces `{{placeholders}}` plus an `inputs` form definition. Every decision is listed in `inference_notes`. **Review and hand-edit the draft config**, then Save (this flips the profile to `active`).

## Profile config reference

```jsonc
{
  "actions": {
    "send_invite": {
      "method": "POST",
      "url": "{{env.SHL_BASE_URL}}/assessments/orders",
      "headers": { "Content-Type": "application/json" },
      "auth": { "type": "api_key", "header": "X-API-Key", "value_env": "SHL_API_KEY" },
      "body_template": { "email": "{{candidate.email}}", "callback_url": "{{callback_url}}",
                          "order_id": "{{generated_uuid}}", "ts": "{{timestamp}}" },
      "response_mapping": { "assessment_link": "$.assessment.link", "status": "$.status" },
      "inputs": [ { "name": "candidate.email", "label": "Email", "required": true } ]
    }
  },
  "webhook": {
    "event_type_path": "$.event",
    "match_candidate_by": "$.candidate.email",
    "match_field": "email",
    "mappings": { "results_link": "$.results.report_url" },
    "status_map": { "assessment.completed": "completed" },
    "verification": { "type": "hmac", "header": "X-SHL-Signature",
                       "secret_env": "SHL_WEBHOOK_SECRET", "algorithm": "sha256", "encoding": "hex" }
  }
}
```

Template variables: `{{candidate.*}}` (form inputs), `{{env.VAR}}` (from `.env`), `{{callback_url}}`, `{{generated_uuid}}`, `{{idempotency_key}}`, `{{timestamp}}` (ISO UTC), `{{timestamp_epoch}}`.

Auth types (secrets always via env var names, never inline): `none`, `api_key`, `bearer_static`, `basic`, `oauth2_cc` (fetches + caches a client-credentials token at request time), `hmac` (signs the body at request time).

## Adding a new ATS (Workday, Oracle, SAP…)

1. Profiles tab → create profile with a new slug, e.g. `workday`.
2. Paste that ATS's real captured samples → Infer → review/edit → Save.
3. Point SHL callbacks at `{CALLBACK_BASE_URL}/webhooks/workday`.

No code changes. All behavior differences live in the profile config.

## API surface

| Method & path | Purpose |
|---|---|
| `GET/POST /api/profiles`, `GET/PUT/DELETE /api/profiles/{name}` | profile CRUD |
| `POST /api/profiles/{name}/infer` | teach from samples |
| `POST /api/simulate` | build (+optionally send) a request to SHL |
| `GET /api/candidates` | candidate states |
| `POST /webhooks/{profile}[/subpath]` | SHL → ATS webhook receiver |
| `GET /api/webhook-events` | parsed webhook events |
| `GET /api/logs?direction=outbound|webhook` | full request/response log |
| `GET /api/status` | armed state + configured URLs |

## Project structure

```
app/
  main.py            # FastAPI wiring, seed, static frontend
  db.py, models.py   # SQLite via SQLAlchemy: profiles, candidates, transactions, webhook_events
  seed.py            # Greenhouse placeholder profile (replace via sample upload)
  engine/
    templating.py    # {{var}} rendering + dot-path extraction
    inference.py     # samples → draft profile (dynamic-field heuristics)
    auth.py          # live auth generation + webhook HMAC verification
    sender.py        # request builder/sender, dry-run + ARMED guard, logging
  routers/           # profiles, simulate, webhooks, logs
  static/index.html  # UI: Simulate / Profiles / Candidates / Logs / Webhook Events
```
