#!/usr/bin/env python3
"""
Check and diagnose Power BI service principal permissions
"""

import os
import json
import requests
from powerbi_auth import get_auth_manager

def check_permissions():
    print("=== POWER BI SERVICE PRINCIPAL PERMISSIONS DIAGNOSTIC ===")
    
    # Load environment variables
    tenant_id = os.getenv("POWERBI_TENANT_ID")
    client_id = os.getenv("POWERBI_CLIENT_ID")
    client_secret = os.getenv("POWERBI_CLIENT_SECRET")
    workspace_id = os.getenv("POWERBI_WORKSPACE_ID")
    
    print(f"Tenant ID: {tenant_id}")
    print(f"Client ID: {client_id}")
    print(f"Has Client Secret: {bool(client_secret)}")
    print(f"Workspace ID: {workspace_id}")
    
    # Test authentication
    print("\n=== AUTHENTICATION TEST ===")
    auth = get_auth_manager()
    
    print(f"Token valid: {auth.is_token_valid()}")
    print(f"Access token: {auth.access_token[:50] + '...' if auth.access_token else 'None'}")
    
    # Test connection
    print("\n=== CONNECTION TEST ===")
    connection_result = auth.test_connection()
    print(f"Connection result: {connection_result}")
    
    # If connection fails, provide detailed analysis
    if not connection_result.get("success"):
        print("\n=== DETAILED ERROR ANALYSIS ===")
        error_msg = connection_result.get("error", "")
        print(f"Error message: {error_msg}")
        
        if "401" in error_msg:
            print("\n🔍 PERMISSIONS ISSUE DETECTED:")
            print("The service principal token is valid but doesn't have Power BI permissions.")
            print("\n🔧 REQUIRED ACTIONS:")
            print("1. Go to Azure Portal: https://portal.azure.com")
            print("2. Navigate to Azure Active Directory")
            print("3. Find your app registration (Client ID: 6413a69e-b951-4d7f-9c8e-af5f040ca3ea)")
            print("4. Go to 'API permissions'")
            print("5. Add the following permissions:")
            print("   - https://analysis.windows.net/powerbi/api/Dataset.ReadWrite.All")
            print("   - https://analysis.windows.net/powerbi/api/Workspace.ReadWrite.All")
            print("   - https://analysis.windows.net/powerbi/api/Report.ReadWrite.All")
            print("6. Click 'Grant admin consent for [your tenant]'")
            print("7. Wait 5-10 minutes for permissions to propagate")
            print("\n📝 PERMISSIONS CONFIGURATION:")
            print("   - Type: Application permissions (not delegated)")
            print("   - Admin consent required: Yes")
            print("   - Status: Must be 'Granted for [tenant]'")
        
        if "workspace" in error_msg.lower():
            print("\n🔍 WORKSPACE ACCESS ISSUE:")
            print("The service principal doesn't have access to the specified workspace.")
            print("\n🔧 REQUIRED ACTIONS:")
            print("1. Go to Power BI Service: https://app.powerbi.com")
            print("2. Navigate to the workspace: 7219790d-ee43-4137-b293-e3c477a754f0")
            print("3. Add the service principal as a member or admin")
            print("4. Use the service principal's object ID or app ID")
    
    # Test token scopes
    print("\n=== TOKEN SCOPE ANALYSIS ===")
    if auth.access_token:
        try:
            # Decode JWT token to check scopes
            import jwt
            decoded = jwt.decode(auth.access_token, options={"verify_signature": False})
            print(f"Token scopes: {decoded.get('scp', 'No scopes found')}")
            print(f"Token audience: {decoded.get('aud', 'No audience found')}")
            print(f"Token issuer: {decoded.get('iss', 'No issuer found')}")
        except Exception as e:
            print(f"Could not decode token: {e}")
    
    print("\n=== FINAL RECOMMENDATIONS ===")
    print("1. Verify service principal has Power BI API permissions in Azure AD")
    print("2. Grant admin consent for the permissions")
    print("3. Add service principal to Power BI workspace")
    print("4. Wait 10 minutes for changes to propagate")
    print("5. Test again after permissions are granted")

if __name__ == "__main__":
    check_permissions()