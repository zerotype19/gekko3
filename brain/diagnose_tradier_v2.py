import requests
import os
import json
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Load secrets
load_dotenv()
# For this diagnostic, we use SANDBOX credentials directly
ACCESS_TOKEN = "XFE6d2z7hJnleNbpQ789otJmvW3z"  # Sandbox Token
ACCOUNT_ID = "VA13978285"  # Tradier Sandbox Account ID
BASE_URL = "https://sandbox.tradier.com/v1"

HEADERS = {
    'Authorization': f"Bearer {ACCESS_TOKEN}",
    'Accept': 'application/json'
}

def get_expiry(days_out=7):
    # Find next Friday
    today = datetime.now()
    days = (4 - today.weekday()) % 7
    if days == 0: 
        days = 7
    target = today + timedelta(days=days + days_out)  # Offset by weeks if needed
    return target

def fmt_symbol(symbol, date, strike, type='P'):
    d_str = date.strftime('%y%m%d')
    s_str = f"{int(strike*1000):08d}"
    return f"{symbol}{d_str}{type}{s_str}"

def send_order(name, payload):
    print(f"\n🧪 TEST: {name}")
    print(f"   Payload: {json.dumps(payload, indent=2)}")
    try:
        r = requests.post(
            f"{BASE_URL}/accounts/{ACCOUNT_ID}/orders",
            data=payload,
            headers=HEADERS
        )
        print(f"   👉 Status: {r.status_code}")
        try:
            response_json = r.json()
            print(f"   👉 Response: {json.dumps(response_json, indent=2)}")
        except:
            print(f"   👉 Response: {r.text}")
    except Exception as e:
        print(f"   ❌ Exception: {e}")

def run_diagnostics():
    print("🏥 TRADIER SANDBOX DIAGNOSTIC V2")
    print("=================================")

    # 1. SETUP SYMBOLS
    # LEAP (Far dated - 2026)
    date_leap = datetime(2026, 1, 16)
    # NEAR (Next Friday)
    date_near = get_expiry(0)
    
    print(f"📅 Near Date: {date_near.strftime('%Y-%m-%d')}")
    print(f"📅 Leap Date: {date_leap.strftime('%Y-%m-%d')}")

    # 2. TEST 1: SINGLE LEG (Control)
    # Should work if account is active
    s_near = fmt_symbol("SPY", date_near, 400, 'P')
    payload_1 = {
        'class': 'option',
        'symbol': 'SPY',
        'option_symbol': s_near,
        'side': 'buy_to_open',
        'quantity': '1',
        'type': 'market',
        'duration': 'day'
    }
    send_order("Single Leg Market (Near Term)", payload_1)

    # 3. TEST 2: MULTILEG MARKET (Near Term)
    # Removes 'price'/limit logic to test structure
    s_near_long = fmt_symbol("SPY", date_near, 395, 'P')
    payload_2 = {
        'class': 'multileg',
        'symbol': 'SPY',
        'type': 'market',  # Just execute, don't check price
        'duration': 'day',
        'option_symbol[0]': s_near,
        'side[0]': 'sell_to_open',
        'quantity[0]': '1',
        'option_symbol[1]': s_near_long,
        'side[1]': 'buy_to_open',
        'quantity[1]': '1'
    }
    send_order("Multileg MARKET (Near Term)", payload_2)

    # 4. TEST 3: MULTILEG CREDIT (Near Term)
    # Adds price logic back in
    payload_3 = payload_2.copy()
    payload_3['type'] = 'credit'
    payload_3['price'] = '0.01'  # Very low credit to ensure it passes
    send_order("Multileg CREDIT (Near Term)", payload_3)

    # 5. TEST 4: MULTILEG CREDIT (LEAP 2026)
    # The one that failed before
    s_leap_short = fmt_symbol("SPY", date_leap, 400, 'P')
    s_leap_long = fmt_symbol("SPY", date_leap, 395, 'P')
    payload_4 = {
        'class': 'multileg',
        'symbol': 'SPY',
        'type': 'credit',
        'duration': 'day',
        'price': '0.50',
        'option_symbol[0]': s_leap_short,
        'side[0]': 'sell_to_open',
        'quantity[0]': '1',
        'option_symbol[1]': s_leap_long,
        'side[1]': 'buy_to_open',
        'quantity[1]': '1'
    }
    send_order("Multileg CREDIT (LEAP 2026)", payload_4)

    print("\n" + "=" * 60)
    print("📊 DIAGNOSTIC SUMMARY")
    print("=" * 60)
    print("\nInterpretation:")
    print("✅ If Test 2 (Multileg Market) passes but Test 4 (LEAP) fails:")
    print("   → Issue is DATA AVAILABILITY for 2026 options in Sandbox")
    print("   → Fix: Use nearer expirations for testing")
    print("\n❌ If All Multileg Tests fail (500):")
    print("   → Sandbox account might not support multileg orders")
    print("   → Fix: Test in Production OR assume code is correct")
    print("\n⚠️  If Test 3 (Credit) fails but Test 2 (Market) passes:")
    print("   → Issue is the CREDIT TYPE validation")
    print("   → Fix: Use 'market' orders for Sandbox testing only")

if __name__ == "__main__":
    run_diagnostics()
