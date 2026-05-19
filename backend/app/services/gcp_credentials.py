import base64
import json
import os
from pathlib import Path
from typing import Any


SERVICE_ACCOUNT_ENV_NAMES = (
    "GCP_SERVICE_ACCOUNT_JSON",
    "GOOGLE_CREDENTIALS_JSON",
    "GCP_SERVICE_ACCOUNT_JSON_B64",
    "GOOGLE_CREDENTIALS_JSON_B64",
)


def _clean_raw_json(value: str) -> str:
    cleaned = str(value or "").strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _decode_env_value(name: str, value: str) -> str:
    cleaned = _clean_raw_json(value)
    if name.endswith("_B64"):
        return base64.b64decode(cleaned).decode("utf-8")
    return cleaned


def service_account_info_from_env(env: dict[str, str] | None = None) -> dict[str, Any] | None:
    source = env or os.environ
    for name in SERVICE_ACCOUNT_ENV_NAMES:
        raw = source.get(name) or os.getenv(name)
        if not raw:
            continue
        try:
            return json.loads(_decode_env_value(name, raw))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{name} is not valid service account JSON.") from exc
    return None


def write_service_account_file_from_env(work_dir: Path, env: dict[str, str]) -> str:
    info = service_account_info_from_env(env)
    if not info:
        return ""

    credentials_path = work_dir / "gcp_service_account.json"
    credentials_path.write_text(json.dumps(info), encoding="utf-8")
    env["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_path)
    return str(credentials_path)
