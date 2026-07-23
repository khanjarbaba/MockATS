"""Live auth generation at request time.

Auth config lives in the profile action ("auth" key). Secrets are referenced
by env var NAME, never stored in the profile. Supported types:

  none          — nothing added
  api_key       — {"type":"api_key","header":"X-API-Key","value_env":"SHL_API_KEY"}
  bearer_static — {"type":"bearer_static","token_env":"SHL_API_KEY"}
  basic         — {"type":"basic","username_env":"...","password_env":"..."}
  oauth2_cc     — {"type":"oauth2_cc","token_url_env":"SHL_OAUTH_TOKEN_URL",
                   "client_id_env":"...","client_secret_env":"...","scope":""}
  hmac          — {"type":"hmac","header":"X-Signature","secret_env":"...",
                   "algorithm":"sha256","payload":"body","encoding":"hex",
                   "prefix":""}
"""

import base64
import hashlib
import hmac as hmac_mod
import os
import time

import httpx

_token_cache: dict[str, tuple[str, float]] = {}


class AuthError(Exception):
    pass


def _env(cfg: dict, key: str, required: bool = True) -> str:
    env_name = cfg.get(key, "")
    value = os.getenv(env_name, "") if env_name else ""
    if required and not value:
        raise AuthError(f"auth config needs env var '{env_name or key}' to be set in .env")
    return value


def _oauth2_token(cfg: dict) -> str:
    token_url = _env(cfg, "token_url_env")
    client_id = _env(cfg, "client_id_env")
    client_secret = _env(cfg, "client_secret_env")
    cache_key = f"{token_url}:{client_id}:{cfg.get('scope', '')}"

    cached = _token_cache.get(cache_key)
    if cached and cached[1] > time.time() + 30:
        return cached[0]

    data = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}
    if cfg.get("scope"):
        data["scope"] = cfg["scope"]
    resp = httpx.post(token_url, data=data, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    token = payload.get("access_token", "")
    if not token:
        raise AuthError(f"token endpoint returned no access_token: {payload}")
    expires = time.time() + float(payload.get("expires_in", 300))
    _token_cache[cache_key] = (token, expires)
    return token


def apply_auth(auth_cfg: dict | None, headers: dict, body_bytes: bytes) -> dict:
    """Return headers with live auth applied."""
    headers = dict(headers)
    if not auth_cfg or auth_cfg.get("type") in (None, "", "none"):
        return headers

    kind = auth_cfg["type"]

    if kind == "api_key":
        headers[auth_cfg.get("header", "X-API-Key")] = _env(auth_cfg, "value_env")
    elif kind == "bearer_static":
        headers["Authorization"] = f"Bearer {_env(auth_cfg, 'token_env')}"
    elif kind == "basic":
        userpass = f"{_env(auth_cfg, 'username_env')}:{_env(auth_cfg, 'password_env')}"
        headers["Authorization"] = "Basic " + base64.b64encode(userpass.encode()).decode()
    elif kind == "oauth2_cc":
        headers["Authorization"] = f"Bearer {_oauth2_token(auth_cfg)}"
    elif kind == "hmac":
        secret = _env(auth_cfg, "secret_env").encode()
        algo = auth_cfg.get("algorithm", "sha256")
        digest = hmac_mod.new(secret, body_bytes, getattr(hashlib, algo)).digest()
        encoded = digest.hex() if auth_cfg.get("encoding", "hex") == "hex" else base64.b64encode(digest).decode()
        headers[auth_cfg.get("header", "X-Signature")] = auth_cfg.get("prefix", "") + encoded
    else:
        raise AuthError(f"unknown auth type: {kind}")
    return headers


def verify_hmac(verification: dict, headers: dict, body_bytes: bytes) -> bool:
    """Verify an inbound webhook signature per the profile's webhook config."""
    secret = os.getenv(verification.get("secret_env", ""), "")
    if not secret:
        return False
    header_name = verification.get("header", "X-Signature")
    provided = ""
    for k, v in headers.items():
        if k.lower() == header_name.lower():
            provided = v
            break
    if not provided:
        return False
    prefix = verification.get("prefix", "")
    if prefix and provided.startswith(prefix):
        provided = provided[len(prefix):]
    algo = verification.get("algorithm", "sha256")
    digest = hmac_mod.new(secret.encode(), body_bytes, getattr(hashlib, algo)).digest()
    expected = digest.hex() if verification.get("encoding", "hex") == "hex" else base64.b64encode(digest).decode()
    return hmac_mod.compare_digest(expected, provided)
