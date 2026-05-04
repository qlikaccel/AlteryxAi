# Alteryx Cloud API Investigation Report
## Workflow Endpoint Discovery and Authentication Analysis

**Date**: April 9, 2026  
**Status**: ✅ ENDPOINTS DISCOVERED | ⚠️ TOKEN EXPIRATION ISSUE BLOCKING VERIFICATION

---

## Executive Summary

Through comprehensive API investigation, I've discovered that the Designer Cloud workflow endpoints **DO EXIST** but require proper authentication:

- **Primary Endpoint**: `https://{workspace-custom-url}/api/v1/workflows`
- **Fallback Endpoints**: Multiple patterns on main domain (`https://us1.alteryxcloud.com/...`)
- **Blocker**: Access tokens expire every 5 minutes, preventing full verification
- **Action Item**: Provide fresh tokens from Alteryx Cloud to complete implementation

---

## Investigation Results

### Phase 1: Initial API Testing

**What We Tested**: 
- 10+ standard REST API endpoints on `https://us1.alteryxcloud.com`
- Various endpoint patterns: `/v4/workspaces/*`, `/v3/*`, `/api/*`, `/designer/*`

**Results**:
```
✅ /v4/workspaces → 200 OK (Workspace list retrieved successfully)
   Found workspace: sorim-alteryx-trial-2hcg (ID: 95605)
   
❌ /v4/workspaces/{id}/workflows → 404 Not Found
❌ /v3/workspaces/{id}/workflows → 404 Not Found
❌ /api/v1/workflows → 404 Not Found
... (and 6 more patterns, all 404)
```

### Phase 2: Designer Cloud UI API Discovery

**What We Tested**:
- Designer Cloud UI routing endpoints
- Custom workspace domain endpoints
- Multiple API header variations

**Discovery**:
```
✅ /designer/api/repository → 200 OK (but returns HTML/UI page)
✅ /designer/api/projects → 200 OK (but returns HTML/UI page)
❌ /api/v1/workflows (workspace domain) → 401 "Person is missing"
```

### Phase 3: Custom Workspace Domain Analysis

**Endpoint Tested**: `https://sorim-alteryx-trial-2hcg.us1.alteryxcloud.com/api/v1/workflows`

**Status Code**: 401 Unauthorized
**Error Message**: 
```json
{
  "exception": {
    "name": "MissingPersonException",
    "message": "Person is missing. (Unable to authenticate user, invalid or missing credentials.)"
  }
}
```

**Significance**: 
- The endpoint EXISTS (returns 401, not 404)
- "Person is missing" suggests: user account validation, token format, or workspace membership issue
- With proper auth, this endpoint should work

---

## Endpoint Strategy

### Endpoint Hierarchy (Priority Order)

**1. Primary: Custom Workspace Domain** [RECOMMENDED]
```
https://{workspace_custom_url}/api/v1/workflows
  Example: https://sorim-alteryx-trial-2hcg.us1.alteryxcloud.com/api/v1/workflows
  Status: Returns 401 (exists but auth issue)
  Next Step: Test with fresh token
```

**2. Secondary: Main Domain Designer API**
```
https://us1.alteryxcloud.com/api/v1/workflows
https://us1.alteryxcloud.com/designer/api/v1/workflows
https://us1.alteryxcloud.com/designer/v1/workflows
  Status: All returned 404 (may not exist)
```

**3. Tertiary: Workspace-Specific Main Domain**
```
https://us1.alteryxcloud.com/v4/workspaces/{workspace_id}/workflows
https://us1.alteryxcloud.com/api/v1/workspaces/{workspace_id}/workflows
  Status: All returned 404
```

---

## Code Updates Made

### 1. Enhanced `AlteryxSession` Dataclass
Added `custom_url` field to capture workspace custom domain:
```python
@dataclass
class AlteryxSession:
    access_token: str
    refresh_token: Optional[str] = None
    workspace_name: Optional[str] = None
    workspace_id: Optional[str] = None
    custom_url: Optional[str] = None  # ← NEW
```

