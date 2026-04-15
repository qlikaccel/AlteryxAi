# HTML Workflow Extraction - Quick Start Guide

## 🚀 What's New?

Workflows are now extracted from Alteryx Designer using **HTML parsing** instead of API calls. This eliminates the need for valid API tokens!

## ⚡ 5-Minute Setup

### Step 1: Install Dependencies
```bash
cd d:\Alteryx\QlikAi\qlik_app\qlik\qlik-fastapi-backend
pip install -r requirements.txt
# or just: pip install beautifulsoup4>=4.12.0
```

### Step 2: Restart Backend
```bash
python main.py
# Backend starts with new HTML parsing enabled
```

### Step 3: Test It
```bash
# Option A: Using curl
curl "http://localhost:8000/workflows/html"

# Option B: Using Python script
python test_html_workflow_parser.py

# Option C: Using browser
# Visit http://localhost:8000/docs (Swagger UI)
# Try GET /workflows
```

## 📋 Key Differences

### Before (API-based)
```
❌ Requires valid API token
❌ Token expires frequently  
❌ Complex fallback logic
❌ 401 errors when token invalid
```

### Now (HTML-based)
```
✅ Uses session cookies (from login)
✅ No token expiration issues
✅ HTML is always available
✅ Falls back to API if needed
✅ Automatically handles duplicates
```

## 🔗 API Endpoints

### 1. Get Workflows (Primary)
```bash
GET /workflows?limit=50&offset=0
```
- Uses HTML parser first
- Falls back to API if needed
- Returns workflows with method info

### 2. Get Workflows (HTML Only)
```bash
GET /workflows/html?limit=50&offset=0
```
- HTML parsing only (for testing)
- Useful to verify parser works

### 3. Discovery Page
```bash
GET /discovery
```
- Combines HTML + API results
- Removes duplicates
- Shows summary by method

## 🧪 Quick Test Commands

### Test 1: Verify Installation
```bash
python -c "from bs4 import BeautifulSoup; print('✓ OK')"
```

### Test 2: Run Local Parser Test
```bash
python test_html_workflow_parser.py
```

### Test 3: Test With Running Backend
```bash
python test_html_workflow_parser.py --integration
```

### Test 4: Curl the endpoint
```bash
curl "http://localhost:8000/workflows"
```

### Test 5: Check Swagger Docs
```
http://localhost:8000/docs
# Click "Try it out" on GET /workflows
```

## 📊 Response Examples

### Success Response
```json
{
  "success": true,
  "workflows": [
    {
      "id": "workflow-data-processing",
      "name": "workflow_data_processing",
      "source": "html_parser",
      "status": "available"
    }
  ],
  "total": 42,
  "limit": 50,
  "offset": 0,
  "method": "html_parser",
  "message": "Extracted 42 workflows from Alteryx Designer"
}
```

### Error Response (With Auto-Recovery)
```json
{
  "success": true,
  "workflows": [...],
  "method": "api",
  "message": "HTML parsing failed, fell back to API"
}
```

## 🔍 How It Finds Workflows

The parser looks for workflow names in THREE places:

1. **Page Text**
   ```html
   <span>workflow_data_processing</span>
   ```

2. **HTML Attributes**
   ```html
   <div data-workflow="workflow_etl_pipeline">
   <input id="workflow_export">
   ```

3. **JavaScript Data**
   ```javascript
   var workflows = {
       "workflow_data_load": {...},
       "data_transform": {...}
   };
   ```

## ✅ Workflow Name Patterns

The parser extracts names that:
- Start with **"workflow_"** (e.g., `workflow_data_processing`)
- Contain **"data"** (e.g., `data_etl_job`)
- Contain **"dataset"**, **"table"**, or **"workflow"**

## 🐛 Troubleshooting

### Issue: No workflows returned
```bash
# 1. Check backend is running
curl http://localhost:8000/docs

# 2. Verify you're logged in
curl http://localhost:8000/auth/connect

# 3. Try API fallback
curl "http://localhost:8000/workflows?use_api=true"
```

### Issue: BeautifulSoup not found
```bash
pip install beautifulsoup4>=4.12.0
# Restart backend
python main.py
```

### Issue: Getting empty results
```bash
# 1. Check Designer page loads
# In browser: https://us1.alteryxcloud.com/designer/

# 2. Test HTML-only endpoint
curl "http://localhost:8000/workflows/html"

# 3. Check discovery page
curl "http://localhost:8000/discovery"
```

## 📁 Files Changed

- ✅ `requirements.txt` - Added beautifulsoup4
- ✅ `app/services/html_workflow_parser.py` - NEW parser service
- ✅ `app/api/v1/endpoints/workflows_html.py` - NEW endpoints
- ✅ `app/main.py` - Integrated new endpoints

## 📚 Full Documentation

For complete details, see: [HTML_WORKFLOW_EXTRACTION.md](HTML_WORKFLOW_EXTRACTION.md)

Topics covered:
- Architecture details
- Multi-strategy extraction
- Validation rules
- Performance metrics
- Frontend integration examples
- Advanced troubleshooting

## 🎯 Next Steps

1. **Verify Installation**
   ```bash
   python test_html_workflow_parser.py
   ```

2. **Start Backend**
   ```bash
   python main.py
   ```

3. **Test Endpoints**
   - http://localhost:8000/workflows
   - http://localhost:8000/discovery
   - http://localhost:8000/docs

4. **Check Frontend**
   - Open Discovery page in web UI
   - Verify workflows display correctly
   - Try sorting/filtering

## ❓ FAQ

**Q: Do I need a valid API token?**  
A: No! HTML parsing uses your session cookies.

**Q: What if HTML parsing fails?**  
A: It automatically falls back to API endpoints.

**Q: How often are workflows updated?**  
A: Fresh on each request (no caching).

**Q: Can I force API mode?**  
A: Yes: `GET /workflows?use_api=true`

**Q: Does this work with token auth?**  
A: Yes, both session cookies and Bearer tokens are supported.

**Q: What about performance?**  
A: Typical response time: 2-5 seconds.

## 📞 Support

1. Check logs: `uvicorn main:app --reload`
2. Enable debug: Set `logging.DEBUG`
3. Test endpoints: Use Swagger at `/docs`
4. Check HTML: Open Designer in browser + DevTools

---

**Status**: ✅ Ready to use  
**Version**: 1.0  
**Last Updated**: April 14, 2026
