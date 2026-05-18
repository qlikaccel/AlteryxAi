from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .architecture_config import config_bool, load_architecture_config


def write_agent_audit_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    config = load_architecture_config()
    if not config_bool(config, "audit", "enabled", True):
        return {"enabled": False}

    audit_config = config.get("audit", {})
    log_dir = Path(str(audit_config.get("log_dir") or "runtime/audit"))
    if not log_dir.is_absolute():
        log_dir = Path.cwd() / log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    include_full_prompt = config_bool(config, "audit", "include_full_prompt", False)
    sanitized_payload = _sanitize_payload(payload, include_full_prompt=include_full_prompt)
    event = {
        "event_id": _event_id(event_type, sanitized_payload),
        "event_type": event_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **sanitized_payload,
    }
    path = log_dir / f"{event['event_id']}.json"
    path.write_text(json.dumps(event, indent=2, sort_keys=True, ensure_ascii=True), encoding="utf-8")
    return {"enabled": True, "event_id": event["event_id"], "path": str(path)}


def _event_id(event_type: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps({"event_type": event_type, "payload": payload}, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{digest}"


def _sanitize_payload(payload: dict[str, Any], *, include_full_prompt: bool) -> dict[str, Any]:
    sanitized = dict(payload)
    if not include_full_prompt:
        for key in ("prompt", "messages", "raw_rows", "sample_rows"):
            if key in sanitized:
                sanitized[key] = f"redacted:{key}"
    return sanitized
