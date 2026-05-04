"""Persistent Alteryx token manager.

Alteryx refresh tokens rotate. After a successful refresh, the previous
refresh token is no longer valid. This manager stores the latest rotated token
pair in a local JSON file so backend restarts do not reuse stale tokens from
the original .env file.

The JSON file contains secrets and must not be committed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

try:
    import jwt
    HAS_JWT = True
except ImportError:
    HAS_JWT = False

logger = logging.getLogger(__name__)

TOKEN_STORAGE_PATH = Path(__file__).parent.parent / "token_storage.json"
TOKEN_LOCK = threading.Lock()

ALTERYX_TOKEN_URL = "https://pingauth.alteryxcloud.com/as/token"
ALTERYX_ENV_FILE = Path(__file__).parent.parent.parent / ".env"
load_dotenv(ALTERYX_ENV_FILE, override=True)

ALTERYX_CLIENT_ID = os.getenv("ALTERYX_CLIENT_ID", "af1b5321-afe0-48c2-966a-c77d74e98085")
ALTERYX_CLIENT_SECRET = os.getenv("ALTERYX_CLIENT_SECRET", "")


class TokenManager:
    """Manages Alteryx token refresh and local token persistence."""

    @staticmethod
    def storage_path() -> Path:
        return TOKEN_STORAGE_PATH

    @staticmethod
    def load_tokens() -> dict:
        if not TOKEN_STORAGE_PATH.exists():
            return {}
        try:
            with open(TOKEN_STORAGE_PATH, "r", encoding="utf-8") as file:
                return json.load(file)
        except Exception as exc:
            logger.warning("[TokenManager] Could not read token storage: %s", exc)
            return {}

    @staticmethod
    def save_tokens(access_token: str, refresh_token: Optional[str], expires_in: Optional[int] = None) -> None:
        if not access_token and not refresh_token:
            return
        stored = TokenManager.load_tokens()
        data = {
            **stored,
            "access_token": access_token or stored.get("access_token", ""),
            "refresh_token": refresh_token or stored.get("refresh_token"),
            "timestamp": time.time(),
            "expires_at": time.time() + expires_in - 60 if expires_in else None,
            "access_token_exp": TokenManager.token_expiry(access_token or stored.get("access_token")),
            "refresh_token_exp": TokenManager.token_expiry(refresh_token or stored.get("refresh_token")),
        }
        try:
            with open(TOKEN_STORAGE_PATH, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2)
            logger.info("[TokenManager] Stored latest Alteryx tokens in %s", TOKEN_STORAGE_PATH)
        except Exception as exc:
            logger.warning("[TokenManager] Could not write token storage: %s", exc)

    @staticmethod
    def clear_storage() -> None:
        try:
            if TOKEN_STORAGE_PATH.exists():
                TOKEN_STORAGE_PATH.unlink()
        except Exception as exc:
            logger.warning("[TokenManager] Could not clear token storage: %s", exc)

    @staticmethod
    def token_expiry(token: Optional[str]) -> Optional[float]:
        if not token or not HAS_JWT:
            return None
        try:
            decoded = jwt.decode(token, options={"verify_signature": False})
            return decoded.get("exp")
        except Exception:
            return None

    @staticmethod
    def get_tokens_from_storage_or_env() -> tuple[str, Optional[str]]:
        stored = TokenManager.load_tokens()
        access_token = stored.get("access_token") or os.getenv("ALTERYX_ACCESS_TOKEN", "")
        refresh_token = stored.get("refresh_token") or os.getenv("ALTERYX_REFRESH_TOKEN")
        return access_token, refresh_token

    @staticmethod
    def refresh_token(refresh_token: str, max_retries: int = 3) -> tuple[str, Optional[str]]:
        if not refresh_token:
            raise ValueError("No Alteryx refresh token available.")

        with TOKEN_LOCK:
            last_error: Optional[Exception] = None
            for attempt in range(1, max_retries + 1):
                payload = {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": ALTERYX_CLIENT_ID,
                }
                if ALTERYX_CLIENT_SECRET:
                    payload["client_secret"] = ALTERYX_CLIENT_SECRET

                try:
                    resp = requests.post(
                        ALTERYX_TOKEN_URL,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        data=payload,
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        body = resp.json()
                        new_access = body.get("access_token", "")
                        new_refresh = body.get("refresh_token")
                        if not new_access:
                            raise ValueError("Ping Identity returned no access_token.")
                        TokenManager.save_tokens(new_access, new_refresh, body.get("expires_in"))
                        return new_access, new_refresh

                    error_text = resp.text[:500]
                    if resp.status_code == 400 and "invalid_grant" in error_text:
                        raise ValueError(
                            "The Alteryx refresh token is invalid, revoked, or already used. "
                            "Generate a fresh OAuth API token pair in Alteryx One."
                        )
                    resp.raise_for_status()
                except Exception as exc:
                    last_error = exc
                    if "refresh token is invalid" in str(exc).lower():
                        raise
                    if attempt < max_retries:
                        time.sleep(2 ** attempt)
                        continue
                    raise

            raise ValueError(f"Token refresh failed: {last_error}")
