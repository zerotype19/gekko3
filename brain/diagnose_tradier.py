import requests
import os
import json
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Load secrets
load_dotenv()
# For this diagnostic, we use SANDBOX credentials directly
# (Production token is in .env for WebSocket streaming)
ACCESS_TOKEN = "XFE6d2z7hJnleNbpQ789otJmvW3z"  # Sandbox Token
ACCOUNT_ID = "VA13978285"  # Tradier Sandbox Account ID

print(f"🔑 Using SANDBOX credentials:")
print(f"   Account ID: {ACCOUNT_ID}")
print(f"   Token: {ACCESS_TOKEN[:10]}...")

BASE_URL = "https://sandbox.tradier.com/v1"

def test_multileg_order():
    print("\n🔬 STARTING TRADIER DIRECT DIAGNOSTIC")
    print("=" * 60)

    # 1. Calculate Valid Date (Next Friday)
    today = datetime.now()
    days_until_friday = (4 - today.weekday()) % 7
    if days_until_friday == 0: 
        days_until_friday = 7
    expiry_date = today + timedelta(days=days_until_friday)
    
    expiry_iso = expiry_date.strftime('%Y-%m-%d')
    expiry_sym = expiry_date.strftime('%y%m%d')
    
    # 2. Construct Legs (SPY ~580)
    # Using 400/395 PUTs (Deep OTM)
    strike_short = 400
    strike_long = 395
    
    sym_short = f"SPY{expiry_sym}P{int(strike_short*1000):08d}"
    sym_long = f"SPY{expiry_sym}P{int(strike_long*1000):08d}"
    
    print(f"🎯 Target: SPY Credit Spread ({expiry_iso})")
    print(f"   Short: {sym_short}")
    print(f"   Long:  {sym_long}")

    # 3. Prepare Payload
    # Note: 'requests' library handles form-encoded data properly
    # Tradier expects explicit keys: 'option_symbol[0]', 'option_symbol[1]', etc.
    
    payload = {
        'class': 'multileg',
        'symbol': 'SPY',
        'type': 'credit',  # Testing 'credit' first (OPEN = credit spread)
        'duration': 'day',
        'price': '0.50',
        'option_symbol[0]': sym_short,
        'side[0]': 'sell_to_open',
        'quantity[0]': '1',
        'option_symbol[1]': sym_long,
        'side[1]': 'buy_to_open',
        'quantity[1]': '1'
    }

    headers = {
        'Authorization': f"Bearer {ACCESS_TOKEN}",
        'Accept': 'application/json'
    }

    print("\n📦 Sending Direct Request to Tradier Sandbox...")
    print("Payload:")
    print(json.dumps(payload, indent=2))
    print("-" * 60)

    # First, let's try to verify the option symbols exist
    print("\n🔍 Step 1: Verifying option symbols...")
    try:
        quote_response = requests.get(
            f"{BASE_URL}/markets/quotes",
            params={'symbols': f"{sym_short},{sym_long}"},
            headers=headers
        )
        if quote_response.status_code == 200:
            quotes = quote_response.json()
            print(f"   Quotes response: {json.dumps(quotes, indent=2)[:200]}...")
        else:
            print(f"   ⚠️  Quote lookup failed: {quote_response.status_code} - {quote_response.text[:200]}")
    except Exception as e:
        print(f"   ⚠️  Quote lookup error: {e}")

    print("\n🔍 Step 2: Testing multileg order...")
    try:
        response = requests.post(
            f"{BASE_URL}/accounts/{ACCOUNT_ID}/orders",
            data=payload,  # 'data' sends as application/x-www-form-urlencoded
            headers=headers
        )
        
        print(f"\n📨 Response Code: {response.status_code}")
        print(f"📄 Response Body:")
        try:
            response_json = response.json()
            print(json.dumps(response_json, indent=2))
        except:
            print(response.text)
        
        if response.status_code == 200:
            print("\n✅ SUCCESS! The payload format is correct.")
            print("   This means the issue is in the Cloudflare Worker code.")
        elif response.status_code == 500:
            print("\n❌ 500 ERROR: Tradier Sandbox backend crashed.")
            print("   This is the SAME error we see from Cloudflare Worker.")
            print("   This means the issue is NOT in our Worker code!")
            print("\n   Possible causes:")
            print("   - Invalid option symbol format/expiry")
            print("   - Option symbols don't exist in Sandbox")
            print("   - Tradier Sandbox limitations with multileg orders")
            print("   - Market closed or symbol unavailable")
            print("\n   💡 Since Python requests also fails, our URLSearchParams encoding is correct.")
        elif response.status_code == 400:
            print("\n⚠️ 400 ERROR: Bad Request (Invalid parameters).")
            print("   Check the error message above for details.")
        else:
            print(f"\n⚠️ Unexpected status code: {response.status_code}")

    except requests.exceptions.RequestException as e:
        print(f"\n❌ CONNECTION ERROR: {e}")
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")

if __name__ == "__main__":
    test_multileg_order()
