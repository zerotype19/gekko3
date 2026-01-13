"""
Backtester: Time Machine for Gekko3
Replays historical data through AlphaEngine to test strategies
Includes P&L Tracking and Risk Metrics
"""

import pandas as pd
import numpy as np
import asyncio
import json
from datetime import datetime, timedelta
import requests
import os
import traceback
from dotenv import load_dotenv

from src.alpha_engine import AlphaEngine
from src.notifier import get_notifier

# Load environment variables
load_dotenv()

TRADIER_API_BASE = "https://api.tradier.com/v1"

class BacktestTrade:
    def __init__(self, symbol, strategy, side, entry_price, entry_time, size=100):
        self.symbol = symbol
        self.strategy = strategy
        self.side = side  # 'LONG', 'SHORT', 'NEUTRAL'
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.size = size  # Number of shares/units
        self.exit_price = None
        self.exit_time = None
        self.status = 'OPEN'
        self.pnl = 0.0
        self.return_pct = 0.0

    def close(self, exit_price, exit_time):
        self.exit_price = exit_price
        self.exit_time = exit_time
        self.status = 'CLOSED'
        
        # Calculate P&L (Simulated)
        if self.side == 'LONG':
            self.pnl = (self.exit_price - self.entry_price) * self.size
        elif self.side == 'SHORT':
            self.pnl = (self.entry_price - self.exit_price) * self.size
        elif self.side == 'NEUTRAL': # Iron Condor / Credit Spread Logic
            # Simulation: Win if price stays within +/- 0.5% of entry
            # Assume Credit = $50. Risk = $450 (Standard $5 wide spread)
            move_pct = abs(self.exit_price - self.entry_price) / self.entry_price
            
            if move_pct < 0.005:
                # Win: Kept premium
                self.pnl = 50.0 * (self.size / 100)
            else:
                # Loss: Drifted too far
                # Approximate loss gradient: -$100 per 1% move beyond threshold
                # Cap at max loss of spread ($450)
                excess_move = move_pct - 0.005
                theoretical_loss = excess_move * self.entry_price * self.size
                self.pnl = -min(theoretical_loss, 450.0 * (self.size / 100))

        # ROI (Estimate)
        if self.side == 'NEUTRAL':
            invested = 500.0 * (self.size / 100) # Margin requirement
        else:
            invested = self.entry_price * self.size
            
        if invested > 0:
            self.return_pct = (self.pnl / invested) * 100

