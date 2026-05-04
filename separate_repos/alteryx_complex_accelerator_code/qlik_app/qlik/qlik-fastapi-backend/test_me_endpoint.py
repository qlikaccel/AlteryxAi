#!/usr/bin/env python3
"""Test /me endpoints to check authentication"""

import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv('ALTERYX_ACCESS_TOKEN')
BASE_URL = 'https://us1.alteryxcloud.com'
WORKSPACE_DOMAIN = 'sorim-alteryx-trial-2hcg.us1.alteryxcloud.com'

headers = {'Authorization': f'Bearer {TOKEN}', 'Accept': 'application/json'}

# Try /me endpoint on both domains
endpoints = [
    (BASE_URL, '/me'),
    (BASE_URL, '/api/v1/me'),
    (BASE_URL, '/api/users/me'),
    (f'https://{WORKSPACE_DOMAIN}', '/me'),
    (f'https://{WORKSPACE_DOMAIN}', '/api/v1/me'),
    (f'https://{WORKSPACE_DOMAIN}', '/api/users/me'),
]

print('Testing /me endpoints to check auth...\n')

for base, endpoint in endpoints:
    url = f'{base}{endpoint}'
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        # Shorten the path for display if it's too long
        path = f'{base[-20:]}{endpoint}' if len(base) > 20 else f'{base}{endpoint}'
        print(f'{path}: {resp.status_code}')
        if resp.status_code in [200, 400, 401]:
            try:
                data = resp.json()
                print(f'  Response: {json.dumps(data, indent=2)[:300]}')
            except:
                print(f'  Response: {resp.text[:300]}')
    except Exception as e:
        print(f'{endpoint}: Error - {str(e)[:50]}')
