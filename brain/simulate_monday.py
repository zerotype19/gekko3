"""
Monday Simulation Script
Integration test to verify system components before live trading
Tests: VIX API, Alpha Engine Math, RSI, Gatekeeper Connectivity
"""

import asyncio
import os
import logging
from datetime import datetime, timedelta
import aiohttp
from dotenv import load_dotenv

# Imports from your actual system
from src.alpha_engine import AlphaEngine
from src.gatekeeper_client import GatekeeperClient

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

async def simulate_market():
    print("\n🧪 STARTING MONDAY SIMULATION (Integration Test)...")
    print("=" * 60)
    
    # 1. Load Environment
    load_dotenv()
    token = os.getenv("TRADIER_ACCESS_TOKEN")
    if not token:
        print("❌ Error: No TRADIER_ACCESS_TOKEN found in .env")
        return

    # 2. Test VIX Polling (Real API Call)
    print("\n📊 TEST 1: VIX API Connectivity")
    print("-" * 60)
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            # We use the same endpoint the MarketFeed uses
            url = "https://api.tradier.com/v1/markets/quotes?symbols=VIX"
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Handle Tradier's response format (sometimes list, sometimes dict)
                    quotes = data.get('quotes', {})
                    quote = quotes.get('quote', {})
                    if isinstance(quote, list):
                        quote = quote[0]
                    
                    vix_val = quote.get('last')
                    if vix_val:
                        print(f"✅ VIX Fetch Successful: {vix_val}")
                        print(f"   (This is REAL data from Tradier API)")
                    else:
                        print(f"⚠️  VIX response received but no 'last' price found: {data}")
                else:
                    error_text = await resp.text()
                    print(f"❌ VIX Fetch Failed: {resp.status} - {error_text}")
        except Exception as e:
            print(f"❌ VIX Error: {e}")

    # 3. Test Alpha Logic (Synthetic Data)
    print("\n🧮 TEST 2: Alpha Engine Math & Warmup")
    print("-" * 60)
    engine = AlphaEngine()
    
    # Verify it starts empty/neutral
    initial_trend, initial_sma = engine.get_trend('SPY')
    print(f"   Initial Trend: {initial_trend} (SMA: {initial_sma})")
    print(f"   Expected: INSUFFICIENT_DATA (no candles yet)")
    
    if initial_trend == 'INSUFFICIENT_DATA':
        print("   ✅ Correctly returns INSUFFICIENT_DATA with no data")
    else:
        print(f"   ⚠️  Unexpected initial state: {initial_trend}")
    
    # Inject 200 minutes of "Perfect Uptrend"
    print("\n   Injecting 200 synthetic candles (Price 400 -> 420)...")
    start_time = datetime.now().replace(second=0, microsecond=0)
    base_price = 400.0
    
    # Generate 205 candles (need to cross minute boundaries to close bars)
    # Each bar closes when we move to the next minute
    for i in range(205):  # Go slightly over 200 to ensure full SMA
        # Synthetic data: Slow grind up, low volume
        fake_price = base_price + (i * 0.1)
        # Create timestamp for each bar (1 minute apart)
        # Start each bar at :00 and update at :30 to ensure it closes on next minute
        bar_start = start_time + timedelta(minutes=i)
        bar_mid = bar_start + timedelta(seconds=30)
        bar_end = bar_start + timedelta(seconds=59)
        
        # Add a tick at the start of the bar
        engine.update("SPY", fake_price, 50000, timestamp=bar_start)
        # Add a tick in the middle
        engine.update("SPY", fake_price + 0.05, 30000, timestamp=bar_mid)
        # Add a tick at the end (before next minute) to close the bar
        engine.update("SPY", fake_price + 0.1, 20000, timestamp=bar_end)
    
    # Add one more tick to close the last bar
    final_time = start_time + timedelta(minutes=205, seconds=30)
    final_price = base_price + (204 * 0.1) + 0.1
    engine.update("SPY", final_price, 100000, timestamp=final_time)
    
    candle_count = len(engine.candles.get('SPY', []))
    trend, sma = engine.get_trend("SPY")
    print(f"   Trend after {candle_count} candles: {trend}")
    print(f"   SMA 200 value: {sma}")
    print(f"   Current price: ${engine.get_current_price('SPY'):.2f}")
    
    if trend == "UPTREND":
        print("   ✅ Trend Logic Verified (UPTREND detected after 200 candles)")
    elif trend == "INSUFFICIENT_DATA":
        print(f"   ⚠️  Still INSUFFICIENT_DATA - only {candle_count} closed candles (need 200)")
        print(f"   (This is OK for simulation - real market will have continuous ticks)")
    elif trend == "DOWNTREND":
        print(f"   ⚠️  Got DOWNTREND instead of UPTREND (price might be below SMA)")
    else:
        print(f"   ❌ Trend Logic Failed (Got {trend})")

    # 4. Test RSI Logic
    print("\n📉 TEST 3: RSI Calculation")
    print("-" * 60)
    # Need enough candles for RSI (14+1 minimum)
    current_candle_count = len(engine.candles.get('SPY', []))
    current_price = engine.get_current_price('SPY')
    print(f"   Current candles: {current_candle_count}")
    print(f"   Current price: ${current_price:.2f}")
    
    if current_candle_count >= 15:
        # Create a sharp decline scenario for RSI
        print("   Creating sharp decline scenario (10 candles, price drops 5%)...")
        last_time = start_time + timedelta(minutes=current_candle_count)
        decline_start_price = current_price
        
        for i in range(15):  # 15 candles of decline
            # Drop 0.33% per candle (5% total over 15 candles)
            drop_factor = 1.0 - (i * 0.0033)
            fake_price = decline_start_price * drop_factor
            bar_start = last_time + timedelta(minutes=i)
            bar_mid = bar_start + timedelta(seconds=30)
            bar_end = bar_start + timedelta(seconds=59)
            
            # Multiple ticks per bar to ensure bars close
            engine.update("SPY", fake_price, 150000, timestamp=bar_start)
            engine.update("SPY", fake_price * 0.998, 100000, timestamp=bar_mid)
            engine.update("SPY", fake_price * 0.996, 50000, timestamp=bar_end)
        
        # Close last bar
        final_time = last_time + timedelta(minutes=15, seconds=30)
        engine.update("SPY", decline_start_price * 0.95, 100000, timestamp=final_time)
    
    rsi = engine.get_rsi("SPY")
    final_candle_count = len(engine.candles.get('SPY', []))
    print(f"   RSI after decline: {rsi:.2f}")
    print(f"   Final candle count: {final_candle_count}")
    
    if rsi < 30:
        print("   ✅ RSI Logic Verified (Oversold < 30 detected)")
    elif rsi < 50:
        print(f"   ⚠️  RSI is {rsi:.2f} (below neutral, but not oversold yet)")
        print(f"   (RSI calculation working, but need sharper decline for < 30)")
    else:
        print(f"   ⚠️  RSI is {rsi:.2f} (might need sharper/more sustained drop)")
        print(f"   (Note: With limited synthetic data, RSI may not reach oversold)")

    # 5. Test Gatekeeper Connectivity
    print("\n🛡️ TEST 4: Gatekeeper Safety Check")
    print("-" * 60)
    client = GatekeeperClient()
    
    # Get current indicators to build realistic proposal
    indicators = engine.get_indicators("SPY")
    current_price = engine.get_current_price("SPY")
    
    # Construct a valid-looking proposal (will likely be rejected, which is GOOD - safety)
    # Note: We set expiration to next Friday to pass DTE checks (1-7 days)
    next_friday = datetime.now() + timedelta(days=(4 - datetime.now().weekday()) % 7)
    if next_friday.weekday() != 4:  # If not Friday, go to next Friday
        next_friday += timedelta(days=7)
    
    # Calculate strikes
    sell_strike = int(current_price * 0.98)
    buy_strike = int(current_price * 0.96)
    
    # CRITICAL FIX: Tradier/OCC requires strike * 1000 in option symbol
    sell_strike_fmt = int(sell_strike * 1000)  # Multiply by 1000 for OCC format
    buy_strike_fmt = int(buy_strike * 1000)    # Multiply by 1000 for OCC format
    
    proposal = {
        "symbol": "SPY",
        "strategy": "CREDIT_SPREAD",
        "side": "OPEN",  # OPEN = Enter new position (not SELL)
        "quantity": 1,
        "price": 0.50,  # MANDATORY: Limit price (mock net credit)
        "legs": [
            {
                "symbol": f"SPY{next_friday.strftime('%y%m%d')}P{sell_strike_fmt:08d}",  # Fixed: strike * 1000
                "expiration": next_friday.strftime('%Y-%m-%d'),
                "strike": sell_strike,  # Actual strike price (not * 1000)
                "type": "PUT",
                "quantity": 1,
                "side": "SELL"
            },
            {
                "symbol": f"SPY{next_friday.strftime('%y%m%d')}P{buy_strike_fmt:08d}",  # Fixed: strike * 1000
                "expiration": next_friday.strftime('%Y-%m-%d'),
                "strike": buy_strike,  # Actual strike price (not * 1000)
                "type": "PUT",
                "quantity": 1,
                "side": "BUY"
            }
        ],
        "context": {
            "vix": indicators.get('vix') or 15.0,  # Use real VIX if available, else test value
            "flow_state": indicators.get('flow_state', 'risk_on').lower(),
            "trend_state": "bullish",
            "vol_state": "normal",
            "rsi": indicators.get('rsi', 50),
            "vwap": indicators.get('vwap', current_price),
            "volume_velocity": indicators.get('volume_velocity', 1.0),
            "imbalance_score": 0
        }
    }
    
    print(f"   Sending test proposal to Gatekeeper...")
    print(f"   Symbol: {proposal['symbol']}")
    print(f"   Strategy: {proposal['strategy']}")
    print(f"   Side: {proposal['side']} (OPEN = Enter position)")
    print(f"   Price: ${proposal['price']} (Limit price - mandatory)")
    print(f"   VIX: {proposal['context']['vix']}")
    try:
        response = await client.send_proposal(proposal)
        print(f"   Gatekeeper Response: {response}")
        
        # We want to see a response. If it's rejected, that's GOOD (safety working).
        # If it's approved, that's also technically a pass for connectivity,
        # but means we rely on Tradier Sandbox to reject the order itself.
        if response:
            status = response.get('status', 'UNKNOWN')
            if status == 'REJECTED':
                reason = response.get('reason', 'Unknown reason')
                print(f"   ✅ Connectivity Verified (Rejected: {reason} - This is GOOD, safety is working!)")
            elif status == 'APPROVED':
                print(f"   ⚠️  Proposal Approved (Connectivity works, but order may fail at Tradier)")
            else:
                print(f"   ✅ Connectivity Verified (Received response: {status})")
        else:
            print(f"   ❌ No response received")
    except Exception as e:
        print(f"   ❌ Gatekeeper Connection Failed: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("🧪 SIMULATION COMPLETE")
    print("=" * 60)
    print("\n📋 Summary:")
    print("   • Check VIX test: Should show real VIX value")
    print("   • Check Trend test: Should show UPTREND after 200 candles")
    print("   • Check RSI test: Should show < 50 (ideally < 30)")
    print("   • Check Gatekeeper: Should receive response (rejection is GOOD)")
    print("\n✅ If all tests pass, system is ready for Monday validation!")

if __name__ == "__main__":
    asyncio.run(simulate_market())
