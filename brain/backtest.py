"""
Backtester: Time Machine for Gekko3
Replays historical data through AlphaEngine to test strategies
"""

import pandas as pd
import asyncio
import json
from datetime import datetime, timedelta
import requests
import os
from dotenv import load_dotenv

from src.alpha_engine import AlphaEngine
from src.notifier import get_notifier

# Load environment variables
load_dotenv()

TRADIER_API_BASE = "https://api.tradier.com/v1"


class MockGatekeeper:
    """Mock Gatekeeper that logs trades instead of executing them"""
    
    def __init__(self):
        self.trades = []
        self.notifier = get_notifier()
    
    async def send_proposal(self, proposal):
        """Log the trade proposal instead of sending to real Gatekeeper"""
        trade_record = {
            'timestamp': datetime.now().isoformat(),
            'symbol': proposal['symbol'],
            'strategy': proposal['strategy'],
            'side': proposal['side'],
            'price': proposal['price'],
            'legs': proposal.get('legs', []),
            'context': proposal.get('context', {})
        }
        self.trades.append(trade_record)
        
        print(f"💰 [BACKTEST TRADE] {proposal['side']} {proposal['symbol']} "
              f"{proposal['strategy']} @ ${proposal['price']:.2f}")
        print(f"   Context: VIX={proposal.get('context', {}).get('vix', 0):.1f}, "
              f"RSI={proposal.get('context', {}).get('rsi', 0):.1f}, "
              f"Trend={proposal.get('context', {}).get('trend_state', 'unknown')}")
    
    def get_trade_summary(self):
        """Return summary of all trades"""
        if not self.trades:
            return "No trades executed"
        
        total_trades = len(self.trades)
        by_strategy = {}
        for trade in self.trades:
            strat = trade['strategy']
            by_strategy[strat] = by_strategy.get(strat, 0) + 1
        
        summary = f"\n📊 BACKTEST SUMMARY\n"
        summary += f"Total Trades: {total_trades}\n"
        summary += f"By Strategy: {by_strategy}\n"
        return summary


async def fetch_historical_data(symbol: str, days: int = 20) -> pd.DataFrame:
    """
    Fetch historical 1-minute candle data from Tradier
    """
    access_token = os.getenv('TRADIER_ACCESS_TOKEN', '')
    if not access_token:
        raise ValueError('TRADIER_ACCESS_TOKEN must be set in .env')
    
    headers = {'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'}
    
    # Tradier timesales limit is ~20 days for 1min data
    safe_days = min(days, 20)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=safe_days)
    
    print(f"📥 Fetching {safe_days} days of 1-min data for {symbol} (Tradier Limit)...")
    
    url = f'{TRADIER_API_BASE}/markets/timesales'
    params = {
        'symbol': symbol,
        'interval': '1min',
        'start': start_date.strftime('%Y-%m-%d %H:%M'),
        'end': end_date.strftime('%Y-%m-%d %H:%M'),
        'session_filter': 'all'
    }
    
    try:
        # Use synchronous requests to avoid any async complications in a simple script
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        
        if resp.status_code == 200:
            data = resp.json()
            series = data.get('series', {}).get('data', [])
            
            if not series:
                print("⚠️  No data returned. Market might be closed or symbol invalid.")
                return create_placeholder_data(symbol, days)
            
            # Robust data cleaning to prevent "Duplicate Keys" error
            clean_data = []
            for row in series:
                # Prioritize ISO time string, fallback to unix timestamp
                ts_val = row.get('time')
                if ts_val:
                    ts = pd.to_datetime(ts_val)
                elif row.get('timestamp'):
                    ts = pd.to_datetime(row.get('timestamp'), unit='s')
                else:
                    continue 
                
                clean_data.append({
                    'timestamp': ts,
                    'open': float(row.get('open', 0)),
                    'high': float(row.get('high', 0)),
                    'low': float(row.get('low', 0)),
                    'close': float(row.get('close', 0)),
                    'volume': int(row.get('volume', 0))
                })
            
            df = pd.DataFrame(clean_data)
            if df.empty:
                return create_placeholder_data(symbol, days)
                
            df = df.sort_values('timestamp')
            print(f"✅ Loaded {len(df)} candles from Tradier Production API")
            return df
        else:
            print(f"⚠️  API Error {resp.status_code}: {resp.text[:200]}")
            return create_placeholder_data(symbol, days)
    except Exception as e:
        print(f"⚠️  Connection Error: {e}")
        return create_placeholder_data(symbol, days)


