"""
Backtester: Time Machine for Gekko3
Replays historical data through AlphaEngine to test strategies
"""

import pandas as pd
import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, List
import aiohttp
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
        
        # Optionally send to Discord for backtest results
        try:
            await self.notifier.send_trade_signal(
                f"BACKTEST: {proposal['side']} {proposal['symbol']} "
                f"{proposal['strategy']} @ ${proposal['price']:.2f}"
            )
        except:
            pass  # Don't fail if notification fails
    
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


async def fetch_historical_data(symbol: str, days: int = 30) -> pd.DataFrame:
    """
    Fetch historical 1-minute candle data from Tradier
    
    Note: Tradier's history endpoint may have limitations.
    For production, consider using a dedicated historical data provider.
    """
    access_token = os.getenv('TRADIER_ACCESS_TOKEN', '')
    if not access_token:
        raise ValueError('TRADIER_ACCESS_TOKEN must be set in .env')
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    print(f"📥 Fetching historical data for {symbol} from {start_date.date()} to {end_date.date()}...")
    
    # Tradier history endpoint
    url = f'{TRADIER_API_BASE}/markets/history'
    params = {
        'symbol': symbol,
        'interval': '1min',
        'start': start_date.strftime('%Y-%m-%d'),
        'end': end_date.strftime('%Y-%m-%d')
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Parse Tradier response format
                    history = data.get('history', {})
                    if not history or history == 'null':
                        print("⚠️  No historical data returned. Using placeholder data.")
                        return create_placeholder_data(symbol, days)
                    
                    day_data = history.get('day', [])
                    if not day_data:
                        print("⚠️  No day data in response. Using placeholder data.")
                        return create_placeholder_data(symbol, days)
                    
                    # Convert to DataFrame
                    # Note: Tradier format may vary - adjust parsing as needed
                    records = []
                    for day in day_data if isinstance(day_data, list) else [day_data]:
                        # Tradier returns daily bars, not 1-min. For 1-min, you may need
                        # to use a different endpoint or data provider
                        records.append({
                            'timestamp': pd.to_datetime(day.get('date', '')),
                            'open': float(day.get('open', 0)),
                            'high': float(day.get('high', 0)),
                            'low': float(day.get('low', 0)),
                            'close': float(day.get('close', 0)),
                            'volume': int(day.get('volume', 0))
                        })
                    
                    df = pd.DataFrame(records)
                    if df.empty:
                        return create_placeholder_data(symbol, days)
                    
                    df = df.sort_values('timestamp')
                    print(f"✅ Loaded {len(df)} candles")
                    return df
                else:
                    print(f"⚠️  API returned {resp.status}. Using placeholder data.")
                    return create_placeholder_data(symbol, days)
    except Exception as e:
        print(f"⚠️  Error fetching data: {e}. Using placeholder data.")
        return create_placeholder_data(symbol, days)


def create_placeholder_data(symbol: str, days: int) -> pd.DataFrame:
    """Create placeholder data for testing when API is unavailable"""
    print("📝 Creating placeholder data for testing...")
    dates = pd.date_range(
        start=datetime.now() - timedelta(days=days),
        end=datetime.now(),
        freq='1min'
    )
    
    # Simple random walk for testing
    import numpy as np
    np.random.seed(42)
    base_price = 450.0  # Example SPY price
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


async def run_backtest(symbol: str = 'SPY', days: int = 30):
    """
    Run a backtest by replaying historical data through AlphaEngine
    
    Args:
        symbol: Symbol to backtest (default: 'SPY')
        days: Number of days of history to use (default: 30)
    """
    print(f"\n{'='*60}")
    print(f"🧪 GEKKO3 BACKTESTER")
    print(f"{'='*60}")
    print(f"Symbol: {symbol}")
    print(f"Period: {days} days")
    print(f"{'='*60}\n")
    
    # Initialize components
    engine = AlphaEngine(lookback_minutes=400)  # Same as production
    gatekeeper = MockGatekeeper()
    
    # Fetch historical data
    df = await fetch_historical_data(symbol, days)
    
    if df.empty:
        print("❌ No data available for backtest")
        return
    
    print(f"\n▶️  Starting replay of {len(df)} candles...\n")
    
    # Replay data
    signal_count = 0
    for idx, row in df.iterrows():
        timestamp = pd.to_datetime(row['timestamp'])
        
        # Feed the engine (simulating real-time updates)
        engine.update(symbol, float(row['close']), int(row['volume']))
        
        # Update VIX periodically (simulate VIX poller)
        if idx % 100 == 0:  # Every 100 candles, update VIX
            # Use a placeholder VIX value for backtesting
            engine.set_vix(15.0, timestamp)
        
        # Check signals (simplified version - you can import from market_feed)
        indicators = engine.get_indicators(symbol)
        
        # Example signal checks (simplified)
        if indicators.get('is_warm', False):
            rsi = indicators.get('rsi', 50)
            trend = indicators.get('trend', 'UNKNOWN')
            
            # Log significant events
            if idx % 1000 == 0:  # Every 1000 candles
                print(f"⏱️  {timestamp.strftime('%Y-%m-%d %H:%M')} | "
                      f"Price: ${row['close']:.2f} | RSI: {rsi:.1f} | Trend: {trend}")
            
            # Example: RSI extreme signal
            if rsi < 5 or rsi > 95:
                signal_count += 1
                print(f"⚡ [SIGNAL #{signal_count}] RSI Extreme: {rsi:.1f} at {timestamp}")
        
        # Progress indicator
        if (idx + 1) % 5000 == 0:
            progress = ((idx + 1) / len(df)) * 100
            print(f"📊 Progress: {progress:.1f}% ({idx + 1}/{len(df)} candles)")
    
    # Print summary
    print(f"\n{'='*60}")
    print(gatekeeper.get_trade_summary())
    print(f"Total Signals Detected: {signal_count}")
    print(f"{'='*60}\n")
    
    # Save results
    results_file = f'backtest_results_{symbol}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    with open(results_file, 'w') as f:
        json.dump({
            'symbol': symbol,
            'days': days,
            'total_candles': len(df),
            'signals_detected': signal_count,
            'trades': gatekeeper.trades
        }, f, indent=2)
    
    print(f"💾 Results saved to: {results_file}")


if __name__ == "__main__":
    import sys
    
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'SPY'
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    
    asyncio.run(run_backtest(symbol, days))
