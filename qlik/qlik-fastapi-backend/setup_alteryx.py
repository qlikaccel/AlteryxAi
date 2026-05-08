#!/usr/bin/env python3
"""
Quick setup script to refresh Alteryx tokens and connect to Alteryx Cloud
Run this before starting the backend if you get 401/expired token errors
"""

import os
import sys
import subprocess
from pathlib import Path
from dotenv import load_dotenv

# Get the script directory
SCRIPT_DIR = Path(__file__).parent
BACKEND_DIR = SCRIPT_DIR.parent.parent.parent  # Go up to backend root

# Load current env
load_dotenv(BACKEND_DIR / ".env")

ALTERYX_USERNAME = os.getenv("ALTERYX_USERNAME", "")
ALTERYX_PASSWORD = os.getenv("ALTERYX_PASSWORD", "")
ALTERYX_CLIENT_ID = os.getenv("ALTERYX_CLIENT_ID", "")


def print_section(title: str):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def refresh_tokens():
    """Refresh tokens using the auth generator."""
    print_section("STEP 1: REFRESH ALTERYX TOKENS")
    
    if not ALTERYX_USERNAME or not ALTERYX_PASSWORD:
        print("❌ Missing credentials in .env:")
        print(f"   ALTERYX_USERNAME: {ALTERYX_USERNAME or 'NOT SET'}")
        print(f"   ALTERYX_PASSWORD: {ALTERYX_PASSWORD or 'NOT SET'}")
        print("\nUpdate your .env file with:")
        print("   ALTERYX_USERNAME=your_email@domain.com")
        print("   ALTERYX_PASSWORD=your_password")
        return False
    
    if not ALTERYX_CLIENT_ID:
        print("❌ Missing ALTERYX_CLIENT_ID in .env")
        return False
    
    print(f"Using credentials:")
    print(f"  Username: {ALTERYX_USERNAME}")
    print(f"  Client ID: {ALTERYX_CLIENT_ID}")
    
    # Run the auth generator
    auth_gen_script = SCRIPT_DIR / "alteryx_auth_generator.py"
    if not auth_gen_script.exists():
        print(f"❌ Auth generator not found at {auth_gen_script}")
        return False
    
    try:
        result = subprocess.run(
            [sys.executable, str(auth_gen_script)],
            cwd=str(BACKEND_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        print(result.stdout)
        if result.stderr:
            print("Errors:")
            print(result.stderr)
        
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("❌ Token refresh timed out")
        return False
    except Exception as e:
        print(f"❌ Error running token generator: {e}")
        return False


def test_connection():
    """Test the connection to Alteryx Cloud."""
    print_section("STEP 2: TEST CONNECTION")
    
    print("Run the backend with:")
    print("   cd qlik_app/qlik/qlik-fastapi-backend")
    print("   python main.py")
    print("\nOr if using uvicorn:")
    print("   uvicorn main:app --reload")
    print("\nThen open the UI and click Connect")


def main():
    print_section("ALTERYX TOKEN & CONNECTION SETUP")
    
    if refresh_tokens():
        print("\n✅ Tokens refreshed successfully!")
        test_connection()
        return 0
    else:
        print("\n❌ Failed to refresh tokens")
        print("\nManual steps:")
        print("1. Go to Alteryx One → User Preferences → OAuth 2.0 API Tokens")
        print("2. Generate New Token")
        print("3. Copy the tokens to .env:")
        print("   ALTERYX_ACCESS_TOKEN=...")
        print("   ALTERYX_REFRESH_TOKEN=...")
        return 1


if __name__ == "__main__":
    sys.exit(main())
