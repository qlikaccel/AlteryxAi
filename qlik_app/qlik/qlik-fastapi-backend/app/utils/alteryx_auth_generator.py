"""
Alteryx OAuth2 Token Generator
Generates fresh ACCESS_TOKEN and REFRESH_TOKEN using username/password
"""

import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

ALTERYX_TENANT_URL = os.getenv("ALTERYX_TENANT_URL", "https://us1.alteryxcloud.com")
ALTERYX_USERNAME = os.getenv("ALTERYX_USERNAME", "")
ALTERYX_PASSWORD = os.getenv("ALTERYX_PASSWORD", "")
ALTERYX_CLIENT_ID = os.getenv("ALTERYX_CLIENT_ID", "")
ALTERYX_TOKEN_URL = "https://pingauth.alteryxcloud.com/as/token"
ENV_FILE = ".env"


def get_fresh_tokens(username: str, password: str, client_id: str) -> tuple[str, str]:
    """
    Get fresh ACCESS_TOKEN and REFRESH_TOKEN using Resource Owner Password Credentials.
    
    This is the 'password' grant type in OAuth2 - uses username/password directly.
    """
    print(f"\n🔵 Generating fresh tokens using username: {username}")
    
    data = {
        "grant_type": "password",
        "username": username,
        "password": password,
        "client_id": client_id,
        "scope": "w:*",  # Request workspace scope
    }
    
    try:
        resp = requests.post(
            ALTERYX_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data,
            timeout=15,
        )
        
        if resp.status_code == 200:
            body = resp.json()
            access_token = body.get("access_token", "")
            refresh_token = body.get("refresh_token", "")
            
            if access_token:
                print(f"✅ Fresh tokens generated successfully!")
                print(f"   Access Token: {access_token[:50]}...")
                print(f"   Refresh Token: {refresh_token[:50] if refresh_token else 'N/A'}...")
                return access_token, refresh_token
            else:
                print(f"❌ No access token in response: {body}")
                return None, None
        else:
            print(f"❌ Token generation failed: {resp.status_code}")
            print(f"   Response: {resp.text}")
            return None, None
    except requests.RequestException as e:
        print(f"❌ Network error: {e}")
        return None, None


def update_env_tokens(access_token: str, refresh_token: str):
    """Update .env file with fresh tokens."""
    if not os.path.exists(ENV_FILE):
        print(f"❌ .env file not found at {ENV_FILE}")
        return False
    
    try:
        # Read current .env
        with open(ENV_FILE, "r") as f:
            content = f.read()
        
        # Replace tokens
        import re
        
        # Replace ACCESS_TOKEN
        content = re.sub(
            r'ALTERYX_ACCESS_TOKEN=.*',
            f'ALTERYX_ACCESS_TOKEN={access_token}',
            content
        )
        
        # Replace REFRESH_TOKEN
        if refresh_token:
            content = re.sub(
                r'ALTERYX_REFRESH_TOKEN=.*',
                f'ALTERYX_REFRESH_TOKEN={refresh_token}',
                content
            )
        
        # Write back
        with open(ENV_FILE, "w") as f:
            f.write(content)
        
        print(f"✅ Updated .env with fresh tokens")
        return True
    except Exception as e:
        print(f"❌ Failed to update .env: {e}")
        return False


def refresh_tokens_from_password():
    """Main function to refresh tokens using username/password."""
    if not ALTERYX_USERNAME or not ALTERYX_PASSWORD:
        print("❌ ALTERYX_USERNAME and ALTERYX_PASSWORD must be set in .env")
        return False
    
    if not ALTERYX_CLIENT_ID:
        print("❌ ALTERYX_CLIENT_ID must be set in .env")
        return False
    
    print("="*70)
    print("  ALTERYX TOKEN REFRESH")
    print("="*70)
    
    access_token, refresh_token = get_fresh_tokens(
        ALTERYX_USERNAME,
        ALTERYX_PASSWORD,
        ALTERYX_CLIENT_ID,
    )
    
    if not access_token:
        print("\n❌ Failed to generate tokens. Check credentials in .env")
        return False
    
    if update_env_tokens(access_token, refresh_token):
        print("\n✅ Tokens refreshed and .env updated!")
        print("   Restart the backend to use fresh tokens")
        return True
    else:
        print("\n❌ Failed to update .env file")
        return False


if __name__ == "__main__":
    import sys
    success = refresh_tokens_from_password()
    sys.exit(0 if success else 1)
