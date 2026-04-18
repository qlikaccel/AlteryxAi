# app/utils/token_manager.py
"""
Token Manager for persistent token storage and lifecycle management.
Handles token refresh, persistence, and validation.
"""

import os
import json
import time
import logging
from typing import Optional, Tuple
from pathlib import Path
import threading

try:
    import jwt
    HAS_JWT = True
except ImportError:
    HAS_JWT = False

logger = logging.getLogger(__name__)

# Token storage file (persists refreshed tokens)
TOKEN_STORAGE_PATH = Path(__file__).parent.parent / "token_storage.json"
TOKEN_LOCK = threading.Lock()  # Prevent concurrent token refresh races

ALTERYX_TOKEN_URL = "https://pingauth.alteryxcloud.com/as/token"
ALTERYX_CLIENT_ID = os.getenv("ALTERYX_CLIENT_ID", "af1b5321-afe0-48c2-966a-c77d74e98085")
ALTERYX_CLIENT_SECRET = os.getenv("ALTERYX_CLIENT_SECRET", "")


class TokenManager:
    """Manages Alteryx token lifecycle with persistence and validation."""
    
    @staticmethod
    def _load_tokens_from_storage() -> dict:
        """Load tokens from persistent storage (JSON file)."""
        if TOKEN_STORAGE_PATH.exists():
            try:
                with open(TOKEN_STORAGE_PATH, 'r') as f:
                    data = json.load(f)
                    logger.debug("📁 Loaded tokens from storage file")
                    return data
            except Exception as e:
                logger.warning(f"⚠️  Could not load token storage: {e}")
                return {}
        return {}
    
    @staticmethod
    def _save_tokens_to_storage(access_token: str, refresh_token: Optional[str], expires_in: Optional[int] = None) -> None:
        """Save tokens to persistent storage with metadata."""
        try:
            # Calculate expires_at: now + expires_in - 60 second buffer
            expires_at = None
            if expires_in:
                expires_at = time.time() + expires_in - 60
            
            data = {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "timestamp": time.time(),
                "expires_at": expires_at,
                "access_token_exp": TokenManager._get_token_expiry(access_token),
                "refresh_token_exp": TokenManager._get_token_expiry(refresh_token),
            }
            with open(TOKEN_STORAGE_PATH, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info("💾 Tokens saved to persistent storage")
        except Exception as e:
            logger.error(f"❌ Could not save token storage: {e}")
    
    @staticmethod
    def _get_token_expiry(token: Optional[str]) -> Optional[float]:
        """Extract expiry timestamp from JWT token."""
        if not token or not HAS_JWT:
            return None
        try:
            decoded = jwt.decode(token, options={"verify_signature": False})
            return decoded.get("exp")
        except Exception:
            return None
    
    @staticmethod
    def get_valid_tokens() -> Tuple[str, Optional[str]]:
        """
        Get valid tokens, trying multiple sources in order:
        1. Persistent storage (recently refreshed tokens) - BOTH tokens from storage
        2. Environment variables (access only, refresh MUST be from storage)
        3. Raise error if no refresh token found
        """
        logger.info("🔍 Checking for valid tokens...")
        
        # 1. Try persistent storage first (most recent tokens)
        stored = TokenManager._load_tokens_from_storage()
        if stored.get("access_token") and stored.get("refresh_token"):
            access = stored["access_token"]
            refresh = stored["refresh_token"]
            
            logger.info("✅ Using tokens from persistent storage")
            return access, refresh
        
        # 2. Fallback: Use env access token ONLY if storage unavailable
        # BUT refresh_token MUST come from storage (never from env)
        access_env = os.getenv("ALTERYX_ACCESS_TOKEN", "")
        refresh_stored = stored.get("refresh_token")
        
        if not refresh_stored:
            # If no refresh token in storage, try env (legacy support)
            refresh_env = os.getenv("ALTERYX_REFRESH_TOKEN", "")
            if not refresh_env:
                error = "❌ No refresh token found. Set ALTERYX_REFRESH_TOKEN in .env or login via OAuth"
                logger.error(error)
                raise ValueError(error)
            logger.info("✅ Using tokens from environment variables (legacy)")
            return access_env, refresh_env
        
        # Use env access token + stored refresh token
        logger.info("✅ Using access token from env + refresh token from storage")
        return access_env or "", refresh_stored
    
    @staticmethod
    def _is_token_expired(token: Optional[str], buffer: int = 30) -> bool:
        """Check if token is expired with buffer."""
        if not token or not HAS_JWT:
            return False
        try:
            decoded = jwt.decode(token, options={"verify_signature": False})
            exp = decoded.get("exp", 0)
            remaining = exp - time.time()
            
            if remaining <= buffer:
                logger.info(f"⏰ Token expiring in {remaining:.1f}s")
                return True
            return False
        except Exception:
            return False
    
    @staticmethod
    def refresh_token(refresh_token: str, max_retries: int = 3) -> Tuple[str, Optional[str]]:
        """
        Refresh access token with retry logic and thread safety.
        
        Returns: (new_access_token, new_refresh_token)
        """
        with TOKEN_LOCK:  # Prevent concurrent refresh race conditions
            logger.info(f"\n🔄 [TokenManager] Attempting token refresh (max {max_retries} retries)...")
            
            if not refresh_token:
                raise ValueError("No refresh token available for refresh attempt")
            
            for attempt in range(1, max_retries + 1):
                try:
                    logger.info(f"   Attempt {attempt}/{max_retries}...")
                    
                    import requests
                    
                    payload = {
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": ALTERYX_CLIENT_ID,
                    }
                    if ALTERYX_CLIENT_SECRET:
                        payload["client_secret"] = ALTERYX_CLIENT_SECRET
                    
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
                        expires_in = body.get("expires_in")
                        
                        if new_access:
                            logger.info(f"✅ Token refresh successful on attempt {attempt}")
                            
                            # Persist the new tokens (including both access and refresh)
                            TokenManager._save_tokens_to_storage(new_access, new_refresh, expires_in)
                            
                            return new_access, new_refresh
                        else:
                            raise ValueError("Empty access token in response")
                    
                    elif resp.status_code == 400:
                        error_body = resp.json()
                        error_desc = error_body.get("error_description", "")
                        
                        if "does not exist" in error_desc.lower():
                            logger.error(f"❌ Refresh token is INVALID (does not exist)")
                            logger.error(f"   Error: {error_desc}")
                            raise ValueError(
                                "Refresh token is invalid or has been revoked. "
                                "Generate a new token from Alteryx Cloud."
                            )
                        else:
                            # Temporary error, retry
                            logger.warning(f"   ⚠️  Temporary error (attempt {attempt}): {error_desc}")
                            if attempt < max_retries:
                                time.sleep(2 ** attempt)  # Exponential backoff
                                continue
                    
                    else:
                        logger.warning(f"   ⚠️  HTTP {resp.status_code}: {resp.text[:200]}")
                        if attempt < max_retries:
                            time.sleep(2 ** attempt)
                            continue
                
                except requests.exceptions.Timeout:
                    logger.warning(f"   ⚠️  Request timeout (attempt {attempt})")
                    if attempt < max_retries:
                        time.sleep(2 ** attempt)
                        continue
                
                except requests.exceptions.ConnectionError:
                    logger.warning(f"   ⚠️  Connection error (attempt {attempt})")
                    if attempt < max_retries:
                        time.sleep(2 ** attempt)
                        continue
                
                except Exception as e:
                    logger.error(f"   ❌ Unexpected error: {str(e)}")
                    raise
            
            raise ValueError(
                f"Token refresh failed after {max_retries} attempts. "
                "Check your refresh token or network connectivity."
            )
    
    @staticmethod
    def get_fresh_access_token(current_access: str, refresh_token: str) -> Tuple[str, Optional[str]]:
        """
        Get a fresh access token if needed.
        ALWAYS loads latest refresh_token from storage (not parameter).
        
        Returns: (access_token, refresh_token)
        """
        # Check expires_at from storage
        stored = TokenManager._load_tokens_from_storage()
        expires_at = stored.get("expires_at")
        
        # Use stored refresh_token, fallback to parameter if not available
        latest_refresh = stored.get("refresh_token") or refresh_token
        
        # Check if token is still valid using expires_at
        if expires_at and time.time() < expires_at:
            logger.debug(f"✅ Access token still valid for {expires_at - time.time():.0f}s")
            return current_access, latest_refresh
        
        # Token expired or expiring soon - refresh it
        logger.info("🔄 Token expired or expiring, refreshing...")
        if not latest_refresh:
            raise ValueError("No refresh token available for token refresh")
        
        return TokenManager.refresh_token(latest_refresh)
    
    @staticmethod
    def validate_refresh_token(refresh_token: str) -> bool:
        """
        Validate that a refresh token is functional.
        Returns True if valid, False if invalid.
        """
        try:
            TokenManager.refresh_token(refresh_token, max_retries=1)
            return True
        except ValueError as e:
            if "invalid" in str(e).lower() or "does not exist" in str(e).lower():
                logger.error(f"❌ Refresh token validation failed: {e}")
                return False
            raise
        except Exception as e:
            logger.warning(f"⚠️  Refresh token validation inconclusive: {e}")
            return False
    
    @staticmethod
    def clear_storage() -> None:
        """Clear persistent token storage (use when manually resetting tokens)."""
        try:
            if TOKEN_STORAGE_PATH.exists():
                TOKEN_STORAGE_PATH.unlink()
                logger.info("🗑️  Token storage cleared")
        except Exception as e:
            logger.error(f"Error clearing token storage: {e}")
    
    @staticmethod
    def _get_recommendations(access_env: str, refresh_env: str, access_expired: bool, refresh_valid: bool) -> list:
        """Generate recommendations based on token state."""
        recommendations = []
        
        if not refresh_env:
            recommendations.append("❌ MISSING: ALTERYX_REFRESH_TOKEN not in .env")
            recommendations.append("   → Set ALTERYX_REFRESH_TOKEN from Alteryx Cloud")
        elif not refresh_valid:
            recommendations.append("❌ INVALID: Refresh token is no longer valid")
            recommendations.append("   → Generate new token from Alteryx Cloud → Settings → API Keys")
        else:
            recommendations.append("✅ Refresh token is valid and functional")
        
        if not access_env:
            recommendations.append("ℹ️  No access token in .env (normal - will be auto-generated)")
        elif access_expired:
            recommendations.append("ℹ️  Access token has expired (normal - will auto-refresh)")
        else:
            recommendations.append("✅ Access token is valid")
        
        return recommendations
