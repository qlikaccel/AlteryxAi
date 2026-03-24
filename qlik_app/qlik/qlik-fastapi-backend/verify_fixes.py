#!/usr/bin/env python3
"""
Verify all fixes are in place
"""
import os
import sys

def check_file_content(filepath, search_string, description):
    """Check if file contains required content"""
    if not os.path.exists(filepath):
        print(f"❌ {description}: FILE NOT FOUND")
        return False
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            if search_string in content:
                print(f"✅ {description}")
                return True
            else:
                print(f"❌ {description}: NOT FOUND IN FILE")
                return False
    except Exception as e:
        print(f"❌ {description}: ERROR - {e}")
        return False

def main():
    print("\n" + "="*70)
    print("VERIFYING ALL FIXES ARE IN PLACE")
    print("="*70 + "\n")
    
    all_good = True
    
    # Check 1: powerbi_auth.py has REST polling
    print("📋 Checking Backend Implementation...")
    print("-" * 70)
    
    auth_file = "e:\\qlikRender\\QlikSense\\qlik_app\\qlik\\qlik-fastapi-backend\\powerbi_auth.py"
    
    if not check_file_content(auth_file, 
        'grant_type": "urn:ietf:params:oauth:grant-type:device_code',
        "1. REST polling with proper device code grant"):
        all_good = False
    
    if not check_file_content(auth_file,
        'requests.post(token_url, data=data, timeout=10)',
        "2. Direct HTTP POST to token endpoint"):
        all_good = False
    
    if not check_file_content(auth_file,
        'if error == "authorization_pending"',
        "3. Proper handling of authorization_pending error"):
        all_good = False
    
    if not check_file_content(auth_file,
        'self._save_token(access_token, expires_in)',
        "4. Token caching on success"):
        all_good = False
    
    print("\n📋 Checking Frontend Implementation...")
    print("-" * 70)
    
    migration_file = "e:\\qlikRender\\QlikSense\\qlik_app\\converter\\csv\\src\\Migration\\MigrationPage.tsx"
    
    if not check_file_content(migration_file,
        'window.open(powerBIUrl, "_blank")',
        "5. Power BI auto-open with window.open()"):
        all_good = False
    
    if not check_file_content(migration_file,
        '01b07483-f683-47bb-9c7c-8e3e5e3b7e11',
        "6. Correct workspace ID configured"):
        all_good = False
    
    if not check_file_content(migration_file,
        'https://app.powerbi.com/groups',
        "7. Power BI workspace URL construction"):
        all_good = False
    
    if not check_file_content(migration_file,
        'proceedWithPublish()',
        "8. Dataset creation after Power BI opens"):
        all_good = False
    
    print("\n📋 Checking Main Backend Entry Point...")
    print("-" * 70)
    
    main_file = "e:\\qlikRender\\QlikSense\\qlik_app\\qlik\\qlik-fastapi-backend\\main.py"
    
    if not check_file_content(main_file,
        'acquire_token_by_device_code',
        "9. Background token acquisition configured"):
        all_good = False
    
    print("\n" + "="*70)
    if all_good:
        print("✅ ALL FIXES ARE IN PLACE!")
        print("="*70)
        print("\n📚 Next Steps:")
        print("  1. Start backend: python main.py")
        print("  2. Start frontend: npm run dev")
        print("  3. Export Qlik table and test the flow")
        print("  4. Power BI should open automatically after authentication")
        print("\n" + "="*70 + "\n")
        return 0
    else:
        print("❌ SOME FIXES ARE MISSING!")
        print("="*70)
        print("\n⚠️  Please check the files listed above")
        print("\n" + "="*70 + "\n")
        return 1

if __name__ == "__main__":
    sys.exit(main())
