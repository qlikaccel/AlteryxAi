#!/usr/bin/env python
"""Detailed test of main.py module state"""

import sys
import importlib.util

print("=" * 70)
print("DETAILED MAIN.PY ANALYSIS")
print("=" * 70)

# Load module spec
spec = importlib.util.spec_from_file_location(
    "main", 
    "main.py"
)
main_module = importlib.util.module_from_spec(spec)

print(f"\n1. Module created: {main_module}")
print(f"2. Module dict before exec: {len(main_module.__dict__)} items")

try:
    spec.loader.exec_module(main_module)
    print(f"3. Module executed successfully")
except Exception as e:
    print(f"3. ❌ Error executing module: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print(f"4. Module dict after exec: {len(main_module.__dict__)} items")

# Check for app
if hasattr(main_module, 'app'):
    print(f"5. ✅ app object FOUND: {type(main_module.app)}")
else:
    print(f"5. ❌ app object NOT FOUND")
    
# List some attributes
public_attrs = [x for x in dir(main_module) if not x.startswith('_')]
print(f"\n6. Public attributes ({len(public_attrs)}):")
for attr in sorted(public_attrs)[:20]:
    try:
        val = getattr(main_module, attr)
        val_type = type(val).__name__
        print(f"   - {attr}: {val_type}")
    except:
        pass

# Check module globals directly
print(f"\n7. Direct module dict keys containing 'app':")
app_keys = [k for k in main_module.__dict__.keys() if 'app' in k.lower()]
for key in app_keys:
    val = main_module.__dict__[key]
    print(f"   - {key}: {type(val).__name__}")

print("\n" + "=" * 70)
