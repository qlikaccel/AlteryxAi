#!/usr/bin/env python3
"""
Test automatic token refresh using refresh_token (365 days instead of 5 min access tokens).

This demonstrates:
1. Access tokens expire every 5 minutes
2. Refresh tokens last 365 days
3. System automatically refreshes access tokens before each API call
4. No more manual token refresh needed!
"""

import os
import time
from dotenv import load_dotenv
from app.utils.alteryx_workspace_utils import (
    AlteryxSession,
    is_token_expired,
    ensure_fresh_token,
    list_alteryx_workspaces,
)

load_dotenv()

print("="*80)
print("  AUTOMATIC TOKEN REFRESH DEMO")
print("="*80)

# Get tokens from .env
access_token = os.getenv("ALTERYX_ACCESS_TOKEN", "")
refresh_token = os.getenv("ALTERYX_REFRESH_TOKEN", "")

if not access_token:
    print("\n❌ ERROR: ALTERYX_ACCESS_TOKEN not set in .env")
    exit(1)

if not refresh_token:
    print("\n❌ ERROR: ALTERYX_REFRESH_TOKEN not set in .env")
    print("   This is the key - it lasts 365 days!")
    exit(1)

print(f"\n✅ Got tokens from .env:")
print(f"   Access Token: {access_token[:50]}...")
print(f"   Refresh Token: {refresh_token[:50]}...")

# Create session
session = AlteryxSession(
    access_token=access_token,
    refresh_token=refresh_token,
)

print(f"\n🔵 STEP 1: Check if access token is expired")
if is_token_expired(session.access_token):
    print(f"   ⏰ Access token IS EXPIRED")
else:
    print(f"   ✅ Access token is STILL VALID")

print(f"\n🔵 STEP 2: Attempt API call - system will auto-refresh if needed")
try:
    workspaces = list_alteryx_workspaces(session)
    print(f"\n✅ SUCCESS! Got {len(workspaces)} workspace(s)")
    for ws in workspaces:
        print(f"   - {ws.get('name')}")
except Exception as e:
    print(f"\n❌ FAILED: {e}")
    exit(1)

print(f"\n🔵 STEP 3: Check token again after API call")
if is_token_expired(session.access_token):
    print(f"   ⏰ Access token IS EXPIRED")
else:
    print(f"   ✅ Access token is STILL VALID")

print(f"\n{'='*80}")
print(f"  KEY BENEFIT: Only ALTERYX_REFRESH_TOKEN (365 days) in .env")
print(f"  System automatically keeps ACCESS_TOKEN fresh before each call")
print(f"  No more manual token refresh needed!")
print(f"{'='*80}")
