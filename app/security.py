"""Shared-password gate for hosted deployments.

If APP_PASSWORD is set, every route requires HTTP Basic auth (any username,
that password). Exempt:
  * /webhooks/*  — SHL can't authenticate; profiles verify via HMAC instead
  * /healthz     — platform health checks
If APP_PASSWORD is unset (local dev), no auth is enforced.
"""

import base64
import binascii
import os
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse

EXEMPT_PREFIXES = ("/webhooks/", "/healthz")


def _password_ok(auth_header: str, expected: str) -> bool:
    scheme, _, encoded = auth_header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded.strip()).decode()
    except (binascii.Error, UnicodeDecodeError):
        return False
    _, _, password = decoded.partition(":")
    return secrets.compare_digest(password, expected)


async def basic_auth_middleware(request: Request, call_next):
    expected = os.getenv("APP_PASSWORD", "")
    if not expected or request.url.path.startswith(EXEMPT_PREFIXES):
        return await call_next(request)

    auth_header = request.headers.get("authorization", "")
    if _password_ok(auth_header, expected):
        return await call_next(request)

    return JSONResponse(
        {"detail": "Authentication required. Use any username with the shared APP_PASSWORD."},
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Mock ATS"'},
    )