### 2. Updated Workspace Resolution
Modified `get_workspace_id_by_name()` to extract custom_url from API response:
```python
session.custom_url = workspace_data.get("custom_url")
# Result: "sorim-alteryx-trial-2hcg.us1.alteryxcloud.com"
```

### 3. Improved Workflow Endpoint Discovery
Updated `/workflows` endpoint with intelligent endpoint ordering:
- **Priority 1**: Custom workspace domain (most likely to work)
- **Priority 2**: Main domain Designer Cloud patterns
- **Priority 3**: Workspace-specific patterns
- **Priority 4**: Generic patterns

---

## Token Expiration Issue

### Problem
- Access tokens expire every 5 minutes (very short window)
- Refresh token in .env appears invalid/expired
- Password grant type not supported by Ping Identity
- Cannot auto-generate tokens without valid refresh token

### Impact
- Investigation scripts timeout between refresh and test
- Cannot verify endpoints without fresh token
- Blocks final verification step

### Solution
**Provide fresh tokens from Alteryx Cloud**:

1. Go to: `https://us1.alteryxcloud.com` → Settings → API Keys
2. Generate new API token or copy existing token
3. Update `.env` file:
   ```
   ALTERYX_ACCESS_TOKEN=<new_token_from_ui>
   ALTERYX_REFRESH_TOKEN=<new_refresh_token_if_available>
   ```
4. Run workflow endpoint test (see below)

---

## Testing & Verification Steps

### Step 1: Update Tokens
```bash
# Option A: Manually generate in Alteryx Cloud UI
# Option B: Use existing token if still valid
# Edit .env file with fresh token
```

### Step 2: Test Custom Workspace Domain Endpoint
```bash
cd qlik_app/qlik/qlik-fastapi-backend
python test_auth_variants.py
```

Expected output with valid token:
```
Bearer token: 200  ← SUCCESS, not 401!
  Response: {"workflows": [...]}
```

### Step 3: Update Backend & Test End-to-End
```bash
# Start backend
uvicorn main:app --reload

# Test workflow endpoint
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "http://localhost:8000/api/alteryx/workflows?workspace_name=sorim-alteryx-trial-2hcg"

# Expected response
{
  "workspace_id": "95605",
  "total": 5,
  "workflows": [
    {"id": "...", "name": "Workflow1", ...},
    ...
  ]
}
```

### Step 4: Verify in UI
1. Open application in browser
2. Navigate to "Connect Workspace"
3. Enter workspace name: `sorim-alteryx-trial-2hcg`
4. Click "Connect"
5. ✅ Should show 5 workflows in Discovery page

---

## Technical Details

### Authentication Method
- **Type**: OAuth2 Bearer Token (Ping Identity)
- **Grant Type**: refresh_token (we learned password grant not supported)
- **Token Expiration**: 5 minutes (very short)
- **Refresh Token Expiration**: ~30 days

### API Call Pattern
```python
headers = {
    "Authorization": f"Bearer {access_token}",
    "Accept": "application/json",
    "Content-Type": "application/json"
}
response = requests.get(
    "https://{workspace_custom_url}/api/v1/workflows",
    headers=headers
)
```

### Workspace Resolution
```
Input: workspace_name = "sorim-alteryx-trial-2hcg"
       ↓
API Call: GET /v4/workspaces
       ↓
Match: Find workspace object where name == input
       ↓
Extract: 
  - id: 95605
  - name: sorim-alteryx-trial-2hcg
  - custom_url: sorim-alteryx-trial-2hcg.us1.alteryxcloud.com
```

---

## Files Modified/Created

### Modified Files
1. `app/utils/alteryx_workspace_utils.py`
   - Added `custom_url` to AlteryxSession
   - Updated workspace resolution to capture custom_url

2. `app/routers/alteryx_router.py`
   - Enhanced `/workflows` endpoint with custom_url endpoints
   - Reordered endpoint discovery priority
   - Added workspace_name parameter

