# Alteryx Connection Fix - Token Refresh Guide

## Problem
Your tokens are expired:
- ❌ `jwt expired` - Your ACCESS_TOKEN has expired
- ❌ `400 Bad Request` - Your REFRESH_TOKEN is also invalid

## Solution
Since you have USERNAME and PASSWORD, we can automatically generate fresh tokens!

## STEP-BY-STEP FIX

### Step 1: Verify Your .env File Has These Credentials

Edit: `qlik_app/qlik/qlik-fastapi-backend/.env`

```bash
# Make sure these are set:
ALTERYX_TENANT_URL=https://us1.alteryxcloud.com
ALTERYX_USERNAME=accelerators@sorim.ai          # ✅ Must be present
ALTERYX_PASSWORD=@1tr3yx123                      # ✅ Must be present
ALTERYX_CLIENT_ID=af1b5321-afe0-48c2-966a-c77d74e98085  # ✅ Must be present
ALTERYX_WORKSPACE_ID=01KNS7RVQS14ZM22MY51EZRGKJ
ALTERYX_WORKSPACE_NAME=sorim-alteryx-trial-2hcg
```

### Step 2: Run Token Refresh Script

Open PowerShell and run:

```powershell
cd d:\Alteryx_Update\QlikAi\qlik_app\qlik\qlik-fastapi-backend
python setup_alteryx.py
```

Expected output:
```
==========================================
  ALTERYX TOKEN & CONNECTION SETUP
==========================================

STEP 1: REFRESH ALTERYX TOKENS

Using credentials:
  Username: accelerators@sorim.ai
  Client ID: af1b5321-afe0-48c2-966a-c77d74e98085

🔵 Generating fresh tokens using username: accelerators@sorim.ai
✅ Fresh tokens generated successfully!
   Access Token: eyJra...
   Refresh Token: eyJhb...
✅ Updated .env with fresh tokens
   Restart the backend to use fresh tokens
```

### Step 3: Restart Backend

```powershell
cd d:\Alteryx_Update\QlikAi\qlik_app\qlik\qlik-fastapi-backend
python main.py
# or
uvicorn main:app --reload
```

You should see:
```
INFO: Uvicorn running on http://127.0.0.1:8000
```

### Step 4: Connect in UI

1. Open AlteryxAI application
2. Go to **Connect** tab
3. Enter workspace name: `sorim-alteryx-trial-2hcg`
4. Click **Connect** button

Expected flow:
```
✅ Creating Alteryx session for workspace: sorim-alteryx-trial-2hcg
✅ Fetching workspaces list...
  Trying: https://us1.alteryxcloud.com/v4/workspaces
  ✅ Found 1 workspace(s)
✅ Auth validation successful!
```

Then you'll see:
```
🔵 Fetching workflows for workspace: 01KNS7RVQS14ZM22MY51EZRGKJ
  Trying: https://us1.alteryxcloud.com/v3/workspaces/.../workflows
  ✅ Success!
```

---

## If Still Getting 401 Error

### Cause 1: USERNAME/PASSWORD Wrong
- Go to `Alteryx One` → verify your login works
- Update `.env` with correct credentials

### Cause 2: CLIENT_ID Wrong  
- Go to `Alteryx One` → User Preferences → OAuth 2.0 API Tokens
- Copy the CLIENT_ID shown
- Update `.env`

### Cause 3: Workspace Name Wrong
- Go to `Alteryx One` → Workspaces
- Find your workspace name (should be: `sorim-alteryx-trial-2hcg`)
- Use exact spelling in Connect form

---

## Manual Token Generation (If Script Fails)

If `setup_alteryx.py` doesn't work, manually generate tokens:

1. Go to **Alteryx One** → **User Preferences** → **OAuth 2.0 API Tokens**
2. Click **Generate New Token**
3. Copy the token information shown
4. Update `.env`:
   ```bash
   ALTERYX_ACCESS_TOKEN=eyJra...        # New access token
   ALTERYX_REFRESH_TOKEN=eyJhb...       # New refresh token  
   ```
5. Restart backend
6. Try connecting again

---

## What These Scripts Do

### `alteryx_auth_generator.py`
- Uses your USERNAME/PASSWORD to authenticate
- Generates fresh ACCESS_TOKEN and REFRESH_TOKEN
- Automatically updates `.env` file
- No need for CLIENT_SECRET

### `setup_alteryx.py`
- Wraps the auth generator7. Verifies credentials are set
- Provides clear success/failure messages
- Guides next steps

---

## Expected Response Flow

```
User: Click Connect button
  ↓
Frontend: POST /api/alteryx/validate-auth
  Body: { workspace_name: "sorim-alteryx-trial-2hcg" }
  ↓
Backend: Creates session with fresh tokens
  1. Calls /v4/workspaces
  2. Filters for matching workspace name
  3. Resolves workspace_id
  ↓  
Frontend: Receives workspace_id + fresh tokens
  ↓
Frontend: GET /api/alteryx/workflows?workspace_id=...
  ↓
Backend: Tries multiple endpoints
  1. /v3/workspaces/{id}/workflows
  2. /v4/workspaces/{id}/workflows
  3. /designer/api/workflows
  4. /api/v3/workflows
  ↓
Frontend: Displays workflows in Discovery tab ✅
```

---

## Still Need Help?

Check these files for debugging:
- Backend logs: Terminal output (all console prints visible)
- Error details: Check `Response:` messages in console
- Token validation: Run `python setup_alteryx.py` to verify tokens work

Your tokens are refreshed every time you:
1. Click Connect (uses `/validate-auth`)
2. Load Discovery tab (uses `/workflows`)

---

**Ready? Run: `python setup_alteryx.py`** 🚀
