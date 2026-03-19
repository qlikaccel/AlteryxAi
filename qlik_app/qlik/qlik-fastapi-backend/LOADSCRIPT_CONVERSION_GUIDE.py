"""
Complete LoadScript to PowerBI M Query Conversion Feature

USAGE GUIDE - Step by Step Testing
==================================

This feature converts Qlik Cloud LoadScript to PowerBI M Query with detailed logging at each phase.

PHASES:
-------
Phase 1: Initialize & Test Connection
Phase 2: Fetch Applications List
Phase 3: Fetch Application Details
Phase 4: Fetch LoadScript
Phase 5: Parse LoadScript
Phase 6: Convert to PowerBI M Query


METHOD 1: Full Pipeline (Recommended - Single Endpoint)
=======================================================

The /full-pipeline endpoint executes all phases (1-6) in one call:

URL: POST http://localhost:8000/api/migration/full-pipeline

Query Parameters:
  - app_id: Your Qlik Cloud app ID (string, required)
  - auto_download: Optional boolean (default: false)

Example:
--------
POST http://localhost:8000/api/migration/full-pipeline?app_id=YOUR_APP_ID

Response includes:
  - status: success/error
  - phases: Results from each phase
  - m_query: Final PowerBI M Query code
  - detailed_results: Complete data from all phases
  - summary: Statistics


METHOD 2: Step-by-Step (For Analysis & Debugging)
==================================================

Step 1: FETCH LOADSCRIPT (Phases 1-4)
--------------------------------------

URL: POST http://localhost:8000/api/migration/fetch-loadscript

Query Parameters:
  - app_id: Your Qlik Cloud app ID (required)

Example:
--------
POST http://localhost:8000/api/migration/fetch-loadscript?app_id=YOUR_APP_ID

Response:
{
  "status": "success",
  "app_id": "YOUR_APP_ID",
  "app_name": "Application Name",
  "loadscript": "// Script content here...",
  "script_length": 5000,
  "method": "direct_api",
  "timestamp": "2026-02-25T10:30:00"
}

LOGGING OUTPUT:
├─ PHASE 0: Initializing LoadScript Fetcher
├─ PHASE 1: Testing Connection to Qlik Cloud
├─ PHASE 2: Fetching Applications List
├─ PHASE 3: Fetching Application Details
└─ PHASE 4: Fetching Loadscript


Step 2: PARSE LOADSCRIPT (Phase 5)
-----------------------------------

URL: POST http://localhost:8000/api/migration/parse-loadscript

Query Parameters:
  - loadscript: The loadscript content from Step 1 (required)

Example:
--------
POST http://localhost:8000/api/migration/parse-loadscript?loadscript=YOUR_SCRIPT_HERE

Response:
{
  "status": "success",
  "summary": {
    "tables_count": 5,
    "fields_count": 25,
    "connections_count": 2,
    "transformations_count": 8,
    "joins_count": 2,
    "variables_count": 3,
    "comments_count": 10
  },
  "details": {
    "tables": [...],
    "fields": [...],
    "data_connections": [...],
    "transformations": [...],
    "joins": [...],
    "variables": [...],
    "comments": [...]
  }
}

LOGGING OUTPUT:
├─ PHASE 5: Parsing Loadscript
├─ Step 5.1: Extracting Comments
├─ Step 5.2: Extracting LOAD Statements
├─ Step 5.3: Extracting Table Names
├─ Step 5.4: Extracting Field Definitions
├─ Step 5.5: Extracting Data Connections
├─ Step 5.6: Extracting Transformations
├─ Step 5.7: Detecting JOIN Operations
├─ Step 5.8: Extracting Variable Definitions
└─ PARSING COMPLETED SUCCESSFULLY


Step 3: CONVERT TO POWERBI M QUERY (Phase 6)
---------------------------------------------

URL: POST http://localhost:8000/api/migration/convert-to-mquery

Query Parameters:
  - parsed_script_json: The full parsed result from Step 2 as JSON (required)

Example (format the full parsed result as JSON):
--------
POST http://localhost:8000/api/migration/convert-to-mquery?parsed_script_json={"status":"success",...}

Response:
{
  "status": "success",
  "m_query": "let\n  Source = ...\nlet\n  #..."
  "query_length": 8500,
  "warnings_count": 2,
  "errors_count": 0,
  "statistics": {
    "total_connections_converted": 2,
    "total_tables_converted": 5,
    "total_fields_converted": 25,
    "total_transformations": 8,
    "total_joins": 2
  }
}

LOGGING OUTPUT:
├─ PHASE 6: Converting to PowerBI M Query
├─ Step 6.1: Converting Data Connections
├─ Step 6.2: Converting Table Definitions
├─ Step 6.3: Converting Field Definitions
├─ Step 6.4: Converting Transformations
├─ Step 6.5: Converting JOIN Operations
├─ Step 6.6: Assembling Final M Query
└─ CONVERSION COMPLETED SUCCESSFULLY


Step 4: DOWNLOAD M QUERY
------------------------

URL: POST http://localhost:8000/api/migration/download-mquery

Query Parameters:
  - m_query: The M query from Step 3 (required)
  - filename: Output filename (optional, default: powerbi_query.m)

Example:
--------
POST http://localhost:8000/api/migration/download-mquery?m_query=let\n  Source=...&filename=myquery.m

Response:
Binary .m file download


TESTING WITH PYTHON
===================

import requests
import json

# Configuration
BASE_URL = "http://localhost:8000"
APP_ID = "YOUR_QLIK_APP_ID_HERE"

# METHOD 1: Complete Pipeline in One Call
print("=" * 80)
print("TESTING FULL PIPELINE")
print("=" * 80)

pipeline_response = requests.post(
    f"{BASE_URL}/api/migration/full-pipeline",
    params={"app_id": APP_ID}
)

if pipeline_response.status_code == 200:
    pipeline_data = pipeline_response.json()
    print("✅ Pipeline successful!")
    print(f"M Query Length: {len(pipeline_data['m_query'])} chars")
    print("\nPhases completed:")
    for phase, result in pipeline_data['phases'].items():
        print(f"  - {phase}: {result['status']}")
else:
    print(f"❌ Pipeline failed: {pipeline_response.status_code}")
    print(pipeline_response.text)


TESTING WITH CURL
=================

# Complete Pipeline
curl -X POST "http://localhost:8000/api/migration/full-pipeline?app_id=YOUR_APP_ID" \\
  -H "Accept: application/json"

# Fetch Loadscript only
curl -X POST "http://localhost:8000/api/migration/fetch-loadscript?app_id=YOUR_APP_ID" \\
  -H "Accept: application/json"

# View API documentation
curl -X GET "http://localhost:8000/api/migration/pipeline-help" \\
  -H "Accept: application/json"


LOGGING FEATURES
================

All modules (fetcher, parser, converter) include comprehensive logging:

✓ PHASE markers for easy tracking
✓ Step-by-step progress indicators
✓ Success checkmarks (✅)
✓ Error indicators (❌)
✓ Warning indicators (⚠️)
✓ Timestamps for each major operation
✓ Statistics and summaries
✓ Percentage/count breakdowns
✓ File sizes and data dimensions

Watch the backend console/logs to see real-time progress!


ERROR HANDLING
==============

If an error occurs in any phase, all modules provide:
- Clear error messages
- Status indicators (status: "error")
- HTTP status codes:
  - 400: Bad request / validation error
  - 500: Server error
- Detailed error descriptions in response

Check logs for full stack traces and debugging info.


NEXT STEPS AFTER CONVERSION
============================

1. Download the M query file (.m)
2. Open Power Query Editor in Power BI
3. Go to: Get Data > More > Blank Query
4. Switch to Advanced Editor
5. Paste the generated M query
6. Click Done
7. Review and adjust data types as needed
8. Load the data into Power BI
9. Configure relationships in the model

Some manual adjustments may be needed:
- Data types (Text, Number, Date, etc.)
- Date format specifications
- Decimal separators
- Database connection strings
- Authentication credentials


NOTES
=====

- All operations are read-only (no data is modified in Qlik)
- Loadscript fetching requires valid Qlik Cloud credentials
- Convert operations work with any loadscript text
- Full conversion can take several seconds for large scripts
- Watch server logs for detailed progress at each phase
- All logging goes to stdout and can be captured/filtered as needed
"""

# STANDALONE EXAMPLE USAGE
if __name__ == "__main__":
    import requests
    import json
    import time
    
    BASE_URL = "http://localhost:8000"
    
    print(__doc__)
    
    print("\n" + "=" * 80)
    print("API ENDPOINTS AVAILABLE")
    print("=" * 80)
    
    # Get pipeline help
    try:
        help_response = requests.get(f"{BASE_URL}/api/migration/pipeline-help")
        if help_response.status_code == 200:
            help_data = help_response.json()
            print("\n✅ API is running!")
            print(f"\nAvailable endpoints: {len(help_data.get('endpoints', []))}")
            for endpoint in help_data.get('endpoints', [])[-5:]:  # Show last 5
                print(f"  - {endpoint}")
        else:
            print("❌ API not responding correctly")
    except Exception as e:
        print(f"❌ Cannot connect to API: {str(e)}")
        print(f"\n📌 Make sure the backend is running at {BASE_URL}")
        print("   Run: python main.py (in the backend directory)")
