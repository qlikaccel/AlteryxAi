# app/utils/token_cache.py
#
# CHANGES vs original dev10:
#   - get_fresh_tokens() now checks token_storage.json first (via TokenManager)
#     before falling back to .env — so rotated tokens are always used.
#   - Added _update_env_in_memory() to sync os.environ after a refresh so that
#     any code calling os.getenv() in the same process sees the new tokens.
#   - All other logic (2-minute expiry window, singleton pattern) unchanged.

import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from app.utils.token_manager import TokenManager

logger = logging.getLogger(__name__)


class TokenCache:
    """Singleton cache: load tokens from storage/env and auto-refresh before expiry."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TokenCache, cls).__new__(cls)
            cls._instance.last_refresh = None
            cls._instance.tokens = None
        return cls._instance

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _update_env_in_memory(access_token: str, refresh_token: str) -> None:
        """Sync refreshed tokens into os.environ so same-process callers see them."""
        if access_token:
            os.environ["ALTERYX_ACCESS_TOKEN"] = access_token
        if refresh_token:
            os.environ["ALTERYX_REFRESH_TOKEN"] = refresh_token

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_from_env(self):
        """Load tokens from .env file (legacy helper, kept for compatibility)."""
        load_dotenv(override=True)
        return {
            "access": os.getenv("ALTERYX_ACCESS_TOKEN"),
            "refresh": os.getenv("ALTERYX_REFRESH_TOKEN"),
        }

    def get_fresh_tokens(self):
        """
        Return (access_token, refresh_token), auto-refreshing if needed.

        Priority:
          1. token_storage.json (contains the latest rotated pair)
          2. .env environment variables
          3. None, None  — if neither source has an access token

        If the access token will expire within 2 minutes, a proactive refresh
        is attempted using the refresh token.
        """
        # Reload .env so manual token updates take effect without restart
        load_dotenv(override=True)

        # --- Resolve tokens (storage beats env so rotated tokens win) ---
        access_token, refresh_token = TokenManager.get_tokens_from_storage_or_env()

        if not access_token:
            logger.warning("[TokenCache] No access token found in storage or .env")
            return None, None

        # --- Check expiry ---
        try:
            import jwt  # noqa: PLC0415

            decoded = jwt.decode(access_token, options={"verify_signature": False})
            exp_time = datetime.fromtimestamp(decoded["exp"])
            time_left = (exp_time - datetime.now()).total_seconds()

            if time_left < 120:  # less than 2 minutes → refresh proactively
                logger.warning(
                    "[TokenCache] Token expiring in %.0fs — attempting proactive refresh...",
                    time_left,
                )
                if refresh_token:
                    try:
                        new_access, new_refresh = TokenManager.refresh_token(refresh_token)
                        logger.info("[TokenCache] Token auto-refreshed successfully")
                        self._update_env_in_memory(new_access, new_refresh or "")
                        return new_access, new_refresh
                    except Exception as exc:
                        logger.error("[TokenCache] Auto-refresh failed: %s", exc)
                        # Return the stale token; let 401 handling deal with it
                        return access_token, refresh_token
        except Exception as exc:
            logger.debug("[TokenCache] Token decode skipped: %s", exc)

        return access_token, refresh_token


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------

def get_alteryx_tokens():
    """
    Get fresh Alteryx tokens with auto-refresh.
    Raises ValueError if no access token is available.
    """
    cache = TokenCache()
    access, refresh = cache.get_fresh_tokens()

    if not access:
        raise ValueError(
            "No Alteryx access token found in token_storage.json or .env. "
            "Generate tokens from Alteryx One → User Preferences → OAuth 2.0 API Tokens."
        )

    return access, refresh
