import os
from dotenv import load_dotenv

# Test the data endpoint directly
import requests
load_dotenv("D:/samFullCodeQlik/QlikSense/qlik_app/qlik/qlik-fastapi-backend/.env")

api_key = os.getenv('QLIK_API_KEY')
tenant_url = os.getenv('QLIK_TENANT_URL')

print("="*80)
print("TESTING DATA RETRIEVAL ENDPOINT")
print("="*80)

# Get first app
headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
r = requests.get(f'{tenant_url}/api/v1/apps', headers=headers)
response_data = r.json()

# Handle response format
if isinstance(response_data, dict) and 'data' in response_data:
    apps = response_data['data']
elif isinstance(response_data, list):
    apps = response_data
else:
    apps = []

if apps:
    app_id = apps[0].get('id') or apps[0].get('attributes', {}).get('id')
    print(f"\n✓ App: {app_id}")
    
    # Test the backend endpoint
    backend_url = f"http://127.0.0.1:8000/applications/{app_id}/table/Ford_Dealer_Master/data"
    # backend_url = f"https://qliksense-stuv.onrender.com/applications/{app_id}/table/Ford_Dealer_Master/data"
    print(f"✓ Endpoint: {backend_url}\n")
    
    try:
        backend_r = requests.get(backend_url)
        data = backend_r.json()
        
        print(f"Response Status: {backend_r.status_code}")
        print(f"Success: {data.get('success')}")
        print(f"Rows Retrieved: {len(data.get('rows', []))}")
        
        if data.get('rows'):
            first_row = data['rows'][0]
            print(f"\n✓ REAL DATA FROM QLIK ENGINE:")
            for k, v in list(first_row.items()):
                print(f"  {k}: {v}")
            
            print("\n✅ DATA IS COMING FROM QLIK ENGINE!")
        else:
            print("\n❌ No rows retrieved")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
else:
    print("❌ No apps found")

print("\n" + "="*80)