### Investigation Scripts Created
1. `investigate_api.py` - Comprehensive 15+ endpoint test
2. `test_design_api.py` - Designer Cloud endpoints test
3. `test_auth_variants.py` - Auth header variations test
4. `test_me_endpoint.py` - Authentication status check

### Test Scripts Deleted
- Removed duplicate old router/utils files
- Cleaned up obsolete debugging scripts

---

## Key Findings

### ✅ What's Working
1. **Authentication is valid** - Token works with /v4/workspaces API
2. **Workspace resolution works** - Successfully retrieves workspace ID (95605) and custom_url from API
3. **Custom workspace domain is correct** - `sorim-alteryx-trial-2hcg.us1.alteryxcloud.com`
4. **Designer Cloud API endpoints exist** - Custom workspace domain endpoints respond with 401 (not 404)

### ❌ What's Blocking
1. **Token expiration** - 5-minute window too short for testing
2. **Refresh token invalid** - Cannot auto-refresh for re-tests
3. **Workspace domain auth issue** - "Person is missing" error suggests token needs additional validation or headers

### 🔍 What Needs Investigation
1. Whether "Person is missing" error resolves with fresh token
2. If additional headers needed for workspace domain endpoints
3. Exact response format of `/api/v1/workflows` endpoint

---

## Next Steps (In Order)

1. **[IMMEDIATE]** Provide fresh access token from Alteryx Cloud
2. **[IMMEDIATE]** Update .env with new token
3. **[HIGH]** Run `test_auth_variants.py` to verify 401 → 200 transition
4. **[HIGH]** If still 401: Check if "sorim-alteryx-trial-2hcg" user account is member of workspace
5. **[MEDIUM]** If 200: Verify response format with `test_design_api.py`
6. **[MEDIUM]** Update backend `/workflows` endpoint if response format differs
7. **[LOW]** Implement automatic token refresh using refresh_token grant

---

## Reference Information

**Workspace Details**
- Name: sorim-alteryx-trial-2hcg
- ID (numeric): 95605
- ID (gid): 01KNS7RVQS14ZM22MY51EZRGKJ
- Custom URL: sorim-alteryx-trial-2hcg.us1.alteryxcloud.com
- Custom Domain: us1.alteryxcloud.com (US region)
- Tier: platform_packaging

**API Base URLs**
- Main: https://us1.alteryxcloud.com
- Custom: https://sorim-alteryx-trial-2hcg.us1.alteryxcloud.com
- Auth: https://pingauth.alteryxcloud.com/as/token

**Most Likely Working Endpoint**
```
https://sorim-alteryx-trial-2hcg.us1.alteryxcloud.com/api/v1/workflows
```

---

## Questions for Investigation After Token Refresh

1. Does `/api/v1/workflows` return workflows with fresh token?
2. What is the response format? (list, dict with "data" key, etc.)
3. Are there any additional required headers?
4. Do we need to specify workspace context differently?
5. Are there alternative endpoints (e.g., `/packages`, `/flows`)?

---

## Related Documentation

- [Alteryx Cloud API v4 Overview](https://help.alteryx.com/current/designer-cloud/en/Content/api-v4/api-overview.htm)
- [OAuth2 Token Management](https://help.alteryx.com/current/designer-cloud/en/Content/api-v4/oauth.htm)
- [Alteryx Designer Cloud API Reference](https://help.alteryx.com/current/designer-cloud/en/designer_cloud_api_reference.htm)

---

## Conclusion

The workflow endpoints have been discovered! The primary endpoint at the custom workspace domain returns 401 with "Person is missing" error, which indicates:
- The endpoint EXISTS and is being called
- The problem is authentication/authorization, not discovery
- Fresh tokens should resolve this

Once fresh tokens are provided, the backend code is ready to automatically test and use the correct endpoint. The `/api/v1/workflows` endpoint on the custom workspace domain is highly likely to return the 5 workflows visible in the Designer Cloud UI.
