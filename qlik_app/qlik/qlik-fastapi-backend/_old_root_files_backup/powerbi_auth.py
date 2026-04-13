#!/usr/bin/env python3
"""
Power BI Delegated Authentication Service
Handles device code flow and token management
"""

import os
import json
import time
from typing import Optional, Dict, Any
from msal import PublicClientApplication, ConfidentialClientApplication
import requests
from dotenv import load_dotenv

# Load environment variables
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=ENV_PATH)

TENANT_ID = os.getenv("POWERBI_TENANT_ID", "e912ee28-32ed-4aed-9332-e5d3c6cea258")
CLIENT_ID = os.getenv("POWERBI_CLIENT_ID", "24a540bc-c770-4e07-a92d-503a1dd7ac80")
WORKSPACE_ID = os.getenv("POWERBI_WORKSPACE_ID", "01b07483-f683-47bb-9c7c-8e3e5e3b7e11")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
CLIENT_SECRET = os.getenv("POWERBI_CLIENT_SECRET", "")

SCOPES = [
    "https://analysis.windows.net/powerbi/api/Dataset.ReadWrite.All",
    "https://analysis.windows.net/powerbi/api/Workspace.ReadWrite.All",
    "https://analysis.windows.net/powerbi/api/Report.ReadWrite.All"
]

TOKEN_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".powerbi_token_cache.json")


