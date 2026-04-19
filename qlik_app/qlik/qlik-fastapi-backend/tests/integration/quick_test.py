import os
from dotenv import load_dotenv
load_dotenv()

api_key = os.getenv('QLIK_API_KEY')
tenant_url = os.getenv('QLIK_TENANT_URL')

# Get first app
import requests
headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
r = requests.get(f'{tenant_url}/api/v1/apps', headers=headers)
response = r.json()

# Handle different response formats
if isinstance(response, list):
    apps = response
elif isinstance(response, dict) and 'data' in response:
    apps = response['data']
elif isinstance(response, dict) and 'items' in response:
    apps = response['items']
else:
    apps = []

if apps and len(apps) > 0:
    app_id = apps[0].get('id') or apps[0].get('attributes', {}).get('id')
    print(f'✓ Testing with app: {app_id}')
    
    # Test the endpoint
    from qlik_websocket_client import QlikWebSocketClient
    ws = QlikWebSocketClient()
    
    if ws.connect_to_app(app_id):
        # Get tables
        tables_info = ws.get_app_tables_simple(app_id)
        tables = tables_info.get('tables', [])
        
        if tables:
            table_name = tables[0].get('name')
            print(f'\n✓ Testing table: {table_name}')
            
            # Test endpoint
            data = ws.get_table_data(app_id, table_name, 10)
            success = data.get('success')
            rows_count = len(data.get('rows', []))
            print(f'✓ Success: {success}, Rows: {rows_count}')
            
            # Check for placeholders
            if data.get('rows'):
                first_row = data['rows'][0]
                has_placeholder = any('not accessible' in str(v) for v in first_row.values())
                if has_placeholder:
                    print('\n⚠️ WARNING: Placeholder data detected')
                    print('First row:', first_row)
                else:
                    print('\n✓ Got real data:')
                    for k, v in list(first_row.items())[:3]:
                        print(f'  {k}: {v}')
        else:
            print('❌ No tables found')
    else:
        print('❌ WebSocket connection failed')
else:
    print('❌ No apps found')
