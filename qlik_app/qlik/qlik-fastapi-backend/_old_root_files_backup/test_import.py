#!/usr/bin/env python
"""Test if main.py loads correctly"""

import sys
import traceback

print("=" * 60)
print("Testing main.py import...")
print("=" * 60)

try:
    import main
    print("✅ main.py imported successfully")
    
    if hasattr(main, 'app'):
        print(f"✅ app object exists: {type(main.app)}")
    else:
        print("❌ app object NOT found!")
        print(f"Available attributes: {[x for x in dir(main) if not x.startswith('_')][:10]}")
        
except Exception as e:
    print(f"❌ Error importing main.py:")
    print(f"   {type(e).__name__}: {str(e)}")
    print("\nFull traceback:")
    traceback.print_exc()

print("=" * 60)
