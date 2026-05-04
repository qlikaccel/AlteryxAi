# 🔐 Alteryx Authentication Fix

## Problem
❌ Your Alteryx access token EXPIRED 6+ hours ago  
❌ Refresh token is also INVALID

## Quick Fix (5 minutes)

### Step 1: Generate New Tokens
1. Go to: **https://us1.alteryxcloud.com**
2. Click **Settings** (top-right corner) → **API Keys**
3. Click **Generate New Key**
4. Copy both tokens:
   - **Access Token** (JWT format)
   - **Refresh Token** (JWT format)

### Step 2: Update Your Credentials  
Run this command and paste your new tokens:

```powershell
cd D:\Alteryx_Update\QlikAi
python scripts\refresh_alteryx_token.py --set-tokens
```

You'll be prompted:
```
Access Token: [paste here]
Refresh Token: [paste here]
Workspace Name (optional): sorim-alteryx-trial-2hcg
```

### Step 3: Verify
```powershell
python scripts\refresh_alteryx_token.py --check
```

Expected output:
```
✅ VALID for 23.9 more hours
```

### Step 4: Restart Backend
```powershell
uvicorn main:app --reload
```

---

## Automatic Token Refresh (After First Setup)

After tokens are valid, this command will auto-refresh:

```powershell
python scripts\refresh_alteryx_token.py --refresh
```

---

## Commands Reference

| Command | Purpose |
|---------|---------|
| `--check` | Check token status (default) |
| `--refresh` | Auto-refresh using refresh token |
| `--set-tokens` | Manually enter new tokens |

---

## What's Fixed

✅ **Token Expiry Management** - Script handles expired tokens automatically  
✅ **Workspace Resolution** - Auto-detects workspace ID from name  
✅ **Error Handling** - Clear error messages in auth endpoint  
✅ **Fallback to ENV** - Uses .env as backup if tokens not provided in request  

---

## Next Steps

1. **Get new tokens** from https://us1.alteryxcloud.com
2. **Run setup script**: `python scripts\refresh_alteryx_token.py --set-tokens`  
3. **Restart backend**: `uvicorn main:app --reload`
4. **Test**: POST to `/api/alteryx/validate-auth` with your credentials

---

## Security Note

⚠️ **Never commit .env to git!** Make sure it's in `.gitignore`

Check: `cat .gitignore | grep ".env"`
