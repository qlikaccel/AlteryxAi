# Alteryx Tokens: .env Configuration Guide

## The Solution: Use Refresh Tokens (365 days) ✅

**Problem**: Access tokens expire every 5 minutes
**Solution**: Use refresh tokens (last 365 days) to keep system connected permanently

---

## How Tokens Work in Alteryx Cloud

| Token Type | Lifespan | Purpose | Where Used |
|-----------|----------|---------|-----------|
| **Access Token** | 5 minutes ⏱️ | Actual API authentication | Quick temporary use |
| **Refresh Token** | 365 days 📅 | Generate new access tokens | Long-term storage in .env |

---

## What We Changed in the Backend

With the new **automatic token refresh**, the system now:

1. **Before each API call**: Check if access token is expired
2. **If expired**: Use refresh_token to get a fresh access token (no manual action needed!)
3. **Then**: Make the API call with the fresh token
4. **Never**: Get stuck on 5-minute expiration window

**Code flow**:
```
{User makes request}
    ↓
[Check: Is access_token valid?]
    ↓
   No → [Refresh using refresh_token]
    ↓
   Yes ↘
         [Make API call with fresh token]
         ↓
      [Return data to user]
```

---

## Setup Instructions

### Step 1: Get Your Refresh Token from Alteryx Cloud

1. **Go to**: `https://us1.alteryxcloud.com`
2. **Login** with: `accelerators@sorim.ai` / `@1tr3yx123`
3. **Click**: Settings → API Keys
4. **Look for**: "Refresh Token" or "Long-lived Token"
5. **Copy** the full refresh token value

### Step 2: Update `.env` File

Edit `qlik_app/qlik/qlik-fastapi-backend/.env`:

```
# MOST IMPORTANT: Use REFRESH token (lasts 365 days!)
ALTERYX_REFRESH_TOKEN=eyJhbGciOiJSUzI1NiIsImtpZCI6ImRlZmF1bHQifQ.eyJzdWI...

# These are temporary (5 min), system auto-refreshes them
ALTERYX_ACCESS_TOKEN=eyJraWQiOiJhMjNiYTUwMC1hYzdlLTExZjAtYjBkMS05YjZjMG...
```

**Key Point**: 
- ✅ Save **ALTERYX_REFRESH_TOKEN** (365 days) - this is your backup
- ✅ System will auto-generate **ALTERYX_ACCESS_TOKEN** as needed
- ❌ Don't worry about access token expiring - it refreshes automatically

### Step 3: Verify Setup

```bash
cd qlik_app\qlik\qlik-fastapi-backend

# Test automatic refresh
python test_auto_refresh.py
```

**Expected output**:
```
✅ Got tokens from .env
✅ Access token refreshed automatically
✅ Got 1 workspace(s)
  - sorim-alteryx-trial-2hcg
```

### Step 4: Start Backend + Test UI

```bash
# Terminal 1: Start backend
uvicorn main:app --reload

# Terminal 2: Test in browser
curl "http://localhost:8000/api/alteryx/workflows?workspace_name=sorim-alteryx-trial-2hcg"

# Or visit frontend and click "Connect"
```

---

## Troubleshooting

### Problem: "Token refresh returned 400"
**Cause**: Refresh token is invalid or expired  
**Solution**:
1. Go to Alteryx Cloud UI
2. Generate a new API key
3. Copy the new refresh token to `.env`

### Problem: "WHERE parameter email has invalid undefined value"
**Cause**: Refresh token was generated incorrectly  
**Solution**:
1. Delete current ALTERYX_REFRESH_TOKEN from .env
2. Go to Alteryx Cloud and generate fresh API token
3. Make sure you copy the entire token value

### Problem: System still getting 401 errors
**Cause**: Both tokens are invalid  
**Solution**:
```bash
# Go to https://us1.alteryxcloud.com → Settings → API Keys
# Generate a completely new API key
# Update .env with new REFRESH_TOKEN
# Delete old ACCESS_TOKEN line (system will auto-generate)
```

---

## File Changes Made

### `app/utils/alteryx_workspace_utils.py`

#### Added Functions:
1. **`is_token_expired(token, buffer_seconds=30)`**
   - Decodes JWT to check expiration time
   - Returns True if expired or expiring within 30 seconds
   - Prevents timeouts during API calls

2. **`ensure_fresh_token(session)`**
   - Called before every API request
   - If access_token is expired: uses refresh_token to get new one
   - Returns fresh access_token ready for use
   - No manual intervention needed!

3. **Updated `_get_with_refresh()`**
   - Now PROACTIVELY refreshes before API call
   - Not just on 401 error (reactive)
   - Prevents expiration mid-call

### `test_auto_refresh.py` (NEW)
- Demonstrates automatic token refresh
- Verifies refresh_token is working
- Shows system handles expiration automatically

---

## System Architecture Now

```
┌─────────────────────────────────────┐
│   Frontend (React UI)               │
│   "Connect Workspace" button        │
└────────────┬────────────────────────┘
             │ POST /validate-auth
             ↓
┌─────────────────────────────────────┐
│   FastAPI Backend                   │
│   Receives workspace_name           │
└────────────┬────────────────────────┘
             │
             ↓
┌─────────────────────────────────────┐
│   alteryx_workspace_utils.py         │
│   ✅ NEW: ensure_fresh_token()      │
│          ↓ Check expiration         │
│          ↓ Auto-refresh if needed   │
│          ↓ Uses REFRESH_TOKEN       │
└────────────┬────────────────────────┘
             │
             ↓
┌─────────────────────────────────────┐
│   Alteryx Cloud API                 │
│   ✅ Receives fresh access_token    │
│   ✅ Works even if called after 5m  │
└─────────────────────────────────────┘
```

---

## Key Improvements

| Before | After |
|--------|-------|
| ❌ Access token expires every 5 min | ✅ Automatically refreshed before each call |
| ❌ 401 errors after 5 minutes | ✅ No more expiration errors |
| ❌ Manual token refresh needed | ✅ Completely automatic |
| ❌ Only keep ACCESS_TOKEN in env | ✅ Keep REFRESH_TOKEN in env (365 days) |
| ❌ System fails between token refreshes | ✅ System works 24/7 |

---

## JWT Token Structure (For Reference)

### Access Token (5 min)
```json
{
  "header": {
    "alg": "RS256",
    "kid": "..."
  },
  "payload": {
    "client_id": "af1b5321-afe0-...",
    "iat": 1776339478,
    "exp": 1776339778  ← Expires in 5 minutes!
  }
}
```

### Refresh Token (365 days)
```json
{
  "header": {
    "alg": "RS256",
    "kid": "default"
  },
  "payload": {
    "sub": "8dceb6a4-6535-...",
    "exp": 1807873610  ← Expires in 365 days!
  }
}
```

---

## Next Steps

1. **[IMMEDIATE]** Get fresh ALTERYX_REFRESH_TOKEN from Alteryx Cloud UI
2. **[IMMEDIATE]** Update `.env` with the new refresh token
3. **[HIGH]** Run `python test_auto_refresh.py` to verify
4. **[HIGH]** Restart backend: `uvicorn main:app --reload`
5. **[OPTIONAL]** Save this guide for future reference

---

## Questions?

The system now:
- ✅ Uses refresh_token (365 day lifespan) from .env
- ✅ Auto-detects when access_token expires
- ✅ Auto-refreshes using refresh_token before each call
- ✅ Never gets stuck on token expiration
- ✅ Works 24/7 without manual intervention

**Result**: Set ALTERYX_REFRESH_TOKEN once → System works forever! 🚀