def create_placeholder_data(symbol: str, days: int) -> pd.DataFrame:
    """Create placeholder data for testing when API is unavailable"""
    print("📝 Creating placeholder data for testing...")
    dates = pd.date_range(
        start=datetime.now() - timedelta(days=days),
        end=datetime.now(),
        freq='1min'
    )
    import numpy as np
    np.random.seed(42)
    base_price = 450.0 
    returns = np.random.normal(0, 0.001, len(dates))
    prices = base_price * (1 + returns).cumprod()
    
    df = pd.DataFrame({
        'timestamp': dates,
        'open': prices,
        'high': prices * 1.001,
        'low': prices * 0.999,
        'close': prices,
        'volume': np.random.randint(1000000, 5000000, len(dates))
    })
    return df


async def run_backtest(symbol: str = 'SPY', days: int = 20):
    print(f"\n{'='*60}\n🧪 GEKKO3 BACKTESTER\n{'='*60}")
    
    # Initialize components
    engine = AlphaEngine(lookback_minutes=400) 
    gatekeeper = MockGatekeeper()
    
    df = await fetch_historical_data(symbol, days)
    if df.empty: return
    
    print(f"\n▶️  Starting replay of {len(df)} candles...\n")
    
    signal_count = 0
    warmup_complete_at = None
    
    # Signal deduplication (match production logic)
    last_proposal_time = {}  # {symbol: datetime}
    last_signals = {}  # {symbol: {'signal': str, 'timestamp': datetime}}
    min_proposal_interval = timedelta(minutes=1)  # Minimum time between proposals
    
    # Track daily signals to prevent duplicates
    daily_signals = {}  # {date: {strategy: bool}} to track if strategy fired today
    
    for idx, row in df.iterrows():
        timestamp = pd.to_datetime(row['timestamp'])
        
        # 1. Feed Engine (Simulate Real-Time)
        engine.update(symbol, float(row['close']), int(row['volume']), timestamp=timestamp)
        
        # Simulate VIX update (since we don't have historical VIX in this feed)
        if idx % 100 == 0: engine.set_vix(20.0, timestamp) # Safe/Neutral VIX
        
        # 2. Check Signals
        indicators = engine.get_indicators(symbol)
        is_warm = indicators.get('is_warm', False)
        
        if is_warm and warmup_complete_at is None:
            warmup_complete_at = idx
            print(f"✅ Warmup complete at {timestamp}")

        # --- STRATEGY LOGIC (Mirrors market_feed.py) ---
        
        # A. ORB Strategy (Runs BEFORE Warmup)
        # Window: 10:00 - 11:30 AM
        if timestamp.hour == 10 or (timestamp.hour == 11 and timestamp.minute < 30):
            orb = engine.get_opening_range(symbol)
            if orb['complete']:
                price = indicators['price']
                if price > orb['high']:
                    signal_count += 1
                    print(f"🎯 [ORB BULL] Breakout > {orb['high']:.2f} at {timestamp}")
                elif price < orb['low']:
                    signal_count += 1
                    print(f"🎯 [ORB BEAR] Breakout < {orb['low']:.2f} at {timestamp}")

        # B. Range Farmer (Iron Condor)
        # Trigger: 1:00 PM if ADX < 20
        if timestamp.hour == 13 and 0 <= timestamp.minute < 5:
            adx = engine.get_adx(symbol)
            if adx < 20:
                signal_count += 1
                print(f"🚜 [FARMER] Iron Condor Setup (ADX {adx:.1f}) at {timestamp}")

        # C. Scalper (All Day)
        # Trigger: RSI(2) Extreme
        rsi_2 = engine.get_rsi(symbol, period=2)
        if rsi_2 is not None:
            if rsi_2 < 5:
                signal_count += 1
                print(f"⚡ [SCALP BULL] RSI(2) {rsi_2:.1f} (Oversold) at {timestamp}")
            elif rsi_2 > 95:
                signal_count += 1
                print(f"⚡ [SCALP BEAR] RSI(2) {rsi_2:.1f} (Overbought) at {timestamp}")

        # D. Trend Strategy (Requires Warmup)
        if is_warm:
            trend = indicators['trend']
            rsi = indicators['rsi']
            flow = indicators['flow_state']
            
            if trend == 'UPTREND' and rsi < 30 and flow != 'NEUTRAL':
                signal_count += 1
                print(f"📈 [TREND BULL] Dip Buy in Uptrend at {timestamp}")
            elif trend == 'DOWNTREND' and rsi > 70 and flow != 'NEUTRAL':
                signal_count += 1
                print(f"📉 [TREND BEAR] Rip Sell in Downtrend at {timestamp}")

        # Progress Log
        if (idx + 1) % 5000 == 0:
            print(f"📊 Progress: {(idx+1)/len(df)*100:.1f}%")

    print(f"\n{'='*60}")
    print(f"Total Signals Detected: {signal_count}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'SPY'
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 10 # Default to 10 days safe limit
    asyncio.run(run_backtest(symbol, days))
