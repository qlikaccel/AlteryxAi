#!/usr/bin/env python3
"""
Power BI Login Script - Uses Device Code Flow
This allows users to login interactively without sharing credentials
"""

import os
from msal import PublicClientApplication
import requests
from dotenv import load_dotenv

# Load environment variables
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=ENV_PATH)

TENANT_ID = os.getenv("POWERBI_TENANT_ID", "e912ee28-32ed-4aed-9332-e5d3c6cea258")
CLIENT_ID = os.getenv("POWERBI_CLIENT_ID", "24a540bc-c770-4e07-a92d-503a1dd7ac80")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"

SCOPES = [
    "https://analysis.windows.net/powerbi/api/Dataset.ReadWrite.All",
    "https://analysis.windows.net/powerbi/api/Workspace.ReadWrite.All",
    "https://analysis.windows.net/powerbi/api/Report.ReadWrite.All"
]

print("=" * 80)
print(" " * 20 + "Power BI Device Code Login")
print("=" * 80)
print(f"\n📋 Tenant ID:  {TENANT_ID}")
print(f"🔑 Client ID:  {CLIENT_ID}")
print()

try:
    # Create public client application
    app = PublicClientApplication(
        client_id=CLIENT_ID,
        authority=AUTHORITY
    )

    # Initiate device flow
    print("🔄 Initiating device code flow...\n")
    flow = app.initiate_device_flow(scopes=SCOPES)

    if "error" in flow:
        print(f"❌ Error initiating device flow: {flow['error_description']}")
        exit(1)

    # Show message to user
    print("👇 COPY & PASTE THIS INTO YOUR BROWSER:")
    print("=" * 80)
    print(flow["message"])
    print("=" * 80)
    print()

    # Acquire token using device flow
    print("⏳ Waiting for you to complete login in browser...")
    print("   (You have 15 minutes)\n")

    token = app.acquire_token_by_device_flow(flow)

    if "error" in token:
        print(f"❌ Authentication failed: {token.get('error_description', token['error'])}")
        exit(1)

    access_token = token["access_token"]
    print("✅ Login Successful!\n")

    # Test the token by listing workspaces
    print("🔍 Testing token by listing your workspaces...\n")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    # List workspaces
    url = "https://api.powerbi.com/v1.0/myorg/groups"
    r = requests.get(url, headers=headers, timeout=10)

    if r.status_code == 200:
        workspaces = r.json().get("value", [])
        print(f"✅ Found {len(workspaces)} workspace(s):\n")
        for ws in workspaces:
            print(f"  • {ws.get('name')}")
            print(f"    ID: {ws.get('id')}\n")
    else:
        print(f"❌ Error listing workspaces: {r.status_code}")
        print(f"   {r.text[:300]}")
        exit(1)

    # Save token to file for backend use
    token_file = "powerbi_token.txt"
    with open(token_file, "w") as f:
        f.write(access_token)
    print(f"💾 Token saved to: {token_file}\n")

    print("=" * 80)
    print("✅ Ready to use with Power BI API!")
    print("=" * 80)

except Exception as e:
    print(f"❌ Error: {str(e)}")
    import traceback
    traceback.print_exc()
    exit(1)
