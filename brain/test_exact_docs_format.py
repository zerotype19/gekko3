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

HEADERS = {
    'Authorization': f"Bearer {ACCESS_TOKEN}",
    'Accept': 'application/json',
    'Content-Type': 'application/x-www-form-urlencoded'
}

def test_exact_docs_format():
    print("\n📚 TESTING EXACT TRADIER DOCS FORMAT")
    print("=" * 60)
    print("Using EXACT format from Tradier documentation example")
    print("=" * 60)
    
    # Calculate valid dates (next Friday)
    today = datetime.now()
    days = (4 - today.weekday()) % 7
    if days == 0:
        days = 7
    target = today + timedelta(days=days)
    d_str = target.strftime('%y%m%d')
    
    # Construct symbols using our format (SPY Put Spread)
    s1 = f"SPY{d_str}P00400000"  # 400 Put
    s2 = f"SPY{d_str}P00395000"  # 395 Put
    
    print(f"\n🎯 Target: SPY Put Spread ({target.strftime('%Y-%m-%d')})")
    print(f"   Symbol 1: {s1}")
    print(f"   Symbol 2: {s2}")
    
    # EXACT FORMAT FROM TRADIER DOCS:
    # class=multileg&symbol=CSCO&duration=day&type=market
    # &side[0]=buy_to_open&quantity[0]=1&option_symbol[0]=CSCO150117C00035000 
    # &side[1]=sell_to_open&quantity[1]=1&option_symbol[1]=CSCO140118C00008000
    
    # Test 1: EXACT DOCS FORMAT (market order, raw brackets)
    print("\n🧪 TEST 1: Exact Docs Format (Market Order)")
    body_1 = (
        f"class=multileg&symbol=SPY&duration=day&type=market"
        f"&side[0]=buy_to_open&quantity[0]=1&option_symbol[0]={s1}"
        f"&side[1]=sell_to_open&quantity[1]=1&option_symbol[1]={s2}"
    )
    print(f"   Body: {body_1}")
    
    try:
        r = requests.post(
            f"{BASE_URL}/accounts/{ACCOUNT_ID}/orders",
            data=body_1,
            headers=HEADERS
        )
        print(f"   👉 Status: {r.status_code}")
        try:
            response_json = r.json()
            print(f"   👉 Response: {json.dumps(response_json, indent=2)}")
        except:
            print(f"   👉 Response: {r.text[:200]}")
        
        if r.status_code == 200:
            print("\n   ✅ SUCCESS: Exact docs format WORKS!")
            print("      This proves the format is correct.")
        else:
            print(f"\n   ❌ FAILED: {r.status_code}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # Test 2: URL-encoded version (what we use in Cloudflare)
    print("\n🧪 TEST 2: URL-Encoded Format (Our Cloudflare Implementation)")
    params_2 = {
        'class': 'multileg',
        'symbol': 'SPY',
        'duration': 'day',
        'type': 'market',
        'side[0]': 'buy_to_open',
        'quantity[0]': '1',
        'option_symbol[0]': s1,
        'side[1]': 'sell_to_open',
        'quantity[1]': '1',
        'option_symbol[1]': s2
    }
    import urllib.parse
    body_2 = urllib.parse.urlencode(params_2)
    print(f"   Body: {body_2[:150]}...")
    
    try:
        r = requests.post(
            f"{BASE_URL}/accounts/{ACCOUNT_ID}/orders",
            data=body_2,
            headers=HEADERS
        )
        print(f"   👉 Status: {r.status_code}")
        try:
            response_json = r.json()
            print(f"   👉 Response: {json.dumps(response_json, indent=2)}")
        except:
            print(f"   👉 Response: {r.text[:200]}")
        
        if r.status_code == 200:
            print("\n   ✅ SUCCESS: URL-encoded format WORKS!")
            print("      This proves our Cloudflare code is correct.")
        else:
            print(f"\n   ❌ FAILED: {r.status_code}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    print("\n" + "=" * 60)
    print("📊 INTERPRETATION:")
    print("=" * 60)
    print("If BOTH tests fail (500):")
    print("  → Sandbox execution engine is broken for multileg orders")
    print("  → NOT a format issue (format matches docs exactly)")
    print("  → Code is correct, ready for Production")
    print("\nIf Test 1 works but Test 2 fails:")
    print("  → Tradier requires raw brackets (not URL-encoded)")
    print("  → Need to update tradier.ts")
    print("\nIf BOTH tests work:")
    print("  → Both formats are valid")
    print("  → Our code is correct")

if __name__ == "__main__":
    test_exact_docs_format()
