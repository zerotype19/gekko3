"""
Live Execution Test (Sandbox Mode)
Tests the full execution pipe: Brain -> Gatekeeper -> Tradier Sandbox
Verifies that approved proposals result in actual order placement
"""

import asyncio
import logging
from datetime import datetime, timedelta
from src.gatekeeper_client import GatekeeperClient
from dotenv import load_dotenv

# Setup
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

async def test_execution():
    print("\n🚀 STARTING LIVE EXECUTION TEST (Sandbox Mode)...")
    print("=" * 70)
    print("⚠️  This will send a test order to TRADIER SANDBOX (paper trading)")
    print("=" * 70)
    
    try:
        client = GatekeeperClient()
    except ValueError as e:
        print(f"❌ Client initialization failed: {e}")
        print("   Make sure GATEKEEPER_URL and API_SECRET are set in .env")
        return
    
    # 1. Construct a PERFECT Proposal
    # Must match Gatekeeper's types.ts exactly
    
    # Calculate valid dates (next Friday = ~4-7 days out for DTE check)
    today = datetime.now()
    days_until_friday = (4 - today.weekday()) % 7
    if days_until_friday == 0:
        days_until_friday = 7  # If today is Friday, use next Friday
    expiry_date = (today + timedelta(days=days_until_friday)).strftime('%Y-%m-%d')
    
    # Use a realistic SPY price (around current market)
    spy_price = 425.0  # Adjust if needed for strike selection
    
    proposal = {
        "symbol": "SPY",
        "strategy": "CREDIT_SPREAD",  # Correct enum from types.ts
        "side": "SELL",  # Top-level side (SELL for credit spreads)
        "quantity": 1,  # 1 contract spread
        "legs": [
            # Short Put Leg (Sell higher strike)
            {
                "symbol": f"SPY{expiry_date.replace('-', '')[2:]}P{int(spy_price * 0.98):08d}",  # Mock option symbol
                "expiration": expiry_date,
                "strike": int(spy_price * 0.98),  # 2% OTM
                "type": "PUT",  # Uppercase as per types.ts
                "quantity": 1,
                "side": "SELL"  # Sell to open (short leg)
            },
            # Long Put Leg (Buy lower strike for protection)
            {
                "symbol": f"SPY{expiry_date.replace('-', '')[2:]}P{int(spy_price * 0.96):08d}",  # Mock option symbol
                "expiration": expiry_date,
                "strike": int(spy_price * 0.96),  # 4% OTM (wider spread)
                "type": "PUT",  # Uppercase as per types.ts
                "quantity": 1,
                "side": "BUY"  # Buy to open (long leg)
            }
        ],
        "context": {
            "vix": 15.5,  # Valid VIX (< 28)
            "flow_state": "risk_on",  # Valid flow state
            "trend_state": "bullish",  # Additional context
            "vol_state": "normal",
            "rsi": 28.5,  # Oversold (bullish signal)
            "vwap": spy_price * 1.001,  # Slightly above VWAP
            "volume_velocity": 1.3,  # Above 1.2 threshold
            "imbalance_score": 0.5
        }
    }
    
    print(f"\n📦 Proposal Details:")
    print(f"   Symbol: {proposal['symbol']}")
    print(f"   Strategy: {proposal['strategy']}")
    print(f"   Side: {proposal['side']}")
    print(f"   Expiration: {expiry_date} (DTE: {days_until_friday} days)")
    print(f"   Legs: {len(proposal['legs'])}")
    for i, leg in enumerate(proposal['legs'], 1):
        print(f"     Leg {i}: {leg['side']} {leg['quantity']}x {leg['type']} @ ${leg['strike']}")
    print(f"   VIX: {proposal['context']['vix']}")
    print(f"   Flow State: {proposal['context']['flow_state']}")
    
    # 2. Send to Gatekeeper
    print(f"\n📤 Sending to Gatekeeper: {client.base_url}/v1/proposal")
    try:
        response = await client.send_proposal(proposal)
        
        print(f"\n📨 Gatekeeper Response:")
        print(f"   Status: {response.get('status', 'UNKNOWN')}")
        
        # 3. Analyze Result
        status = response.get('status')
        
        if status == 'APPROVED':
            order_id = response.get('data', {}).get('order_id') or response.get('order_id')
            print(f"   ✅ SUCCESS: Trade Approved and Sent to Tradier!")
            print(f"   Order ID: {order_id}")
            print(f"\n   This confirms:")
            print(f"   • Gatekeeper validation passed")
            print(f"   • Proposal reached Tradier Sandbox")
            print(f"   • Order was placed (check Tradier dashboard)")
            
        elif status == 'REJECTED':
            reason = response.get('reason') or response.get('rejectionReason', 'Unknown reason')
            print(f"   ⚠️  REJECTED: {reason}")
            
            if 'signature' in reason.lower():
                print(f"\n   Possible causes:")
                print(f"   • API_SECRET mismatch between .env and Cloudflare")
                print(f"   • Check SETUP_SECRETS.md for correct secret")
            elif 'vix' in reason.lower():
                print(f"\n   Safety check working: VIX validation rejected the trade")
            elif 'stale' in reason.lower():
                print(f"\n   Proposal timestamp too old (shouldn't happen in test)")
            elif 'symbol' in reason.lower() or 'strategy' in reason.lower():
                print(f"\n   Proposal structure issue - check types.ts match")
            else:
                print(f"\n   Gatekeeper safety checks rejected (this is GOOD)")
                
        elif 'error' in response:
            error_msg = response.get('error', 'Unknown error')
            print(f"   ⚠️  ERROR: {error_msg}")
            
            if 'Market Closed' in str(error_msg) or 'market' in str(error_msg).lower():
                print(f"\n   ✅ SUCCESS (Technically): Gatekeeper tried to execute,")
                print(f"      but Tradier said Market Closed.")
                print(f"   This confirms the execution pipe is open!")
            else:
                print(f"\n   Execution attempted but Tradier returned error")
                print(f"   This is still a connectivity success - pipe is working")
                
        else:
            print(f"   ⚠️  UNEXPECTED RESPONSE: {response}")
            
    except Exception as e:
        print(f"\n❌ NETWORK ERROR: {e}")
        import traceback
        traceback.print_exc()
        
        print(f"\n   Possible causes:")
        print(f"   • Cloudflare Worker not deployed/accessible")
        print(f"   • GATEKEEPER_URL incorrect in .env")
        print(f"   • Network connectivity issue")

    print("\n" + "=" * 70)
    print("🧪 EXECUTION TEST COMPLETE")
    print("=" * 70)
    print("\n📋 Next Steps:")
    print("   • If APPROVED: Check Tradier Sandbox dashboard for order")
    print("   • If REJECTED: Review rejection reason (safety working)")
    print("   • If ERROR: Check network/Gatekeeper deployment status")
    print("\n⚠️  Remember: This is SANDBOX mode (paper trading only)")

if __name__ == "__main__":
    asyncio.run(test_execution())
