#!/usr/bin/env python3
"""Test Designer Cloud API endpoints to find workflow data"""

import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv('ALTERYX_ACCESS_TOKEN')
BASE_URL = 'https://us1.alteryxcloud.com'

print('='*80)
print('TESTING DESIGNER CLOUD API ENDPOINTS')
print('='*80)

headers = {'Authorization': f'Bearer {TOKEN}'}

# Test repository endpoint
print('\n1️⃣  Testing /designer/api/repository')
print('-'*80)
resp = requests.get(f'{BASE_URL}/designer/api/repository', headers=headers)
print(f'Status: {resp.status_code}')
if resp.status_code == 200:
    try:
        data = resp.json()
        print(f'Response type: {type(data).__name__}')
        if isinstance(data, dict):
            print(f'Keys: {list(data.keys())[:10]}')
            print(f'Full response (first 2000 chars):\n{json.dumps(data, indent=2)[:2000]}')
        elif isinstance(data, list):
            print(f'List length: {len(data)}')
            if data:
                first_item = data[0]
                if isinstance(first_item, dict):
                    print(f'First item keys: {list(first_item.keys())}')
                print(f'First item (first 1000 chars):\n{json.dumps(first_item, indent=2)[:1000]}')
    except Exception as e:
        print(f'Error parsing JSON: {e}')
        print(f'Raw response: {resp.text[:500]}')
else:
    print(f'Response: {resp.text[:500]}')

# Test projects endpoint
print('\n\n2️⃣  Testing /designer/api/projects')
print('-'*80)
resp = requests.get(f'{BASE_URL}/designer/api/projects', headers=headers)
print(f'Status: {resp.status_code}')
if resp.status_code == 200:
    try:
        data = resp.json()
        print(f'Response type: {type(data).__name__}')
        if isinstance(data, dict):
            print(f'Keys: {list(data.keys())[:10]}')
            print(f'Full response (first 2000 chars):\n{json.dumps(data, indent=2)[:2000]}')
        elif isinstance(data, list):
            print(f'List length: {len(data)}')
            if data:
                first_item = data[0]
                if isinstance(first_item, dict):
                    print(f'First item keys: {list(first_item.keys())}')
                print(f'First item (first 1000 chars):\n{json.dumps(first_item, indent=2)[:1000]}')
    except Exception as e:
        print(f'Error parsing JSON: {e}')
        print(f'Raw response: {resp.text[:500]}')
else:
    print(f'Response: {resp.text[:500]}')
