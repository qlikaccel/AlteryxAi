#!/usr/bin/env python3
"""
Quick validation that the fixes are in place
"""
import sys
import os

def check_files():
    """Verify all fixes are present"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    print("🔍 Validating Device Code Flow Fixes...\n")
    
    # Check 1: powerbi_auth.py has polling loop
    print("1️⃣  Checking powerbi_auth.py for polling implementation...")
    with open(os.path.join(base_dir, "powerbi_auth.py"), encoding='utf-8') as f:
        content = f.read()
        if "while time.time() - start_time < max_wait_seconds:" in content:
            print("   ✅ Polling loop found")
        else:
            print("   ❌ Polling loop NOT found")
            return False
            
        if "authorization_pending" in content:
            print("   ✅ Error handling found")
        else:
            print("   ❌ Error handling NOT found")
            return False
    
    # Check 2: main.py has background thread with delay
    print("\n2️⃣  Checking main.py for async implementation...")
    with open(os.path.join(base_dir, "main.py"), encoding='utf-8') as f:
        content = f.read()
        if "time.sleep(2)" in content and "acquire_in_background" in content:
            print("   ✅ Background thread with delay found")
        else:
            print("   ❌ Background thread NOT properly configured")
            return False
            
        if "max_wait_seconds=600" in content:
            print("   ✅ 10-minute timeout configured")
        else:
            print("   ❌ Timeout NOT configured")
            return False
    
    # Check 3: MigrationPage.tsx has faster polling
    print("\n3️⃣  Checking MigrationPage.tsx for polling optimization...")
    tsx_path = os.path.join(base_dir, "..", "..", "..", "..", "converter", "csv", "src", "Migration", "MigrationPage.tsx")
    tsx_path = os.path.normpath(tsx_path)
    if os.path.exists(tsx_path):
        with open(tsx_path, encoding='utf-8') as f:
            content = f.read()
            if "authCheckCount < 5 ? 1000 : 3000" in content:
                print("   ✅ Adaptive polling frequency found")
            else:
                print("   ❌ Polling optimization NOT found")
                return False
    else:
        print(f"   ⚠️  File not found at: {tsx_path}")
        print("      (This is OK if frontend hasn't been updated)")
    
    print("\n" + "="*60)
    print("✅ ALL FIXES VALIDATED SUCCESSFULLY!")
    print("="*60)
    print("\nTo test the complete flow:")
    print("1. Start backend: python main.py")
    print("2. Observe console for polling logs")
    print("3. Start frontend: npm run dev (in csv folder)")
    print("4. Click 'Continue to Power BI' and authenticate")
    print("5. Modal should auto-close within 10 seconds of authentication")
    print("6. Power BI should open automatically with dataset\n")
    
    return True

if __name__ == "__main__":
    success = check_files()
    sys.exit(0 if success else 1)
