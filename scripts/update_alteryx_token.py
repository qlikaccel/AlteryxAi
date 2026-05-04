#!/usr/bin/env python
"""
Script to update Alteryx API token in users.json
Run this after generating a new token from Alteryx Cloud
"""

import json
import sys

def update_token():
    """Update the alteryx_api_token in users.json"""
    
    users_file = "qlik_app/qlik/qlik-fastapi-backend/users.json"
    
    print("\n" + "="*70)
    print(" ALTERYX API TOKEN UPDATER")
    print("="*70)
    
    # Step 1: Instructions
    print("\n📋 STEP 1: Generate New Token from Alteryx Cloud")
    print("-" * 70)
    print("""
1. Open browser and go to: https://us1.alteryxcloud.com
2. Log in with:
   - Email: accelerators@sorim.ai
   - Password: @1tr3yx123
3. Go to: Settings → API Keys
4. Click: "Generate New Key" or "Create New API Key"
5. Copy the ENTIRE token (it will be very long)
6. Paste it below when prompted
    """)
    
    # Step 2: Get new token from user
    print("\n🔑 STEP 2: Paste Your New Token")
    print("-" * 70)
    print("Paste the new API token from Alteryx Cloud:")
    print("(It should be a long string starting with 'eyJ...')")
    print()
    
    new_token = input("Enter new token: ").strip()
    
    if not new_token:
        print("❌ Error: Token cannot be empty!")
        sys.exit(1)
    
    if len(new_token) < 100:
        print("⚠️  Warning: Token seems too short. API tokens are usually 500+ characters.")
        confirm = input("Continue anyway? (y/n): ").strip().lower()
        if confirm != 'y':
            sys.exit(1)
    
    # Step 3: Load users.json
    print("\n📂 STEP 3: Updating users.json")
    print("-" * 70)
    
    try:
        with open(users_file, 'r') as f:
            users = json.load(f)
        print(f"✓ Loaded {users_file}")
    except FileNotFoundError:
        print(f"❌ Error: {users_file} not found!")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"❌ Error: {users_file} is not valid JSON!")
        sys.exit(1)
    
    # Step 4: Update token
    if "accelerators@sorim.ai" not in users:
        print("❌ Error: 'accelerators@sorim.ai' not found in users.json!")
        sys.exit(1)
    
    users["accelerators@sorim.ai"]["alteryx_api_token"] = new_token
    
    # Step 5: Save
    try:
        with open(users_file, 'w') as f:
            json.dump(users, f, indent=2)
        print(f"✓ Updated {users_file}")
    except Exception as e:
        print(f"❌ Error writing file: {e}")
        sys.exit(1)
    
    # Step 6: Verify
    print("\n✅ STEP 4: Verify Token")
    print("-" * 70)
    
    # Check expiration
    try:
        import base64
        from datetime import datetime
        
        parts = new_token.split('.')
        if len(parts) != 3:
            print("⚠️  Token format warning: doesn't appear to be a valid JWT")
        else:
            payload = parts[1]
            payload += '=' * (4 - len(payload) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(payload))
            
            if 'exp' in decoded:
                exp_date = datetime.utcfromtimestamp(decoded['exp'])
                current = datetime.utcnow()
                is_valid = current < exp_date
                
                print(f"Token Expiration: {exp_date}")
                print(f"Current Time:    {current}")
                print(f"Status:          {'✓ VALID' if is_valid else '❌ EXPIRED'}")
                
                if is_valid:
                    hours_left = (exp_date - current).total_seconds() / 3600
                    print(f"Valid for:       {hours_left:.1f} hours")
    except Exception as e:
        print(f"⚠️  Could not verify token: {e}")
    
    print("\n" + "="*70)
    print("✓ Token updated successfully!")
    print("="*70)
    print("\n📌 Next Steps:")
    print("   1. Also update .env file if needed:")
    print("      ALTERYX_API_TOKEN=<same_token_here>")
    print("   2. Restart backend: python main.py")
    print("   3. Test workflows endpoint: GET /workflows")
    print("\n")

if __name__ == "__main__":
    try:
        update_token()
    except KeyboardInterrupt:
        print("\n❌ Cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
