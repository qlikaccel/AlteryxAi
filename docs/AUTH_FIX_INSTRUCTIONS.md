# 401 Unauthorized Fix - Expired API Token

## Problem
Your Alteryx API token **EXPIRED on April 13, 2026 at 15:49:11 UTC**
- Current time: April 14, 2026
- Token was valid for: ~5 minutes only (very short expiration)

## Solution: Generate New API Token

### Step 1: Get New Token from Alteryx Cloud
1. Go to https://us1.alteryxcloud.com
2. Log in with: `accelerators@sorim.ai` / `@1tr3yx123`
3. Navigate to **Settings** → **API Keys**
4. Click **Generate New API Key** (or **Create New Key**)
5. Copy the full token value

### Step 2: Update users.json

Replace the entire `alteryx_api_token` value with your new token in:
`qlik_app/qlik/qlik-fastapi-backend/users.json`

Current structure:
```json
{
  "accelerators@sorim.ai": {
    "password": "@1tr3yx123",
    "tenant": "https://us1.alteryxcloud.com",
    "type": "alteryx",
    "alteryx_api_token": "[PASTE YOUR NEW TOKEN HERE]"
  },
  ...
}
```

### Step 3: Verify & Restart

1. Save `users.json`
2. Restart the backend: `python main.py` in the qlik-fastapi-backend folder
3. Test the workflows endpoint

### Troubleshooting

If you still get 401 after updating:

1. **Verify token format** - should be a long JWT (3 parts separated by dots)
2. **Check expiration** - use this Python script:
   ```python
   import json, base64
   from datetime import datetime
   
   with open('users.json') as f:
       token = json.load(f)['accelerators@sorim.ai']['alteryx_api_token']
   
   payload = token.split('.')[1] + '=' * (4 - len(token.split('.')[1]) % 4)
   decoded = json.loads(base64.urlsafe_b64decode(payload))
   exp_date = datetime.utcfromtimestamp(decoded['exp'])
   print(f"Token expires: {exp_date}")
   print(f"Is valid: {datetime.utcnow() < exp_date}")
   ```

3. **Verify credentials** - double-check username/password match your Alteryx account
4. **Check tenant URL** - ensure it's correct: `https://us1.alteryxcloud.com`

### Additional Notes

- API tokens typically expire after a set period (hours/days depending on your Alteryx config)
- Recommendation: Store tokens in `.env` file instead of `users.json` for better security
- Consider using Alteryx's OAuth2 flow for long-term integration
