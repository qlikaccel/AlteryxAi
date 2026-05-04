# Automatic Token Refresh on Every Connection - Implementation Summary

## Problem
The token was only being refreshed once. After the first successful connection, subsequent calls to `/validate-auth` were not triggering a refresh because `ensure_fresh_token()` only refreshed when the token was **expired**. Since Alteryx tokens have a 5-minute lifetime, a token that was refreshed 30 seconds ago would not be refreshed again during that window.

User requirement: **Token should be refreshed EVERY TIME the user attempts to connect**, and both `token_storage.json` and `.env` should be updated each time.

## Solution
Added a `force_refresh` parameter throughout the token refresh chain to allow **forced token refresh** on connection attempts, while maintaining the default behavior of **lazy refresh** (only when expired) for other operations.

## Changes Made

### 1. [alteryx_workspace_utils.py] - `ensure_fresh_token()` Function
**Line 292-341**

Added `force_refresh: bool = False` parameter:
```python
def ensure_fresh_token(session: AlteryxSession, force_refresh: bool = False) -> str:
    # Check if refresh is needed
    token_is_expired = is_token_expired(session.access_token)
    needs_refresh = force_refresh or token_is_expired  # ← NEW: honor force_refresh flag
    
    if not needs_refresh:
        return session.access_token
    
    refresh_reason = "force refresh" if force_refresh else "token expired"
    print(f"\n🔄 [ensure_fresh_token] {refresh_reason.capitalize()} — refreshing...")
```

**Impact**: Allows callers to force a refresh even if token isn't expired.

### 2. [alteryx_workspace_utils.py] - `_get_with_refresh()` Function  
**Line 344-398**

Added `force_refresh: bool = False` parameter and passes it to `ensure_fresh_token()`:
```python
def _get_with_refresh(
    url: str,
    session: AlteryxSession,
    params: Optional[dict] = None,
    force_refresh: bool = False,  # ← NEW parameter
) -> dict:
    # Step 1: proactive refresh (with optional force)
    fresh_token = ensure_fresh_token(session, force_refresh=force_refresh)
```

**Impact**: All authenticated API calls can now force token refresh when needed.

### 3. [alteryx_workspace_utils.py] - `list_alteryx_workspaces()` Function
**Line 401-431**

Added `force_refresh: bool = False` parameter and passes it to `_get_with_refresh()`:
```python
def list_alteryx_workspaces(session: AlteryxSession, force_refresh: bool = False) -> list[dict]:
    """Fetch all workspaces accessible to this token."""
    for endpoint in endpoints:
        data = _get_with_refresh(endpoint, session, force_refresh=force_refresh)
```

**Impact**: Workspace listing can now use forced refresh when called from connection flow.

### 4. [alteryx_workspace_utils.py] - `get_workspace_id_by_name()` Function
**Line 434-467**

Added `force_refresh: bool = False` parameter and passes it to `list_alteryx_workspaces()`:
```python
def get_workspace_id_by_name(
    session: AlteryxSession, 
    workspace_name: str, 
    force_refresh: bool = False  # ← NEW parameter
) -> str:
    workspaces = list_alteryx_workspaces(session, force_refresh=force_refresh)
```

**Impact**: Workspace resolution can now use forced refresh when called from connection flow.

### 5. [alteryx_workspace_utils.py] - `create_alteryx_session()` Function
**Line 501-621** - **KEY CHANGE**

Added `force_refresh: bool = True` parameter (defaults to **True**):
```python
def create_alteryx_session(
    access_token: str,
    workspace_name: str,
    refresh_token: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    force_refresh: bool = True,  # ← DEFAULT IS TRUE (force on connection)
) -> AlteryxSession:
    ...
    # Always force refresh on connection attempt
    get_workspace_id_by_name(session, workspace_name, force_refresh=force_refresh)
```

**Critical**: This is the **KEY CHANGE**. `force_refresh=True` as default means:
- Every call to `/validate-auth` will trigger a token refresh
- Both `token_storage.json` and `.env` are updated with fresh tokens
- This happens for EVERY connection attempt, not just when expired

### 6. [alteryx_workspace_utils.py] - `list_alteryx_workflows()` Function
**Line 501-542**

Added `force_refresh: bool = False` parameter for consistency:
```python
def list_alteryx_workflows(
    session: AlteryxSession, 
    workspace_id: Optional[str] = None, 
    force_refresh: bool = False  # ← Defaults to False (lazy refresh)
) -> list[dict]:
    data = _get_with_refresh(endpoint, session, params=params, force_refresh=force_refresh)
```

**Impact**: Workflow retrieval defaults to lazy refresh but can be forced if needed.

## Flow Diagram

### Before (Token only refreshed when expired)
```
User connects → validate-auth → create_alteryx_session()
   ↓
   → ensure_fresh_token() checks is_token_expired()
   ↓
   IF token NOT expired → skip refresh ❌ (Problem!)
   IF token expired → refresh ✅
```

### After (Token always refreshed on connection)
```
User connects → validate-auth → create_alteryx_session(force_refresh=True)
   ↓
   → get_workspace_id_by_name(..., force_refresh=True)
   ↓
   → list_alteryx_workspaces(..., force_refresh=True)
   ↓
   → _get_with_refresh(..., force_refresh=True)
   ↓
   → ensure_fresh_token(..., force_refresh=True)
   ↓
   ALWAYS REFRESH (regardless of expiry) ✅
   ↓
   → persist_alteryx_tokens() saves to token_storage.json + .env
```

## Files Modified

1. **`app/utils/token_manager.py`** - Already fixed for thread-safe token persistence
2. **`app/utils/alteryx_workspace_utils.py`** - Added force_refresh chain

## Behavior Changes

| Endpoint | Before | After |
|----------|--------|-------|
| `POST /validate-auth` | Refresh only if expired | **Always refresh** |
| `GET /workflows` | Refresh only if expired | Refresh only if expired |
| `POST /workflows/materialize` | Refresh only if expired | Refresh only if expired |
| Other API calls | Refresh only if expired | Refresh only if expired |

## Backwards Compatibility
✅ All existing calls continue to work
✅ Default behavior for non-connection endpoints is unchanged (lazy refresh)
✅ Only the connection flow (`create_alteryx_session`) has new default behavior

## Testing the Fix

After restart, test with:
```bash
# Should update token_storage.json and .env
curl -X POST http://localhost:8000/api/alteryx/validate-auth \
  -H "Content-Type: application/json" \
  -d '{"workspace_name": "sorim-alteryx-trial-2hcg"}'

# Check file timestamp changed
ls -l app/token_storage.json
cat app/token_storage.json | jq '.timestamp'
```

Expected: File timestamp should update with current time on every call.

---
**Status**: ✅ Ready for deployment
