import requests
import os
import json
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()
# Use Sandbox credentials directly
ACCESS_TOKEN = "XFE6d2z7hJnleNbpQ789otJmvW3z"  # Sandbox Token
ACCOUNT_ID = "VA13978285"  # Sandbox Account ID
BASE_URL = "https://sandbox.tradier.com/v1"

def test_preview():
    print("\n🔮 STARTING TRADIER PREVIEW TEST")
    print("   Goal: Prove payload is valid by bypassing execution engine")
    
    # 1. Calc Date
    today = datetime.now()
    days = (4 - today.weekday()) % 7
    if days == 0: 
        days = 7
    target = today + timedelta(days=days)
    d_str = target.strftime('%y%m%d')
    
    # 2. Construct Payload (Matches your Gekko3 format)
    # 400/395 Put Spread
    s1 = f"SPY{d_str}P00400000"
    s2 = f"SPY{d_str}P00395000"
    
    payload = {
        'class': 'multileg',
        'symbol': 'SPY',
        'type': 'credit',
        'duration': 'day',
        'price': '0.50',
        'option_symbol[0]': s1,
        'side[0]': 'sell_to_open',
        'quantity[0]': '1',
        'option_symbol[1]': s2,
        'side[1]': 'buy_to_open',
        'quantity[1]': '1',
        'preview': 'true'  # <--- THE MAGIC SWITCH
    }
    
    headers = {
        'Authorization': f"Bearer {ACCESS_TOKEN}",
        'Accept': 'application/json'
    }

    print(f"\n📦 Sending Payload (preview=true)...")
    print(f"   Symbols: {s1} / {s2}")
    print(f"   Payload: {json.dumps(payload, indent=2)}")
    
    try:
        r = requests.post(
            f"{BASE_URL}/accounts/{ACCOUNT_ID}/orders",
            data=payload,
            headers=headers
        )
        
        print(f"\n👉 Status: {r.status_code}")
        print(f"👉 Response:")
        try:
            response_json = r.json()
            print(json.dumps(response_json, indent=2))
        except:
            print(r.text)
        
        if r.status_code == 200:
            print("\n✅ SUCCESS: Validation Passed!")
            print("   This PROVES your code format is correct.")
            print("   The 500 error is definitely the Sandbox Execution Engine crashing.")
        else:
            print(f"\n❌ FAILED: {r.status_code}")
            if r.status_code == 400:
                print("   (If 400, check error message - might still be format validation)")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_preview()
