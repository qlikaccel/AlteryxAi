#!/usr/bin/env python3
"""Test different auth formats on custom workspace domain"""

import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv('ALTERYX_ACCESS_TOKEN')
WORKSPACE_DOMAIN = 'sorim-alteryx-trial-2hcg.us1.alteryxcloud.com'

print('='*80)
print('Testing custom workspace domain with different auth formats')
print('='*80)

# Try different auth header formats
auth_variants = [
    ('Bearer token', {'Authorization': f'Bearer {TOKEN}', 'Accept': 'application/json'}),
    ('Token header', {'X-API-Token': TOKEN, 'Accept': 'application/json'}),
    ('Auth header', {'Authorization': f'OAuth {TOKEN}', 'Accept': 'application/json'}),
    ('No auth', {'Accept': 'application/json'}),
]

endpoint = '/api/v1/workflows'
url = f'https://{WORKSPACE_DOMAIN}{endpoint}'

for auth_name, headers in auth_variants:
    resp = requests.get(url, headers=headers, timeout=5)
    print(f'\n{auth_name}: {resp.status_code}')
    try:
        data = resp.json()
        if isinstance(data, dict) and 'error' in data:
            error = data.get('error', 'unknown')
            print(f'  Error: {error}')
        elif isinstance(data, list):
            print(f'  SUCCESS! Got {len(data)} items!')
            if data and isinstance(data[0], dict):
                print(f'  First item keys: {list(data[0].keys())}')
        else:
            print(f'  Got: {type(data).__name__}')
    except Exception as e:
        print(f'  Response (first 300 chars): {resp.text[:300]}')
