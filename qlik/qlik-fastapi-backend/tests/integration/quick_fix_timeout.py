#!/usr/bin/env python3
"""
Quick Fix Script for WebSocket Connection Timeout
Run this when experiencing connection timeouts
"""

def print_quick_fixes():
    fixes = {
        "1. Test Connectivity": [
            "Run: python test_websocket_connection.py",
            "This will diagnose the exact issue"
        ],
        "2. Check API Key": [
            "Open .env file",
            "Verify QLIK_API_KEY is not expired",
            "Get new key from: https://qlikcloud.com/console/api-keys"
        ],
        "3. Check Network": [
            "Run: ping c8vlzp3sx6akvnh.in.qlikcloud.com",
            "If fails → Internet issue",
            "If succeeds → Firewall might be blocking WebSocket"
        ],
        "4. Check Firewall/Proxy": [
            "Open firewall settings",
            "Check if port 443 is blocked",
            "Check if wss:// protocol is allowed",
            "If behind proxy, update .env with proxy settings"
        ],
        "5. Retry with Backoff": [
            "Automatic retry enabled (3 attempts)",
            "Wait 2-8 seconds between retries",
            "Check logs for actual error"
        ],
        "6. Check Qlik Status": [
            "Go to: https://status.qlik.com",
            "If service is down → Wait and retry later"
        ]
    }
    
    print("\n" + "="*70)
    print("  WEBSOCKET CONNECTION TIMEOUT - QUICK FIX GUIDE")
    print("="*70 + "\n")
    
    for step, actions in fixes.items():
        print(f"{step}")
        for action in actions:
            print(f"  • {action}")
        print()
    
    print("="*70)
    print("\nMost Common Reasons:")
    print("  1. Firewall blocking WebSocket connections")
    print("  2. Network connectivity issues")
    print("  3. Expired API key")
    print("  4. Qlik Cloud temporarily unavailable")
    print("  5. High network latency")
    print("\n" + "="*70 + "\n")

if __name__ == "__main__":
    print_quick_fixes()
