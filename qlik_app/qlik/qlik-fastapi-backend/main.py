"""
Entry point wrapper for the Qlik to Power BI Migration Backend

This file maintains backward compatibility with existing startup scripts.
All the actual application code has been moved to the app/ folder for
better organization following industry standards.

The FastAPI app is now in: app/main.py
"""

import sys
import os

# Add the project root to sys.path so imports work correctly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the FastAPI app from the new location
from app.main import app

# Optional imports for backward compatibility
# If any code expects these to be importable from main.py, they'll be available
from app.services.qlik_client import QlikClient
from app.services.processor import PowerBIProcessor
from app.services.mquery_converter import MQueryConverter

if __name__ == "__main__":
    import uvicorn
    
    print("\n" + "="*80)
    print(" " * 20 + "Qlik FastAPI Backend v2.0")
    print("="*80)
    print("\nFeatures:")
    print("  [+] REST API for Qlik Cloud")
    print("  [+] WebSocket connection to Qlik Engine")
    print("  [+] Table and field discovery")
    print("  [+] Script extraction & parsing")
    print("  [+] M Query generation")
    print("  [+] Power BI dataset publisher")
    print("\nProject structure: app/ (organized)")
    print("Entry point: app/main.py")
    print("\nStarting server...")
    print("="*80 + "\n")
    
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
