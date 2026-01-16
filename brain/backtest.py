"""
Backtester: Time Machine for Gekko3
Replays historical data through AlphaEngine to test strategies.
Includes Mark-to-Market P&L for Calendars, Ratios, and Credit Spreads.
Uses PositionSizer for dynamic risk management.
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
from concurrent.futures import ThreadPoolExecutor

# Import core systems
from src.alpha_engine import AlphaEngine
from src.regime_engine import RegimeEngine
from src.position_sizer import PositionSizer

# Load environment variables
load_dotenv()

TRADIER_API_BASE = "https://api.tradier.com/v1"

class BacktestTrade:
    def __init__(self, symbol, strategy, side, entry_price, entry_time, size, signal=None, regime=None, vix_at_entry=None):
        self.symbol = symbol
        self.strategy = strategy
        self.side = side 
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.size = size
        self.exit_price = None
        self.exit_time = None
        self.status = 'OPEN'
        self.pnl = 0.0
        self.return_pct = 0.0
        self.signal = signal
        self.regime = regime
        self.vix_at_entry = vix_at_entry or 15.0
        
        # Strategy Assumptions (Standardized for Backtest)
        self.spread_width = 5.0  
        self.credit_received = 0.50 
        
        # Determine Bias
        self.bias = 'NEUTRAL'
        if 'BULL' in str(signal): self.bias = 'BULLISH'
        elif 'BEAR' in str(signal): self.bias = 'BEARISH'
        elif 'RATIO' in str(strategy): self.bias = 'BEARISH_HEDGE'
        
        # Determine DTE and Type
        if 'CALENDAR' in str(strategy) or 'BEAST' in str(signal):
            self.target_dte = 45 
            self.type = 'VEGA_LONG'
        elif 'RATIO' in str(strategy) or 'SKEW' in str(signal):
            self.target_dte = 30
            self.type = 'GAMMA_LONG' 
        else:
            self.target_dte = 30
            self.type = 'THETA_SHORT'

    def close(self, exit_price, exit_time):
        """Calculate P&L based on Strategy Type (Mark-to-Market)"""
        self.exit_price = exit_price
        self.exit_time = exit_time
        self.status = 'CLOSED'
        
        pct_change = (exit_price - self.entry_price) / self.entry_price
        time_delta = exit_time - self.entry_time
        days_held = max(0.5, time_delta.total_seconds() / 86400)
        
        # --- LOGIC 1: CALENDAR SPREAD (Volatility Beast) ---
        if self.type == 'VEGA_LONG':
            debit_paid = 150.0 
            
            # A. Directional Risk (Gamma)
            price_move = abs(pct_change)
            direction_loss = 0
            if price_move > 0.015: # Widened breakeven slightly
                direction_loss = (price_move - 0.015) * 100 * self.size * 25
            
            # B. Volatility Profit (Vega)
            # Assume mean reversion of VIX generates profit
            vol_profit = 0
            if self.vix_at_entry < 14:
                vol_profit = 40.0 * self.size * days_held * 0.5 
            
            # C. Theta Profit
            theta_profit = 12.0 * days_held * self.size
            
            estimated = theta_profit + vol_profit - direction_loss
            max_risk = debit_paid * self.size
            self.pnl = max(-max_risk, min(max_risk * 0.8, estimated))

        # --- LOGIC 2: RATIO BACKSPREAD (Trend Skew) ---
        elif self.type == 'GAMMA_LONG':
            credit_received = 20.0 
            
            if self.bias == 'BEARISH_HEDGE':
                if pct_change < -0.04: 
                    gamma_mult = abs(pct_change) / 0.04
                    self.pnl = 500.0 * self.size * gamma_mult
                elif pct_change > 0.01:
                    self.pnl = credit_received * self.size
                else:
                    self.pnl = -150.0 * self.size * (days_held / 20)
            else:
                self.pnl = 0

        # --- LOGIC 3: STANDARD CREDIT SPREAD (Trend/Farmer) ---
        else: 
            max_profit = self.credit_received * 100
            max_loss = (self.spread_width - self.credit_received) * 100
            
            theta_gain = (max_profit / self.target_dte) * days_held
            net_delta = 0.10
            price_diff = exit_price - self.entry_price
            
            if self.bias == 'BULLISH': delta_pnl = price_diff * 100 * net_delta
            elif self.bias == 'BEARISH': delta_pnl = -price_diff * 100 * net_delta
            else: delta_pnl = -abs(price_diff) * 100 * net_delta
                
            estimated = (theta_gain + delta_pnl) * self.size
            max_daily_gain = (max_profit / 30) * (days_held + 2) 
            self.pnl = max(-max_loss * self.size, min(max_daily_gain * self.size, estimated))

        margin = self.spread_width * 100 * self.size
        if margin > 0: self.return_pct = (self.pnl / margin) * 100

class BacktestAccountant:
    def __init__(self, initial_equity=100000.0):
        self.equity = initial_equity
        self.initial_equity = initial_equity
        self.trades = []
        self.closed_trades = []
        self.position_sizer = PositionSizer()
        
    def get_trade_size(self, spread_width=5.0):
        return self.position_sizer.calculate_size(self.equity, spread_width)

    def log_trade(self, trade):
        self.trades.append(trade)
        print(f"💰 [OPEN] {trade.signal} ({trade.strategy}) Size:{trade.size} @ ${trade.entry_price:.2f} (VIX: {trade.vix_at_entry:.2f})")

    def close_trade(self, trade):
        self.closed_trades.append(trade)
        self.equity += trade.pnl
        print(f"🔒 [CLOSE] {trade.signal} P&L: ${trade.pnl:.2f} | Equity: ${self.equity:,.2f}")

    def get_summary(self):
        if not self.closed_trades: return "No trades closed."
        
        wins = [t for t in self.closed_trades if t.pnl > 0]
        win_rate = len(wins) / len(self.closed_trades) * 100
        
        equity_curve = [self.initial_equity]
        current = self.initial_equity
        for t in self.closed_trades:
            current += t.pnl
            equity_curve.append(current)
            
        peak = np.maximum.accumulate(equity_curve)
        drawdown = (peak - equity_curve) / peak * 100
        max_dd = np.max(drawdown)
        
        strat_stats = {}
        for t in self.closed_trades:
            s = t.strategy
            if s not in strat_stats: strat_stats[s] = {'count':0, 'pnl':0, 'wins':0}
            strat_stats[s]['count'] += 1
            strat_stats[s]['pnl'] += t.pnl
            if t.pnl > 0: strat_stats[s]['wins'] += 1

        total_return = ((self.equity / self.initial_equity) - 1) * 100
        
        summary = f"\n📊 BACKTEST REPORT\n{'='*40}\n"
        summary += f"Return:       {total_return:.2f}%\n"
        summary += f"Max DD:       {max_dd:.2f}%\n"
        summary += f"Win Rate:     {win_rate:.1f}%\n"
        summary += f"Trades:       {len(self.closed_trades)}\n{'='*40}\n"
        for s, stats in strat_stats.items():
            wr = stats['wins']/stats['count']*100
            summary += f"{s:15s}: {stats['count']:3d} | ${stats['pnl']:8.2f} | {wr:5.1f}% WR\n"
        return summary

def fetch_data_sync(symbol: str, days: int):
    token = os.getenv('TRADIER_ACCESS_TOKEN')
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M')
    
    api_symbol = symbol
    if symbol == 'VIX': api_symbol = 'VIX' 
    
    url = f'{TRADIER_API_BASE}/markets/timesales'
    params = {'symbol': api_symbol, 'interval': '1min', 'start': start, 'session_filter': 'all'}
    
    try:
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code == 200:
            return resp.json().get('series', {}).get('data', [])
        return []
    except: return []

async def run_backtest(symbol: str = 'SPY', days: int = 20):
    print(f"\n🧪 GEKKO3 PIVOT BACKTEST: {symbol} ({days} days)")
    
    engine = AlphaEngine(lookback_minutes=600)
    regime_engine = RegimeEngine(engine)
    accountant = BacktestAccountant()
    
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as executor:
        price_data = await loop.run_in_executor(None, fetch_data_sync, symbol, days)
        vix_data = await loop.run_in_executor(None, fetch_data_sync, 'VIX', days)
    
    if not price_data:
        print("❌ No data found.")
        return

    vix_map = {}
    for row in vix_data:
        ts = pd.to_datetime(row['time']) if 'time' in row else pd.to_datetime(row['timestamp'], unit='s')
        vix_map[ts.floor('min')] = float(row.get('close', 0))

    df = pd.DataFrame([{
        'timestamp': pd.to_datetime(row['time']) if 'time' in row else pd.to_datetime(row['timestamp'], unit='s'),
        'close': float(row['close']),
        'open': float(row['open']),
        'high': float(row['high']),
        'low': float(row['low']),
        'volume': int(row['volume'])
    } for row in price_data]).sort_values('timestamp')

    print(f"✅ Data Loaded: {len(df)} candles | VIX Coverage: {len(vix_map)} points")
    
    open_trades = []
    last_proposal_time = {}
    warmup_idx = 0
    
    for idx, row in df.iterrows():
        ts = row['timestamp']
        price = row['close']
        
        # Update Engine
        engine.update(symbol, price, row['volume'], timestamp=ts)
        
        # Update VIX
        vix_val = vix_map.get(ts.floor('min'))
        if not vix_val and idx > 0 and vix_map: 
             pass 
        if vix_val: engine.set_vix(vix_val, ts)
        current_vix = engine.get_vix() or 20.0
        
        # Check Exits (MULTI-DAY LOGIC)
        for trade in open_trades[:]:
            should_close = False
            
            # Force Close if Expired (simplified simulation)
            days_held = (ts - trade.entry_time).days
            if days_held > trade.target_dte: should_close = True
            
            pct_move = (price - trade.entry_price) / trade.entry_price
            
            # Strategy Specific Stops
            if 'CALENDAR' in trade.strategy:
                # Stop if price moves too far (>2%) or held > 5 days (profit taking)
                if abs(pct_move) > 0.02: should_close = True
                if days_held >= 5: should_close = True
                
            elif 'RATIO' in trade.strategy:
                # Close if rally (profit) or huge crash (profit)
                if pct_move > 0.02: should_close = True 
                if pct_move < -0.05: should_close = True
                # Time stop if stuck (10 days)
                if days_held >= 10: should_close = True
                
            else: # Credit Spread / Condor
                # Stop loss if price moves > 1.5% against bias
                if trade.bias == 'BULLISH' and pct_move < -0.015: should_close = True
                elif trade.bias == 'BEARISH' and pct_move > 0.015: should_close = True
                # Take profit (Theta capture) after 5 days
                if days_held >= 5: should_close = True
            
            if should_close:
                trade.close(price, ts)
                accountant.close_trade(trade)
                open_trades.remove(trade)

        # Check Entries
        indicators = engine.get_indicators(symbol)
        if not indicators.get('is_warm', False):
            warmup_idx += 1
            continue
            
        current_regime = regime_engine.get_regime(symbol)
        
        # Cooldown (2 hours for same symbol to avoid spamming)
        last = last_proposal_time.get(symbol)
        if last and (ts - last).total_seconds() < 7200: continue
        
        signal = None
        strategy = None
        
        # --- STRATEGY 1: VOLATILITY BEAST ---
        if ts.hour == 10 and current_vix < 15:
            orb = engine.get_opening_range(symbol)
            if orb['complete'] and orb['low'] > 0:
                range_pct = (orb['high'] - orb['low']) / orb['low']
                if range_pct < 0.005: 
                    signal = 'VOLATILITY_BEAST'
                    strategy = 'CALENDAR_SPREAD'

        # --- STRATEGY 2: RANGE FARMER (Stricter) ---
        if not signal and current_regime.value == 'LOW_VOL_CHOP' and ts.hour == 13:
            adx = engine.get_adx(symbol)
            if adx < 20:
                poc = indicators.get('poc', 0)
                if poc > 0 and abs(price - poc) < 2.00:
                    signal = 'IRON_CONDOR'
                    strategy = 'IRON_CONDOR'

        # --- STRATEGY 3: TREND ENGINE (Skew Upgrade) ---
        if not signal and current_regime.value == 'TRENDING':
            trend = indicators['trend']
            rsi = indicators['rsi']
            poc = indicators.get('poc', 0)
            vah = indicators.get('vah', 0)
            val = indicators.get('val', 0)
            
            if poc > 0:
                use_skew = current_vix < 13
                
                if trend == 'UPTREND':
                    if (price > vah and rsi < 60) or (price > poc and price < vah and rsi < 30):
                        if use_skew:
                            signal = 'SKEW_RATIO_SPREAD'
                            strategy = 'RATIO_SPREAD'
                        else:
                            signal = 'BULL_PUT_SPREAD'
                            strategy = 'CREDIT_SPREAD'
                            
                elif trend == 'DOWNTREND':
                    if (price < val and rsi > 40) or (price < poc and price > val and rsi > 70):
                        signal = 'BEAR_CALL_SPREAD'
                        strategy = 'CREDIT_SPREAD'

        if signal:
            size = accountant.get_trade_size(5.0)
            trade = BacktestTrade(symbol, strategy, 'OPEN', price, ts, size, signal, current_regime.value, current_vix)
            open_trades.append(trade)
            accountant.log_trade(trade)
            last_proposal_time[symbol] = ts

    print(accountant.get_summary())

if __name__ == "__main__":
    import sys
    asyncio.run(run_backtest(sys.argv[1] if len(sys.argv)>1 else 'SPY', 20))
