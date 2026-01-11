"""
Live Order Test Script
Tests the complete pipeline: Brain → Gatekeeper → Tradier Sandbox
"""

import asyncio
import logging
import json
from datetime import datetime, timedelta
from src.gatekeeper_client import GatekeeperClient
from dotenv import load_dotenv

# Setup
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger()

async def test_live_order():
    print("\n🚀 STARTING LIVE ORDER TEST (Sandbox)...")
    print("=" * 60)
    
    client = GatekeeperClient()
    
    # 1. Calculate Valid Dates (Next Friday)
    today = datetime.now()
    days_until_friday = (4 - today.weekday()) % 7
    if days_until_friday == 0:
        days_until_friday = 7  # If today is Friday, go to next Friday
    expiry_date = today + timedelta(days=days_until_friday)
    
    # Formats
    expiry_str_iso = expiry_date.strftime('%Y-%m-%d')
    expiry_str_sym = expiry_date.strftime('%y%m%d')  # YYMMDD
    
    # 2. Mock Strikes (Use far OTM to be safe/realistic)
    # SPY is around 580, using 400/395 PUTs (far OTM)
    strike_short = 400
    strike_long = 395
    
    # CRITICAL: OCC Symbol Format - Strike * 1000, padded to 8 chars
    # Example: 400 -> 400 * 1000 -> 400000 -> 00400000
    fmt_short = f"{int(strike_short * 1000):08d}"
    fmt_long = f"{int(strike_long * 1000):08d}"
    
    symbol_short = f"SPY{expiry_str_sym}P{fmt_short}"
    symbol_long = f"SPY{expiry_str_sym}P{fmt_long}"

    print(f"📅 Expiry Date: {expiry_str_iso}")
    print(f"🎫 Short Leg: {symbol_short} (Strike: {strike_short})")
    print(f"🎫 Long Leg:  {symbol_long} (Strike: {strike_long})")
    print()

    # 3. Construct Proposal (Phase 5 Standard Format)
    proposal = {
        "symbol": "SPY",
        "strategy": "CREDIT_SPREAD",
        "side": "OPEN",  # Critical: OPEN for new position
        "quantity": 1,
        "price": 0.50,  # Critical: Mandatory limit price
        "legs": [
            {
                "symbol": symbol_short,
                "expiration": expiry_str_iso,
                "strike": strike_short,
                "type": "PUT",
                "quantity": 1,
                "side": "SELL"  # Short leg (sell to open)
            },
            {
                "symbol": symbol_long,
                "expiration": expiry_str_iso,
                "strike": strike_long,
                "type": "PUT",
                "quantity": 1,
                "side": "BUY"  # Long leg (buy to open)
            }
        ],
        "context": {
            "vix": 15.0,
            "flow_state": "risk_on",
            "trend_state": "bullish",
            "rsi": 25.0
        }
    }

    print(f"📦 Sending Proposal...")
    print(f"   Symbol: {proposal['symbol']}")
    print(f"   Strategy: {proposal['strategy']}")
    print(f"   Side: {proposal['side']}")
    print(f"   Price: ${proposal['price']} (Limit)")
    print()
    
    try:
        # 4. Fire!
        response = await client.send_proposal(proposal)
        
        print("\n📨 GATEKEEPER RESPONSE:")
        print(json.dumps(response, indent=2))
        print()
        
        # 5. Analyze Result
        status = response.get('status')
        
        if status == 'APPROVED':
            order_id = response.get('data', {}).get('order_id') or response.get('order_id')
            print("✅ SUCCESS! Order Placed.")
            print(f"   🆔 Order ID: {order_id}")
            print(f"   📝 Status:   PENDING (Sent to Tradier Sandbox)")
            print()
            print("   💡 Next Steps:")
            print("      1. Check Dashboard: https://gekko3-core.kevin-mcgovern.workers.dev/")
            print("      2. Check Tradier Sandbox: https://sandbox.tradier.com/")
            print("      3. Verify order appears in Recent Activity Log")
        elif status == 'REJECTED':
            reason = response.get('reason') or response.get('data', {}).get('reason')
            print(f"❌ REJECTED: {reason}")
            print()
            print("   💡 This is OK - the pipe is working, Gatekeeper is doing its job.")
            print("      Common reasons:")
            print("      - Market Closed (Sandbox may restrict hours)")
            print("      - Risk limits (position limits, VIX, etc.)")
            print("      - Invalid symbol (if option doesn't exist in Sandbox)")
        else:
            error = response.get('error') or response.get('data', {}).get('error')
            print(f"⚠️  UNEXPECTED STATUS: {status}")
            if error:
                print(f"   Error: {error}")

    except Exception as e:
        print(f"\n❌ CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_live_order())
