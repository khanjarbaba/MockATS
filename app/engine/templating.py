"""Tiny templating layer.

Templates may contain ``{{var}}`` placeholders anywhere in strings, dicts or
lists. Context keys use dot notation: ``candidate.email``, ``env.SHL_API_KEY``,
``callback_url``. Special generators: ``generated_uuid``, ``timestamp``
(ISO-8601 UTC), ``timestamp_epoch``, ``idempotency_key``.

Path helpers (``get_path``/dot-paths like ``$.data.link`` or ``items.0.url``)
also live here — used for response mapping and webhook extraction.
"""

import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.\-]+)\s*\}\}")


def _generated(key: str):
    if key == "generated_uuid":
        return str(uuid.uuid4())
    if key == "idempotency_key":
        return str(uuid.uuid4())
    if key == "timestamp":
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if key == "timestamp_epoch":
        return str(int(time.time()))
    return None


def resolve_key(key: str, context: dict) -> Any:
    """Resolve a dotted key against context, env, or generators."""
    if key.startswith("env."):
        return os.getenv(key[4:], "")
    gen = _generated(key)
    if gen is not None:
        return gen
    parts = key.split(".")
    node: Any = context
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def render_string(template: str, context: dict) -> str:
    def repl(match: re.Match) -> str:
        value = resolve_key(match.group(1), context)
        return "" if value is None else str(value)

    return PLACEHOLDER.sub(repl, template)


def render(template: Any, context: dict) -> Any:
    """Recursively render a template structure (dict/list/str)."""
    if isinstance(template, str):
        # If the whole string is a single placeholder, preserve native type.
        m = PLACEHOLDER.fullmatch(template.strip())
        if m:
            value = resolve_key(m.group(1), context)
            return "" if value is None else value
        return render_string(template, context)
    if isinstance(template, dict):
        return {k: render(v, context) for k, v in template.items()}
    if isinstance(template, list):
        return [render(v, context) for v in template]
    return template


def find_placeholders(template: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(template, str):
        found.update(PLACEHOLDER.findall(template))
    elif isinstance(template, dict):
        for v in template.values():
            found |= find_placeholders(v)
    elif isinstance(template, list):
        for v in template:
            found |= find_placeholders(v)
    return found


def get_path(data: Any, path: str) -> Any:
    """Get a value via simple dot path: ``$.a.b.0.c`` or ``a.b.0.c``."""
    if path.startswith("$."):
        path = path[2:]
    elif path == "$":
        return data
    node = data
    for part in path.split("."):
        if isinstance(node, dict):
            if part not in node:
                return None
            node = node[part]
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return node
