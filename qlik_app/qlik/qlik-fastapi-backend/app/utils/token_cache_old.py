# Token refresh middleware for Alteryx API
import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from app.utils.alteryx_workspace_utils import AlteryxSession, refresh_access_token

logger = logging.getLogger(__name__)

class TokenCache:
    """Cache tokens and auto-refresh them before expiry"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TokenCache, cls).__new__(cls)
            cls._instance.last_refresh = None
            cls._instance.tokens = None
        return cls._instance
    
    def load_from_env(self):
        """Load tokens from .env file"""
        load_dotenv(override=True)  # Force reload
        return {
            'access': os.getenv('ALTERYX_ACCESS_TOKEN'),
            'refresh': os.getenv('ALTERYX_REFRESH_TOKEN'),
        }
    
    def get_fresh_tokens(self):
        """Get tokens, refreshing if needed or expired"""
        load_dotenv(override=True)  # Reload .env in case manual refresh happened
        
        access_token = os.getenv('ALTERYX_ACCESS_TOKEN')
        refresh_token = os.getenv('ALTERYX_REFRESH_TOKEN')
        
        if not access_token:
            return None, None
        
        # Check if token is about to expire (within 2 minutes)
        try:
            import jwt
            decoded = jwt.decode(access_token, options={'verify_signature': False})
            exp_time = datetime.fromtimestamp(decoded['exp'])
            time_left = (exp_time - datetime.now()).total_seconds()
            
            # If less than 120 seconds (2 minutes) left, try refresh
            if time_left < 120:
                logger.warning(f"Token expiring in {time_left:.0f}s, attempting refresh...")
                if refresh_token:
                    try:
                        new_access, new_refresh = refresh_access_token(refresh_token)
                        logger.info("✅ Token auto-refreshed successfully")
                        return new_access, new_refresh
                    except Exception as e:
                        logger.error(f"Auto-refresh failed: {e}")
                        # Return stale token - let caller handle 401
                        return access_token, refresh_token
        except Exception as e:
            logger.debug(f"Token decode error: {e}")
        
        return access_token, refresh_token


def get_alteryx_tokens():
    """Get fresh Alteryx tokens with auto-refresh"""
    cache = TokenCache()
    access, refresh = cache.get_fresh_tokens()
    
    if not access:
        raise ValueError("No Alteryx tokens in .env")
    
    return access, refresh
