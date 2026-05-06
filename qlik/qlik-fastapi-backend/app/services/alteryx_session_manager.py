"""
Alteryx Cloud Session Manager

Handles authentication with Alteryx Cloud and manages session cookies.
Uses proper API authentication with Bearer tokens for workflow API calls.
"""

import requests
import logging
import base64
from typing import Optional, Dict, Any
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


class AlteryxSessionManager:
    """Manages sessions and authentication with Alteryx Cloud"""
    
    def __init__(self):
        self.session = requests.Session()
        self._session_cookies: Dict[str, Any] = {}
        self._base_url: str = ""  # Store base URL for later API calls
        self._auth_token: Optional[str] = None  # Store API token for bearer auth
        self._username: Optional[str] = None  # Store username for backup auth
        self._password: Optional[str] = None  # Store password for backup auth
        
    def authenticate(
        self, 
        base_url: str, 
        username: str, 
        password: str,
        api_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Authenticate with Alteryx Cloud using multiple strategies.
        
        1. Use provided API token if available (preferred)
        2. Try session-based auth at various endpoints
        3. Extract auth token from response if available
        4. Store credentials for backup basic auth on API calls
        
        Args:
            base_url: Alteryx Cloud base URL (e.g., https://us1.alteryxcloud.com)
            username: Username (e.g., accelerators@sorim.ai)
            password: Password
            api_token: Optional pre-generated API token
            
        Returns:
            Dict with success status and message
        """
        try:
            # Normalize base URL (remove trailing slashes)
            base_url = base_url.rstrip('/')
            
            # Store credentials for later use in API calls
            self._base_url = base_url
            self._username = username
            self._password = password
            
            # If API token is provided, use it directly
            if api_token:
                logger.info(f"✓ Using provided API token for authentication")
                self._auth_token = api_token
                self._session_cookies = {}
                
                logger.info(f"✓ Auth token stored successfully in session manager")
                logger.debug(f"✓ Token length: {len(api_token)} chars")
                
                return {
                    "success": True,
                    "message": f"Connected to {base_url} with API token",
                    "base_url": base_url,
                    "auth_mode": "bearer_token",
                    "note": "Using Bearer token authentication"
                }
            
            logger.info(f"Authenticating to Alteryx Cloud: {base_url}")
            
            # Try multiple authentication endpoints
            auth_endpoints = [
                f"{base_url}/api/auth/login",           # Standard API auth
                f"{base_url}/api/v1/auth/login",        # API v1
                f"{base_url}/api/v2/auth/login",        # API v2
                f"{base_url}/auth/login",               # Root auth
                f"{base_url}/api/login",                # Simple API
                f"{base_url}/api/auth/token",           # Token endpoint
            ]
            
            auth_success = False
            auth_token = None
            
            # Try each endpoint
            for auth_url in auth_endpoints:
                try:
                    logger.debug(f"Trying auth endpoint: {auth_url}")
                    
                    # Try JSON payload
                    payload = {
                        "username": username,
                        "password": password,
                        "email": username,
                        "grant_type": "password"
                    }
                    
                    response = self.session.post(
                        auth_url,
                        json=payload,
                        timeout=10,
                        verify=True
                    )
                    
                    logger.debug(f"Auth endpoint {auth_url}: {response.status_code}")
                    
                    if response.status_code == 200:
                        logger.info(f"✓ Successful authentication at {auth_url}")
                        auth_success = True
                        
                        # Try to extract auth token from response
                        try:
                            resp_data = response.json()
                            auth_token = (
                                resp_data.get("access_token") or
                                resp_data.get("token") or
                                resp_data.get("auth_token") or
                                None
                            )
                            if auth_token:
                                logger.info(f"✓ Extracted auth token from response")
                                self._auth_token = auth_token
                        except Exception as e:
                            logger.debug(f"Could not extract token: {e}")
                        
                        # Store session cookies
                        self._session_cookies = dict(self.session.cookies)
                        break
                        
                except (requests.Timeout, requests.ConnectionError, requests.RequestException) as e:
                    logger.debug(f"Auth endpoint {auth_url} failed: {e}")
                    continue
            
            # Prepare response
            if auth_success:
                return {
                    "success": True,
                    "message": f"Successfully authenticated to {base_url}",
                    "cookies": self._session_cookies,
                    "base_url": base_url,
                    "has_token": bool(auth_token)
                }
            else:
                logger.warning(f"All authentication endpoints failed. Will use credentials for API basic auth.")
                # Store credentials but return success so workflows can use basic auth
                self._session_cookies = dict(self.session.cookies)
                
                return {
                    "success": True,
                    "message": f"Connection to {base_url} established (using credential-based auth for APIs)",
                    "cookies": self._session_cookies,
                    "base_url": base_url,
                    "auth_mode": "credentials",
                    "note": "Using credentials for API calls instead of session auth"
                }
                
        except requests.Timeout:
            logger.error("Authentication timeout")
            return {
                "success": False,
                "message": "Connection timeout. Alteryx Cloud may be unreachable.",
                "base_url": base_url
            }
        except requests.ConnectionError as e:
            logger.error(f"Connection error: {str(e)}")
            return {
                "success": False,
                "message": f"Failed to connect to Alteryx Cloud: {str(e)}",
                "base_url": base_url
            }
        except Exception as e:
            logger.error(f"Unexpected error during authentication: {str(e)}")
            return {
                "success": False,
                "message": f"Authentication error: {str(e)}",
                "base_url": base_url
            }
    
    def get_auth_headers(self) -> Dict[str, str]:
        """
        Get authorization headers for API calls.
        
        Returns either:
        - Bearer token if extracted from login response
        - Basic auth header with username/password if no token available
        - Empty dict if no credential available
        """
        headers = {}
        
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
            logger.debug("Using Bearer token for auth")
        elif self._username and self._password:
            # Create basic auth header
            credentials = base64.b64encode(
                f"{self._username}:{self._password}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {credentials}"
            logger.debug("Using Basic auth for API calls")
        
        return headers
    
    def get_cookies(self) -> Dict[str, str]:
        """Returns stored session cookies"""
        return self._session_cookies
    
    def get_session(self) -> requests.Session:
        """Returns the underlying requests session with cookies"""
        return self.session
    
    def get_base_url(self) -> str:
        """Returns stored base URL"""
        return self._base_url
    
    def clear(self):
        """Clear session and cookies"""
        self.session.close()
        self._session_cookies = {}
        self.session = requests.Session()


# Global session manager instance
_alteryx_manager: Optional[AlteryxSessionManager] = None


def get_alteryx_session_manager() -> AlteryxSessionManager:
    """Get or create the global Alteryx session manager"""
    global _alteryx_manager
    if _alteryx_manager is None:
        _alteryx_manager = AlteryxSessionManager()
    return _alteryx_manager
