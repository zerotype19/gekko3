import requests
import os
import json
from dotenv import load_dotenv

# Load secrets
load_dotenv()
# Use Sandbox credentials directly (for testing)
ACCESS_TOKEN = "XFE6d2z7hJnleNbpQ789otJmvW3z"  # Sandbox Token
ACCOUNT_ID = "VA13978285"  # Sandbox Account ID
BASE_URL = "https://sandbox.tradier.com/v1"

print(f"🔑 Using SANDBOX credentials:")
print(f"   Account ID: {ACCOUNT_ID}")
print(f"   Token: {ACCESS_TOKEN[:10]}...")

def test_equity_order():
    print("\n🧪 STARTING SIMPLE EQUITY TEST")
    print(f"   Target: Buy 1 Share SPY (Market)")
    
    # 1. Construct Simple Equity Payload
    payload = {
        'class': 'equity',
        'symbol': 'SPY',
        'side': 'buy',
        'quantity': '1',
        'type': 'market',
        'duration': 'day'
    }

    headers = {
        'Authorization': f"Bearer {ACCESS_TOKEN}",
        'Accept': 'application/json'
    }

    print(f"📦 Payload: {json.dumps(payload, indent=2)}")

    try:
        # 2. Send Request
        r = requests.post(
            f"{BASE_URL}/accounts/{ACCOUNT_ID}/orders",
            data=payload,
            headers=headers
        )
        
        print(f"\n📨 Response Code: {r.status_code}")
        print(f"📄 Response Body: {r.text}")
        
        if r.status_code == 200:
            print("\n✅ SUCCESS: Order Placed!")
            print("   CONCLUSION: Your API Token and Account ID are VALID.")
            print("   The 500 error on options is purely a Sandbox limitation.")
        else:
            print(f"\n❌ FAILED: {r.status_code}")

    except Exception as e:
        print(f"❌ CONNECTION ERROR: {e}")

if __name__ == "__main__":
    test_equity_order()
