#!/usr/bin/env python3
"""Test if main.py imports successfully"""
import sys
import traceback

try:
    from main import app
    print("✅ Backend imports successfully!")
    print(f"✅ FastAPI app initialized: {type(app)}")
except Exception as e:
    print(f"❌ Import error: {e}")
    traceback.print_exc()
    sys.exit(1)
