"""Persistent Alteryx token manager.

Alteryx refresh tokens rotate. After a successful refresh, the previous
refresh token is no longer valid. This manager stores the latest rotated token
pair in a local JSON file so backend restarts do not reuse stale tokens from
the original .env file.

The JSON file contains secrets and must not be committed.

CHANGES vs original dev10:
  - Added get_valid_tokens()       (ported from dev01 — used by alteryx_auth_router)
  - Added get_fresh_access_token() (ported from dev01 — used by token_cache)
  - Added _load_tokens_from_storage() alias for backwards-compat with token_cache
  - All other existing dev10 code is UNCHANGED
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

import requests
from dotenv import load_dotenv

try:
    import jwt
    HAS_JWT = True
except ImportError:
    HAS_JWT = False

logger = logging.getLogger(__name__)

TOKEN_STORAGE_PATH = Path(__file__).parent.parent / "token_storage.json"
TOKEN_LOCK = threading.RLock()  # ← CHANGED: RLock() allows nested acquisitions

ALTERYX_TOKEN_URL = "https://pingauth.alteryxcloud.com/as/token"
ALTERYX_ENV_FILE = Path(__file__).parent.parent.parent / ".env"
load_dotenv(ALTERYX_ENV_FILE, override=True)

ALTERYX_CLIENT_ID = os.getenv("ALTERYX_CLIENT_ID", "af1b5321-afe0-48c2-966a-c77d74e98085")
ALTERYX_CLIENT_SECRET = os.getenv("ALTERYX_CLIENT_SECRET", "")


class TokenManager:
    """Manages Alteryx token refresh and local token persistence."""

    # ── Storage path ──────────────────────────────────────────────────────────

    @staticmethod
    def storage_path() -> Path:
        return TOKEN_STORAGE_PATH

    # ── Load / Save ───────────────────────────────────────────────────────────

    @staticmethod
    def _load_tokens_unlocked() -> dict:
        """Internal: Load tokens WITHOUT acquiring lock (caller must hold lock)."""
        if not TOKEN_STORAGE_PATH.exists():
            return {}
        try:
            with open(TOKEN_STORAGE_PATH, "r", encoding="utf-8") as file:
                return json.load(file)
        except Exception as exc:
            logger.warning("[TokenManager] Could not read token storage: %s", exc)
            return {}

    @staticmethod
    def load_tokens() -> dict:
        with TOKEN_LOCK:  # ← CRITICAL: Prevent reading during writes
            return TokenManager._load_tokens_unlocked()

    # Alias used by token_cache.py (imported from dev01 pattern)
    @staticmethod
    def _load_tokens_from_storage() -> dict:
        """Alias for load_tokens() — preserves compatibility with token_cache."""
        return TokenManager.load_tokens()

    @staticmethod
    def save_tokens(
        access_token: str,
        refresh_token: Optional[str],
        expires_in: Optional[int] = None,
    ) -> None:
        if not access_token and not refresh_token:
            return
        
        with TOKEN_LOCK:  # ← CRITICAL: Prevent race conditions with refresh_token()
            stored = TokenManager._load_tokens_unlocked()  # ← Use unlocked version
            data = {
                **stored,
                "access_token": access_token or stored.get("access_token", ""),
                "refresh_token": refresh_token or stored.get("refresh_token"),
                "timestamp": time.time(),
                "expires_at": time.time() + expires_in - 60 if expires_in else None,
                "access_token_exp": TokenManager.token_expiry(
                    access_token or stored.get("access_token")
                ),
                "refresh_token_exp": TokenManager.token_expiry(
                    refresh_token or stored.get("refresh_token")
                ),
            }
            try:
                with open(TOKEN_STORAGE_PATH, "w", encoding="utf-8") as file:
                    json.dump(data, file, indent=2)
                    file.flush()  # ← CRITICAL: Ensure data is written to disk
                    os.fsync(file.fileno())  # ← Force OS to write to storage
                logger.info(
                    "[TokenManager] Stored latest Alteryx tokens in %s", TOKEN_STORAGE_PATH
                )
            except Exception as exc:
                logger.error(
                    "[TokenManager] CRITICAL: Could not write token storage: %s", exc
                )

    # Alias used by dev01-style callers
    @staticmethod
    def _save_tokens_to_storage(
        access_token: str,
        refresh_token: Optional[str],
        expires_in: Optional[int] = None,
    ) -> None:
        """Alias for save_tokens() — preserves compatibility."""
        TokenManager.save_tokens(access_token, refresh_token, expires_in)

    @staticmethod
    def clear_storage() -> None:
        with TOKEN_LOCK:  # ← CRITICAL: Prevent deletion during reads/writes
            try:
                if TOKEN_STORAGE_PATH.exists():
                    TOKEN_STORAGE_PATH.unlink()
            except Exception as exc:
                logger.warning("[TokenManager] Could not clear token storage: %s", exc)

    # ── JWT helpers ───────────────────────────────────────────────────────────

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
    def _is_token_expired(token: Optional[str], buffer: int = 30) -> bool:
        """Return True if the JWT will expire within *buffer* seconds."""
        if not token or not HAS_JWT:
            return False
        try:
            decoded = jwt.decode(token, options={"verify_signature": False})
            exp = decoded.get("exp", 0)
            remaining = exp - time.time()
            if remaining <= buffer:
                logger.info("[TokenManager] Token expiring in %.1fs", remaining)
                return True
            return False
        except Exception:
            return False

    # ── Token resolution ──────────────────────────────────────────────────────

    @staticmethod
    def get_tokens_from_storage_or_env() -> Tuple[str, Optional[str]]:
        """
        Return (access_token, refresh_token), preferring token_storage.json
        over .env so rotated tokens are always used.
        """
        stored = TokenManager.load_tokens()
        access_token = stored.get("access_token") or os.getenv("ALTERYX_ACCESS_TOKEN", "")
        refresh_token = stored.get("refresh_token") or os.getenv("ALTERYX_REFRESH_TOKEN")
        return access_token, refresh_token

    @staticmethod
    def get_valid_tokens() -> Tuple[str, Optional[str]]:
        """
        Get valid tokens, trying sources in priority order:
          1. token_storage.json (most-recently-rotated pair)
          2. Environment variables (.env)
          3. Raise ValueError if no refresh token is available

        Ported from dev01 — required by alteryx_auth_router.py.
        """
        logger.info("[TokenManager] Checking for valid tokens...")

        # 1. Persistent storage — contains the latest rotated tokens
        stored = TokenManager.load_tokens()
        if stored.get("access_token") and stored.get("refresh_token"):
            logger.info("[TokenManager] Using tokens from persistent storage")
            return stored["access_token"], stored["refresh_token"]

        # 2. Fall back to environment variables
        access_env = os.getenv("ALTERYX_ACCESS_TOKEN", "")
        refresh_env = os.getenv("ALTERYX_REFRESH_TOKEN", "")

        if not refresh_env:
            error = (
                "[TokenManager] No refresh token found. "
                "Set ALTERYX_REFRESH_TOKEN in .env or log in via OAuth."
            )
            logger.error(error)
            raise ValueError(error)

        logger.info("[TokenManager] Using tokens from environment variables")
        return access_env, refresh_env

    # ── Token refresh ─────────────────────────────────────────────────────────

    @staticmethod
    def refresh_token(refresh_token: str, max_retries: int = 3) -> Tuple[str, Optional[str]]:
        """
        Exchange a refresh_token for a new access_token via Ping Identity.
        Saves the new pair to token_storage.json on success.
        Raises ValueError on permanent failures (invalid_grant).
        """
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
                    logger.info(
                        "[TokenManager] Refresh attempt %d/%d", attempt, max_retries
                    )
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
                        TokenManager.save_tokens(
                            new_access, new_refresh, body.get("expires_in")
                        )
                        logger.info(
                            "[TokenManager] Token refresh succeeded on attempt %d", attempt
                        )
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
                    if "refresh token is invalid" in str(exc).lower() or isinstance(
                        exc, ValueError
                    ):
                        raise
                    if attempt < max_retries:
                        time.sleep(2 ** attempt)
                        continue
                    raise

            raise ValueError(f"Token refresh failed after {max_retries} attempts: {last_error}")

    # ── Fresh access token ────────────────────────────────────────────────────

    @staticmethod
    def get_fresh_access_token(
        current_access: str, refresh_token: str
    ) -> Tuple[str, Optional[str]]:
        """
        Return a valid access token, refreshing only if the stored token is
        expired or about to expire.

        ALWAYS loads the latest refresh_token from storage (not the parameter)
        to avoid using a stale rotated token.

        Ported from dev01 — required by token_cache.py.
        """
        # Use stored expires_at (set when token was last saved) if available
        stored = TokenManager.load_tokens()
        expires_at = stored.get("expires_at")
        latest_refresh = stored.get("refresh_token") or refresh_token

        if expires_at and time.time() < expires_at:
            remaining = expires_at - time.time()
            logger.debug(
                "[TokenManager] Access token still valid for %.0fs", remaining
            )
            return current_access, latest_refresh

        # Token expired or expiry unknown — refresh proactively
        logger.info("[TokenManager] Access token expired or expiry unknown — refreshing...")
        if not latest_refresh:
            raise ValueError("No refresh token available for token refresh")

        return TokenManager.refresh_token(latest_refresh)
