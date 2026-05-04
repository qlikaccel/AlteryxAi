#!/usr/bin/env python
"""
Alteryx Token Management Script
Automatically refreshes expired Alteryx API tokens or generates new ones

MANUAL SETUP:
1. Go to: https://us1.alteryxcloud.com
2. Click Settings → API Keys → Generate New Key
3. Copy the Access Token & Refresh Token
4. Run: python refresh_alteryx_token.py --set-tokens
5. Enter tokens when prompted

AUTOMATIC REFRESH:
python refresh_alteryx_token.py --refresh
"""

import requests
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import jwt

# Configuration
ALTERYX_TOKEN_URL = "https://pingauth.alteryxcloud.com/as/token"
ALTERYX_CLIENT_ID = "af1b5321-afe0-48c2-966a-c77d74e98085"
ENV_FILE = Path(__file__).parent.parent / "qlik_app" / "qlik" / "qlik-fastapi-backend" / ".env"

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

def load_env():
    """Load environment variables"""
    load_dotenv(ENV_FILE, override=False)

def get_token_expiry(token):
    """Decode JWT and return expiry time"""
    try:
        decoded = jwt.decode(token, options={"verify_signature": False})
        return datetime.fromtimestamp(decoded["exp"])
    except:
        return None

def check_token_status():
    """Check current token status"""
    load_env()
    access_token = os.getenv("ALTERYX_ACCESS_TOKEN")
    
    if not access_token:
        print("❌ No ALTERYX_ACCESS_TOKEN found in .env")
        return False
    
    exp_time = get_token_expiry(access_token)
    now = datetime.now()
    
    if not exp_time:
        print("⚠️  Could not decode token expiry")
        return False
    
    print(f"\n📋 Token Status:")
    print(f"   Expires: {exp_time}")
    print(f"   Now:     {now}")
    
    if exp_time < now:
        elapsed = (now - exp_time).total_seconds()
        print(f"   ❌ EXPIRED {elapsed/3600:.1f} hours ago")
        return False
    else:
        remaining = (exp_time - now).total_seconds()
        print(f"   ✅ VALID for {remaining/3600:.1f} more hours")
        return True

def refresh_token():
    """Refresh access token using refresh token"""
    load_env()
    refresh_token_val = os.getenv("ALTERYX_REFRESH_TOKEN")
    
    if not refresh_token_val:
        print("❌ No ALTERYX_REFRESH_TOKEN found in .env")
        print("   Please run with --set-tokens to provide new tokens")
        return False
    
    print("\n🔄 Attempting to refresh token...")
    try:
        resp = requests.post(
            ALTERYX_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token_val,
                "client_id": ALTERYX_CLIENT_ID,
            },
            timeout=15,
        )
        
        if resp.status_code == 200:
            data = resp.json()
            new_access_token = data["access_token"]
            new_refresh_token = data.get("refresh_token", refresh_token_val)
            
            print("✅ Token refresh successful!")
            update_env_tokens(new_access_token, new_refresh_token)
            return True
        else:
            error = resp. json().get('error_description', resp.text)
            print(f"❌ Refresh failed: {error}")
            print("\n🔗 SOLUTION: Generate new tokens manually:")
            print("   1. Visit: https://us1.alteryxcloud.com")
            print("   2. Click Settings (top-right) → OAuth2.0 API Tokens")
            print("   3. Click '+ Generate' button")
            print("   4. Copy Access Token & Refresh Token")
            print("   5. Run: python scripts/refresh_alteryx_token.py --set-tokens")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def set_tokens_manual():
    """Prompt user to enter new tokens"""
    print("\n🔐 Enter New Alteryx Tokens:")
    print("   Get from: https://us1.alteryxcloud.com → Settings → API Keys")
    
    access_token = input("\nAccess Token: ").strip()
    refresh_token = input("Refresh Token: ").strip()
    workspace_name = input("Workspace Name (optional, default from env): ").strip() or None
    
    if not access_token or not refresh_token:
        print("❌ Tokens cannot be empty")
        return False
    
    # Validate tokens are JWT format
    if not access_token.count(".") == 2 or not refresh_token.count(".") == 2:
        print("❌ Invalid token format (should be JWT with 2 dots)")
        return False
    
    print("\n✅ Tokens provided. Updating .env...")
    
    extra_updates = {}
    if workspace_name:
        extra_updates["ALTERYX_WORKSPACE_NAME"] = workspace_name
    
    update_env_tokens(access_token, refresh_token, extra_updates)
    return True

def update_env_tokens(access_token, refresh_token, extra_updates=None):
    """Update .env file with new tokens"""
    if not ENV_FILE.exists():
        print(f"❌ .env file not found at {ENV_FILE}")
        return False
    
    with open(ENV_FILE, "r") as f:
        lines = f.readlines()
    
    # Update or add tokens
    new_lines = []
    access_found = False
    refresh_found = False
    
    for line in lines:
        if line.startswith("ALTERYX_ACCESS_TOKEN="):
            new_lines.append(f"ALTERYX_ACCESS_TOKEN={access_token}\n")
            access_found = True
        elif line.startswith("ALTERYX_REFRESH_TOKEN="):
            new_lines.append(f"ALTERYX_REFRESH_TOKEN={refresh_token}\n")
            refresh_found = True
        elif extra_updates and line.startswith("ALTERYX_WORKSPACE_NAME="):
            if "ALTERYX_WORKSPACE_NAME" in extra_updates:
                new_lines.append(f"ALTERYX_WORKSPACE_NAME={extra_updates['ALTERYX_WORKSPACE_NAME']}\n")
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
    
    # Add if not found
    if not access_found:
        new_lines.append(f"\nALTERYX_ACCESS_TOKEN={access_token}\n")
    if not refresh_found:
        new_lines.append(f"ALTERYX_REFRESH_TOKEN={refresh_token}\n")
    
    with open(ENV_FILE, "w") as f:
        f.writelines(new_lines)
    
    print(f"✅ Updated {ENV_FILE}")
    print(f"   Access Token:  {access_token[:50]}...")
    print(f"   Refresh Token: {refresh_token[:50]}...")
    
    # Verify
    load_dotenv(ENV_FILE, override=True)
    exp_time = get_token_expiry(access_token)
    if exp_time:
        now = datetime.now()
        remaining = (exp_time - now).total_seconds() / 3600
        print(f"   ✅ Token expires in {remaining:.1f} hours")
    
    return True

def main():
    parser = argparse.ArgumentParser(description="Alteryx Token Manager")
    parser.add_argument(
        "--check", 
        action="store_true", 
        help="Check token status"
    )
    parser.add_argument(
        "--refresh", 
        action="store_true", 
        help="Attempt to refresh token using refresh_token"
    )
    parser.add_argument(
        "--set-tokens", 
        action="store_true", 
        help="Manually set new tokens (interactive)"
    )
    
    args = parser.parse_args()
    
    if not args.check and not args.refresh and not args.set_tokens:
        args.check = True  # Default
    
    if args.check:
        check_token_status()
    
    if args.refresh:
        if not refresh_token():
            print("\n💡 Refresh failed. Try: python refresh_alteryx_token.py --set-tokens")
    
    if args.set_tokens:
        if set_tokens_manual():
            check_token_status()

if __name__ == "__main__":
    main()