class BacktestAccountant:
    def __init__(self):
        self.trades = []
        self.closed_trades = []
        
    def log_trade(self, trade):
        self.trades.append(trade)
        print(f"💰 [OPEN] {trade.strategy} ({trade.side}) @ ${trade.entry_price:.2f}")

    def close_trade(self, trade):
        self.closed_trades.append(trade)
        print(f"🔒 [CLOSE] {trade.strategy} @ ${trade.exit_price:.2f} | P&L: ${trade.pnl:.2f} ({trade.return_pct:.1f}%)")

    def get_summary(self):
        if not self.closed_trades:
            return "No trades closed."
            
        total_pnl = sum(t.pnl for t in self.closed_trades)
        wins = [t for t in self.closed_trades if t.pnl > 0]
        win_rate = len(wins) / len(self.closed_trades) * 100
        
        # Max Drawdown
        cumulative = np.cumsum([t.pnl for t in self.closed_trades])
        peak = np.maximum.accumulate(cumulative)
        drawdown = peak - cumulative
        max_dd = np.max(drawdown) if len(drawdown) > 0 else 0.0
        
        summary =  f"\n📊 PERFORMANCE REPORT\n"
        summary += f"----------------------------------------\n"
        summary += f"Total Trades:   {len(self.closed_trades)}\n"
        summary += f"Win Rate:       {win_rate:.1f}%\n"
        summary += f"Total P&L:      ${total_pnl:.2f}\n"
        summary += f"Max Drawdown:   -${max_dd:.2f}\n"
        summary += f"----------------------------------------"
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
        # Use synchronous requests
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        
        if resp.status_code == 200:
            data = resp.json()
            series = data.get('series', {}).get('data', [])
            
            if not series:
                print("⚠️  No data returned. Market might be closed or symbol invalid.")
                return create_placeholder_data(symbol, days)
            
            # Robust data cleaning
            clean_data = []
            for row in series:
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
    print(f"\n{'='*60}\n🧪 GEKKO3 BACKTESTER (P&L Tracking Enabled)\n{'='*60}")
    
    engine = AlphaEngine(lookback_minutes=400) 
    accountant = BacktestAccountant()
    
    df = await fetch_historical_data(symbol, days)
    if df.empty: return
    
    print(f"\n▶️  Starting replay of {len(df)} candles...\n")
    
    open_trades = []
    warmup_complete_at = None
    
    # Track signal history
    last_signals = {}
    daily_signals = {} 
    
    for idx, row in df.iterrows():
        timestamp = pd.to_datetime(row['timestamp'])
        price = float(row['close'])
        
        # 1. Update Engine
        engine.update(symbol, price, int(row['volume']), timestamp=timestamp)
        if idx % 100 == 0: engine.set_vix(20.0, timestamp)
        
        # 2. Check Exits (All strategies except Scalper exit at EOD)
        # Scalper exits on RSI reversal
        for trade in open_trades[:]:
            should_close = False
            
            # EOD Exit (Hard stop for all day trades at 15:55)
            if timestamp.hour == 15 and timestamp.minute >= 55:
                should_close = True
            
            # Scalper Specific Exit (Mean Reversion)
            if trade.strategy == 'SCALPER':
                rsi_now = engine.get_rsi(symbol, period=2)
                if trade.side == 'LONG' and rsi_now > 50:
                    should_close = True
                elif trade.side == 'SHORT' and rsi_now < 50:
                    should_close = True
            
            if should_close:
                trade.close(price, timestamp)
                accountant.close_trade(trade)
                open_trades.remove(trade)

        # 3. Check Signals
        indicators = engine.get_indicators(symbol)
        is_warm = indicators.get('is_warm', False)
        
        if is_warm and warmup_complete_at is None:
            warmup_complete_at = idx
            print(f"✅ Warmup complete at {timestamp}")

        # Signal Deduplication
        current_date = timestamp.date()
        if current_date not in daily_signals: daily_signals[current_date] = {}
        
        signal = None
        strategy = None
        side = None
        
        # A. ORB
        if timestamp.hour == 10 or (timestamp.hour == 11 and timestamp.minute < 30):
            orb_key = f"ORB_{current_date}"
            if orb_key not in daily_signals[current_date]:
                orb = engine.get_opening_range(symbol)
                if orb['complete']:
                    if price > orb['high']:
                        signal = 'ORB_BULL'
                        strategy = 'ORB'
                        side = 'LONG'
                        daily_signals[current_date][orb_key] = True
                    elif price < orb['low']:
                        signal = 'ORB_BEAR'
                        strategy = 'ORB'
                        side = 'SHORT'
                        daily_signals[current_date][orb_key] = True

        # B. Farmer
        if not signal and timestamp.hour == 13 and 0 <= timestamp.minute < 5:
            farmer_key = f"FARMER_{current_date}"
            if farmer_key not in daily_signals[current_date]:
                adx = engine.get_adx(symbol)
                if adx < 20:
                    signal = 'CONDOR'
                    strategy = 'FARMER'
                    side = 'NEUTRAL'
                    daily_signals[current_date][farmer_key] = True

        # C. Scalper (Cooldown 5 mins)
        if not signal:
            rsi_2 = engine.get_rsi(symbol, period=2)
            if rsi_2 is not None:
                last_sig = last_signals.get(symbol, {})
                time_since = (timestamp - last_sig.get('timestamp', timestamp)).seconds if last_sig.get('timestamp') else 999
                
                if rsi_2 < 5 and (last_sig.get('signal') != 'SCALP_BULL' or time_since > 300):
                    signal = 'SCALP_BULL'
                    strategy = 'SCALPER'
                    side = 'LONG'
                elif rsi_2 > 95 and (last_sig.get('signal') != 'SCALP_BEAR' or time_since > 300):
                    signal = 'SCALP_BEAR'
                    strategy = 'SCALPER'
                    side = 'SHORT'

        # D. Trend
        if not signal and is_warm:
            trend = indicators['trend']
            rsi = indicators['rsi']
            flow = indicators['flow_state']
            
            last_sig = last_signals.get(symbol, {})
            time_since = (timestamp - last_sig.get('timestamp', timestamp)).seconds if last_sig.get('timestamp') else 999
            
            if trend == 'UPTREND' and rsi < 30 and flow != 'NEUTRAL' and time_since > 300:
                signal = 'TREND_BULL'
                strategy = 'TREND'
                side = 'LONG'
            elif trend == 'DOWNTREND' and rsi > 70 and flow != 'NEUTRAL' and time_since > 300:
                signal = 'TREND_BEAR'
                strategy = 'TREND'
                side = 'SHORT'

        # Execute Signal
        if signal:
            last_signals[symbol] = {'signal': signal, 'timestamp': timestamp}
            
            # Create Trade
            trade = BacktestTrade(symbol, strategy, side, price, timestamp)
            open_trades.append(trade)
            accountant.log_trade(trade)

        if (idx + 1) % 5000 == 0:
            print(f"📊 Progress: {(idx+1)/len(df)*100:.1f}%", flush=True)

    print(accountant.get_summary())

if __name__ == "__main__":
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'SPY'
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    asyncio.run(run_backtest(symbol, days))
