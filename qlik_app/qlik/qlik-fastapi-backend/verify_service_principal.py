#!/usr/bin/env python3
"""
Verify all service principal changes are in place
"""
import powerbi_auth
import inspect

print("\n" + "="*70)
print("VERIFYING SERVICE PRINCIPAL FIX")
print("="*70 + "\n")

# Check if ConfidentialClientApplication is imported
source = inspect.getsource(powerbi_auth)
if 'ConfidentialClientApplication' in source:
    print('✅ ConfidentialClientApplication imported')
else:
    print('❌ ConfidentialClientApplication NOT imported')

# Check if /.default scope is used
if '/.default' in source:
    print('✅ Service principal scope (/.default) configured')
else:
    print('❌ Service principal scope NOT configured')

# Check if acquire_token_for_client is used
if 'acquire_token_for_client' in source:
    print('✅ acquire_token_for_client method used')
else:
    print('❌ acquire_token_for_client NOT used')

print('\n' + "="*70)
print('Testing token acquisition...')
print("="*70 + "\n")

from powerbi_auth import get_auth_manager
auth = get_auth_manager()
result = auth.acquire_token_by_device_code()

if result.get('success'):
    print('✅✅✅ SERVICE PRINCIPAL TOKEN ACQUISITION WORKS! ✅✅✅')
    print(f'   Message: {result.get("message")}')
    print(f'   Expires in: {result.get("expires_in")} seconds')
else:
    error = result.get('error', 'Unknown')
    print(f'❌ Error: {error[:100]}')

print("\n" + "="*70 + "\n")
