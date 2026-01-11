import requests
import os
import urllib.parse
from dotenv import load_dotenv

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

def send_raw(name, body_str):
    print(f"\n🧪 TEST: {name}")
    print(f"   Body: {body_str[:150]}...")
    try:
        # We use data=body_str to prevent requests from auto-encoding
        r = requests.post(
            f"{BASE_URL}/accounts/{ACCOUNT_ID}/orders",
            data=body_str,
            headers=HEADERS
        )
        print(f"   👉 Status: {r.status_code}")
        try:
            response_json = r.json()
            print(f"   👉 Response: {json.dumps(response_json, indent=2)}")
        except:
            print(f"   👉 Response: {r.text[:200]}")
    except Exception as e:
        print(f"   ❌ Error: {e}")

def run():
    print("🔬 TRADIER ENCODING DEEP DIVE")
    print("=" * 60)
    
    # Common Data
    sym = "SPY"
    # Use the exact symbols from your logs
    s1 = "SPY260116P00400000"
    s2 = "SPY260116P00395000"

    # VARIATION 1: Standard Percent Encoding (What our Cloudflare Worker uses)
    # option_symbol%5B0%5D=...
    params_1 = {
        'class': 'multileg', 'symbol': sym, 'type': 'market', 'duration': 'day',
        'option_symbol[0]': s1, 'side[0]': 'sell_to_open', 'quantity[0]': '1',
        'option_symbol[1]': s2, 'side[1]': 'buy_to_open', 'quantity[1]': '1'
    }
    body_1 = urllib.parse.urlencode(params_1)
    send_raw("1. Standard Percent Encoded (%5B %5D)", body_1)

    # VARIATION 2: Raw Brackets (Non-Standard but sometimes required by old PHP backends)
    # option_symbol[0]=...
    # We manually construct string to keep brackets un-encoded
    body_2 = (
        f"class=multileg&symbol={sym}&type=market&duration=day"
        f"&option_symbol[0]={s1}&side[0]=sell_to_open&quantity[0]=1"
        f"&option_symbol[1]={s2}&side[1]=buy_to_open&quantity[1]=1"
    )
    send_raw("2. Raw Brackets ([ ])", body_2)

    # VARIATION 3: No Indices (Array style [])
    # option_symbol[]=...
    body_3 = (
        f"class=multileg&symbol={sym}&type=market&duration=day"
        f"&option_symbol[]={s1}&side[]=sell_to_open&quantity[]=1"
        f"&option_symbol[]={s2}&side[]=buy_to_open&quantity[]=1"
    )
    send_raw("3. No Indices (Array style [])", body_3)

    print("\n" + "=" * 60)
    print("📊 DECISION MATRIX:")
    print("=" * 60)
    print("If Test 1 works (200 OK):")
    print("  → Our Cloudflare code is PERFECT")
    print("  → Previous 500 errors were 100% Tradier Sandbox")
    print("\nIf Test 2 works (200 OK):")
    print("  → Tradier requires Raw Brackets")
    print("  → Need to update tradier.ts to manually construct string")
    print("\nIf ALL fail (500/400):")
    print("  → Confirms Sandbox execution engine is broken")
    print("  → Not an encoding issue")

if __name__ == "__main__":
    run()