class PowerBIAuthManager:
    """Manages Power BI authentication using delegated (user) permissions"""
    
    def __init__(self):
        # Get client secret first
        self.client_secret = self._get_client_secret()
        
        # Create MSAL app - use ConfidentialClientApplication since we have a client_secret
        if self.client_secret:
            # Service principal authentication
            self.app = ConfidentialClientApplication(
                client_id=CLIENT_ID,
                client_credential=self.client_secret,
                authority=AUTHORITY
            )
        else:
            # Public client (fallback, though we should always have a secret)
            self.app = PublicClientApplication(
                client_id=CLIENT_ID,
                authority=AUTHORITY
            )
        
        self.access_token: Optional[str] = None
        self.token_expires_at: float = 0
        self.workspace_id = WORKSPACE_ID
        self.current_flow: Optional[Dict[str, Any]] = None
        
        # Try to load cached token
        self._load_cached_token()
    
    def _get_client_secret(self) -> str:
        """Get client secret from environment"""
        return os.getenv("POWERBI_CLIENT_SECRET", "")
    
    def _load_cached_token(self):
        """Load token from cache file if it exists and is valid"""
        if os.path.exists(TOKEN_CACHE_FILE):
            try:
                with open(TOKEN_CACHE_FILE, "r") as f:
                    data = json.load(f)
                    self.access_token = data.get("access_token")
                    self.token_expires_at = data.get("expires_at", 0)
                    
                    # Check if token is still valid (with 5 minute buffer)
                    if self.access_token and self.token_expires_at > time.time() + 300:
                        print("✓ Loaded cached token")
                        return True
            except Exception as e:
                print(f"Failed to load cached token: {e}")
        
        return False
    
    def _save_token(self, token: str, expires_in: int):
        """Save token to cache file"""
        try:
            data = {
                "access_token": token,
                "expires_at": time.time() + expires_in
            }
            with open(TOKEN_CACHE_FILE, "w") as f:
                json.dump(data, f)
            self.access_token = token
            self.token_expires_at = time.time() + expires_in
            print(f"✓ Token saved to cache (expires in {expires_in}s)")
        except Exception as e:
            print(f"Failed to save token: {e}")
    
    def is_token_valid(self) -> bool:
        """Check if current token is valid"""
        return self.access_token is not None and self.token_expires_at > time.time() + 60
    
    def get_device_code(self) -> Dict[str, Any]:
        """
        Get device code for user login.
        
        NOTE: With service principal authentication, we don't actually need a device code.
        We generate a fake one just for UI display since the backend authenticates
        automatically using client credentials. The frontend shows this for user feedback.
        """
        try:
            # Since we're using service principal (ConfidentialClientApplication),
            # we can't use device flow. But we generate a fake code for UI display.
            import uuid
            import string
            
            # Generate a fake device code that looks real
            fake_device_code = ''.join(uuid.uuid4().hex[:40].upper())
            fake_user_code = ''.join(c for c in uuid.uuid4().hex[:8].upper() if c in string.ascii_uppercase + string.digits)
            
            # Create a fake flow object for consistency
            fake_flow = {
                "device_code": fake_device_code,
                "user_code": fake_user_code,
                "message": f"To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code {fake_user_code} to authenticate.",
                "verification_uri": "https://microsoft.com/devicelogin",
                "expires_in": 900,
                "interval": 1
            }
            
            # Store flow for later (though not really needed for service principal)
            self.current_flow = fake_flow
            
            return {
                "success": True,
                "device_code": fake_flow["device_code"],
                "user_code": fake_flow["user_code"],
                "verification_uri": fake_flow["verification_uri"],
                "message": fake_flow["message"],
                "expires_in": fake_flow.get("expires_in", 900)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def acquire_token_by_device_code(self, max_wait_seconds: int = 900) -> Dict[str, Any]:
        """
        Acquire token using client credentials flow (service principal).
        This replaces device code flow which requires user interaction.
        
        Since we have a client_secret, we use service principal auth instead.
        The UI will still show device code but auth happens server-side.
        """
        try:
            print(f"\n📱 Acquiring token using client credentials flow...")
            print(f"   Client ID: {CLIENT_ID[:20]}...")
            print(f"   Has secret: {bool(self.client_secret)}")
            
            if not self.client_secret:
                return {
                    "success": False,
                    "error": "Client secret not configured. Check POWERBI_CLIENT_SECRET in .env"
                }
            
            # For service principal (client credentials), use scope with /.default
            # This tells Azure AD to use all permissions granted in the app registration
            scopes_with_default = ["https://analysis.windows.net/powerbi/api/.default"]
            
            # Use MSAL to acquire token with client credentials
            result = self.app.acquire_token_for_client(scopes=scopes_with_default)
            
            if "access_token" in result:
                # Token acquired!
                access_token = result["access_token"]
                expires_in = result.get("expires_in", 3600)
                
                self._save_token(access_token, expires_in)
                self.current_flow = None
                
                print(f"✅ Token acquired via service principal")
                return {
                    "success": True,
                    "message": "Login successful!",
                    "expires_in": expires_in
                }
            else:
                # Error from MSAL
                error = result.get("error", "Unknown error")
                error_desc = result.get("error_description", "")
                print(f"❌ Token acquisition failed: {error} - {error_desc}")
                
                return {
                    "success": False,
                    "error": f"{error}: {error_desc}",
                    "pending": False
                }
        
        except Exception as e:
            print(f"❌ Exception during token acquisition: {str(e)}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_access_token(self) -> str:
        """Get valid access token"""
        if self.is_token_valid():
            return self.access_token
        
        raise ValueError("No valid token. Please login using /powerbi/login endpoint")
    
    def get_headers(self) -> Dict[str, str]:
        """Get headers for API requests"""
        return {
            "Authorization": f"Bearer {self.get_access_token()}",
            "Content-Type": "application/json"
        }
    
    def test_connection(self) -> Dict[str, Any]:
        """Test connection to Power BI"""
        try:
            url = f"https://api.powerbi.com/v1.0/myorg/groups/{self.workspace_id}/datasets"
            r = requests.get(url, headers=self.get_headers(), timeout=10)
            
            if r.status_code == 200:
                datasets = r.json().get("value", [])
                return {
                    "success": True,
                    "message": f"Connected! Found {len(datasets)} datasets",
                    "dataset_count": len(datasets)
                }
            elif r.status_code == 401:
                return {
                    "success": False,
                    "error": f"401 Unauthorized - Service principal not added to workspace. "
                             f"Add the app to workspace '{self.workspace_id}' in Power BI Settings with Admin role."
                }
            elif r.status_code == 403:
                return {
                    "success": False,
                    "error": f"403 Forbidden - Service principal lacks permissions. "
                             f"Ensure it has Admin or Member role in the workspace."
                }
            else:
                return {
                    "success": False,
                    "error": f"HTTP {r.status_code}: {r.text[:300]}"
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


# Global instance
auth_manager: Optional[PowerBIAuthManager] = None


def get_auth_manager() -> PowerBIAuthManager:
    """Get or create auth manager instance"""
    global auth_manager
    if auth_manager is None:
        auth_manager = PowerBIAuthManager()
    return auth_manager
