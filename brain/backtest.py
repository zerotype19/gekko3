"""
Backtester: Time Machine for Gekko3
Replays historical data through AlphaEngine to test strategies.
Includes P&L Tracking, Risk Metrics, and Volume Profile Diagnostics.
Uses PositionSizer for dynamic risk management (matches live system).
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
    def __init__(self, symbol, strategy, side, entry_price, entry_time, size, signal=None, regime=None):
        self.symbol = symbol
        self.strategy = strategy
        self.side = side  # 'OPEN' (matches live system)
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.size = size  # Number of contracts (calculated by PositionSizer)
        self.exit_price = None
        self.exit_time = None
        self.status = 'OPEN'
        self.pnl = 0.0
        self.return_pct = 0.0
        self.signal = signal
        self.regime = regime
        
        # Strategy Parameters
        self.spread_width = 5.0  # $5 wide spread
        self.credit_received = 0.50  # $0.50 per share = $50/contract
        
        # Determine directional bias for P&L logic
        self.bias = 'NEUTRAL'
        if 'BULL' in str(signal):
            self.bias = 'BULLISH'
        elif 'BEAR' in str(signal):
            self.bias = 'BEARISH'
        elif 'RATIO' in str(strategy) or 'SKEW' in str(signal):
            # Ratio Spreads are bearish hedges (profit on crashes)
            self.bias = 'BEARISH_HEDGE'
        
        # Set DTE based on Strategy (CRITICAL FIX)
        # Scalper/ORB = 0DTE (Binary Outcome - expires same day)
        # Trend/Farmer/Calendar = 30-60 DTE (Partial Decay - only capture 1 day worth of theta)
        if 'SCALP' in str(strategy) or 'ORB' in str(strategy):
            self.target_dte = 0
        elif 'CALENDAR' in str(strategy) or 'BEAST' in str(signal):
            # Calendar Spreads: Long-term structure (front ~30 DTE, back ~60 DTE)
            self.target_dte = 45  # Average DTE for calendar
        else:
            self.target_dte = 30

    def close(self, exit_price, exit_time):
        """Calculate P&L with realistic theta decay (mark-to-market)"""
        self.exit_price = exit_price
        self.exit_time = exit_time
        self.status = 'CLOSED'
        
        pct_change = (exit_price - self.entry_price) / self.entry_price
        max_profit = self.credit_received * 100  # $50 per contract
        max_loss = (self.spread_width - self.credit_received) * 100  # $450 per contract
        
        # --- 0DTE LOGIC (Binary Outcome) ---
        # Scalper/ORB trades expire same day - all or nothing
        if self.target_dte == 0:
            if self.bias == 'BULLISH':
                # Bull Put: Win if price held or went up (stayed above short strike)
                if pct_change > -0.001:
                    self.pnl = max_profit * self.size
                else:
                    # Price broke support - max loss
                    self.pnl = -max_loss * self.size
            elif self.bias == 'BEARISH':
                # Bear Call: Win if price held or went down (stayed below short strike)
                if pct_change < 0.001:
                    self.pnl = max_profit * self.size
                else:
                    # Price broke resistance - max loss
                    self.pnl = -max_loss * self.size
            else:  # NEUTRAL
                # Iron Condor: Win if price stayed in range
                self.pnl = (max_profit if abs(pct_change) < 0.005 else -max_loss) * self.size

        # --- 30 DTE LOGIC (Mark-to-Market with Theta Decay) ---
        # Trend/Farmer trades are 30 DTE - only capture partial theta per day
        else:
            # 1. Theta Decay (Time Value)
            # Calculate days held (fractional for intraday exits)
            time_delta = exit_time - self.entry_time
            days_held = max(0.5, time_delta.total_seconds() / 86400)  # Convert to days
            
            # If same day exit, use 0.5 days (half trading day)
            if exit_time.date() == self.entry_time.date():
                hours_held = time_delta.total_seconds() / 3600
                days_held = max(0.25, hours_held / 6.5)  # 6.5 hour trading day
            
            # Theta gain: Only capture proportional decay based on days held
            # Example: $50 credit spread over 30 days = ~$1.67 per day
            theta_gain = (max_profit / self.target_dte) * days_held
            
            # 2. Delta Impact (Price Movement)
            # Approximate net delta for credit spread ~ 0.10 (varies with DTE and moneyness)
            net_delta = 0.10
            price_diff = exit_price - self.entry_price
            
            if self.bias == 'BULLISH':
                # Price UP = Good (spread worth less) = Positive P&L
                # Price DOWN = Bad (spread worth more) = Negative P&L
                delta_pnl = price_diff * 100 * net_delta * self.size
            elif self.bias == 'BEARISH':
                # Price DOWN = Good (spread worth less) = Positive P&L
                # Price UP = Bad (spread worth more) = Negative P&L
                delta_pnl = -price_diff * 100 * net_delta * self.size
            else:  # NEUTRAL (Iron Condor)
                # Moves away from center are bad (spread worth more)
                delta_pnl = -abs(price_diff) * 100 * net_delta * self.size

            # --- CALENDAR SPREAD LOGIC (Long Vega, Long Theta, Negative Gamma) ---
            if 'CALENDAR' in str(self.strategy) or 'BEAST' in str(self.signal):
                # Calendar Spread: Buy volatility (Long Vega), Long Theta
                # Entry: Typically a debit ($100-$200 per spread)
                debit_paid = 150.0  # Estimated debit per spread
                
                # 1. Price Move Impact (Negative Gamma) - Lose money if price moves away from strike
                price_move_pct = abs(pct_change)
                direction_loss = 0.0
                if price_move_pct > 0.01:  # Moved > 1% away from strike
                    # Lose more as price moves further (gamma risk)
                    direction_loss = (price_move_pct - 0.01) * debit_paid * self.size * 2.0
                
                # 2. Volatility Impact (Vega) - Profit from IV expansion
                # We entered when VIX was low (<15), profit if IV normalizes/higher
                # Assume small positive drift in IV (backtest assumption)
                vol_profit = 50.0 * self.size  # Base profit for "buying low vol"
                
                # 3. Theta (Time) - Make money every day we hold (time decay on short leg > long leg)
                days_held = max(0.5, days_held)  # Reuse calculated days_held
                theta_profit = 20.0 * days_held * self.size  # ~$20 per day theta decay
                
                estimated_pnl = vol_profit + theta_profit - direction_loss
                
                # Cap profit at reasonable % of debit (e.g., 50% max return usually)
                # Cap loss at debit paid
                max_risk = debit_paid * self.size
                self.pnl = max(-max_risk, min(max_risk * 0.5, estimated_pnl))
            
            # --- RATIO SPREAD LOGIC (Skew Trade - Asymmetric Risk/Reward) ---
            elif 'RATIO' in str(self.strategy) or 'SKEW' in str(self.signal):
                # Ratio Backspread: Sell 1 ATM, Buy 2 OTM (typically Put Ratio for Skew)
                # Net Credit or small Debit
                credit_received = 50.0  # Estimated credit per spread
                
                if self.bias == 'BEARISH_HEDGE':
                    # PUT Ratio: Sell 1 ATM Put, Buy 2 OTM Puts
                    if pct_change < -0.05:  # CRASH! Massive Profit (Gamma)
                        # OTM puts print massively on crash
                        profit_factor = abs(pct_change) * 10  # Exponential profit on big moves
                        self.pnl = min(1000.0 * self.size, credit_received * self.size * profit_factor)
                    elif pct_change > 0:
                        # Rally: Short ATM Put expires worthless, keep credit or lose small debit
                        self.pnl = credit_received * self.size * 0.8  # Keep most of credit
                    else:
                        # Slow bleed down (The Trap) - Loss peaks at OTM strike
                        # Price hangs near short strike = worst case
                        loss_factor = abs(pct_change) / 0.05  # Scale loss from 0 to -100% of credit
                        self.pnl = -credit_received * self.size * loss_factor * 2.0
                else:
                    # Standard Ratio Spread logic (if not hedge)
                    if abs(pct_change) < 0.01:
                        self.pnl = credit_received * self.size
                    else:
                        loss_factor = min((abs(pct_change) - 0.01) / 0.04, 1.0)
                        self.pnl = -(credit_received * self.size * 3.0) * loss_factor  # Larger max loss for ratios
            
            # --- STANDARD CREDIT SPREAD / IRON CONDOR LOGIC ---
            else:
                # Combine Theta + Delta for estimated P&L
                estimated_pnl_per_contract = theta_gain + (delta_pnl / self.size if self.size > 0 else 0)
                estimated_pnl = estimated_pnl_per_contract * self.size
                
                # Cap at realistic limits
                # Can't lose more than max loss, can't gain more than full credit in one day
                # (Can't exceed ~3 days worth of theta in a single day)
                max_daily_gain = (max_profit / self.target_dte) * 3  # Cap at 3 days of theta
                self.pnl = max(-max_loss * self.size, min(max_daily_gain * self.size, estimated_pnl))

        # ROI calculation on margin used
        margin_used = self.spread_width * 100 * self.size
        if margin_used > 0:
            self.return_pct = (self.pnl / margin_used) * 100

class BacktestAccountant:
    def __init__(self, initial_equity=100000.0):
        self.equity = initial_equity
        self.initial_equity = initial_equity
        self.trades = []
        self.closed_trades = []
        self.position_sizer = PositionSizer()  # Use the live system's sizer
        
    def get_trade_size(self, spread_width=5.0):
        """Delegate to PositionSizer for dynamic sizing based on current equity"""
        return self.position_sizer.calculate_size(self.equity, spread_width)

    def log_trade(self, trade):
        self.trades.append(trade)
        regime_str = f" [{trade.regime}]" if trade.regime else ""
        signal_str = f" ({trade.signal})" if trade.signal else ""
        print(f"💰 [OPEN] {trade.strategy}{regime_str}{signal_str} Size:{trade.size} @ ${trade.entry_price:.2f}")

    def close_trade(self, trade):
        self.closed_trades.append(trade)
        self.equity += trade.pnl  # Update equity with P&L
        print(f"🔒 [CLOSE] {trade.strategy} P&L: ${trade.pnl:.2f} ({trade.return_pct:.1f}%) | Equity: ${self.equity:,.2f}")

    def get_summary(self):
        if not self.closed_trades:
            return "No trades closed."
            
        wins = [t for t in self.closed_trades if t.pnl > 0]
        win_rate = len(wins) / len(self.closed_trades) * 100 if self.closed_trades else 0
        
        # Equity Curve for Drawdown Calculation
        equity_curve = [self.initial_equity]
        current = self.initial_equity
        for t in self.closed_trades:
            current += t.pnl
            equity_curve.append(current)
            
        peak = np.maximum.accumulate(equity_curve)
        drawdown = (peak - equity_curve) / peak * 100
        max_dd = np.max(drawdown) if len(drawdown) > 0 else 0.0
        
        # Strategy breakdown
        strat_stats = {}
        for t in self.closed_trades:
            s = t.strategy
            if s not in strat_stats:
                strat_stats[s] = {'count': 0, 'pnl': 0, 'wins': 0}
            strat_stats[s]['count'] += 1
            strat_stats[s]['pnl'] += t.pnl
            if t.pnl > 0:
                strat_stats[s]['wins'] += 1

        total_return_pct = ((self.equity / self.initial_equity) - 1) * 100
        
        summary = f"\n📊 BACKTEST PERFORMANCE REPORT\n"
        summary += f"----------------------------------------\n"
        summary += f"Initial Equity: ${self.initial_equity:,.2f}\n"
        summary += f"Final Equity:   ${self.equity:,.2f}\n"
        summary += f"Total Return:   {total_return_pct:.2f}%\n"
        summary += f"Max Drawdown:   {max_dd:.2f}%\n"
        summary += f"Total Trades:   {len(self.closed_trades)}\n"
        summary += f"Win Rate:       {win_rate:.1f}%\n"
        summary += f"----------------------------------------\n"
        summary += f"STRATEGY BREAKDOWN:\n"
        for s, stats in strat_stats.items():
            wr = (stats['wins'] / stats['count'] * 100) if stats['count'] > 0 else 0
            summary += f"  {s:20s}: {stats['count']:3d} trades | ${stats['pnl']:8.2f} P&L | {wr:5.1f}% WR\n"
        summary += f"----------------------------------------"
        return summary

def fetch_data_sync(symbol: str, days: int):
    """Synchronous data fetcher to run in executor (avoids blocking event loop)"""
    access_token = os.getenv('TRADIER_ACCESS_TOKEN', '')
    if not access_token:
        raise ValueError('TRADIER_ACCESS_TOKEN must be set in .env')
    
    headers = {'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'}
    
    safe_days = min(days, 20)  # Tradier limit
    end_date = datetime.now()
    start_date = end_date - timedelta(days=safe_days)
    
    url = f'{TRADIER_API_BASE}/markets/timesales'
    params = {
        'symbol': symbol,
        'interval': '1min',
        'start': start_date.strftime('%Y-%m-%d %H:%M'),
        'end': end_date.strftime('%Y-%m-%d %H:%M'),
        'session_filter': 'all'
    }
    
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        
        if resp.status_code == 200:
            data = resp.json()
            series_root = data.get('series') or {}
            series = series_root.get('data', []) if isinstance(series_root, dict) else []
            return series
        else:
            print(f"⚠️  API Error {resp.status_code} for {symbol}: {resp.text[:200]}")
            return []
    except Exception as e:
        print(f"⚠️  Connection Error for {symbol}: {e}")
        return []

async def fetch_historical_data(symbol: str, days: int = 20) -> pd.DataFrame:
    """Fetch historical data using executor to avoid blocking event loop"""
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as executor:
        series = await loop.run_in_executor(None, fetch_data_sync, symbol, days)
    
    if not series:
        print("⚠️ No data returned or API error.")
        return pd.DataFrame()
        
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
        
    df = pd.DataFrame(clean_data).sort_values('timestamp').reset_index(drop=True)
    
    # Data Quality Checks
    print(f"\n📊 DATA VALIDATION:")
    print(f"   Total Candles: {len(df)}")
    if not df.empty:
        print(f"   Date Range: {df['timestamp'].min()} to {df['timestamp'].max()}")
        print(f"   Price Range: ${df['close'].min():.2f} to ${df['close'].max():.2f}")
        print(f"   Avg Volume: {df['volume'].mean():,.0f}")
        print(f"   Missing Data: {df.isnull().sum().sum()} cells")
        
        # Check for suspicious data
        if df['close'].nunique() < 10:
            print(f"⚠️  WARNING: Only {df['close'].nunique()} unique prices (suspicious)")
        if (df['volume'] == 0).sum() > len(df) * 0.1:
            print(f"⚠️  WARNING: {(df['volume'] == 0).sum()} candles with zero volume (>10%)")
    
    print(f"✅ Loaded {len(df)} candles")
    return df

async def run_backtest(symbol: str = 'SPY', days: int = 20):
    print(f"\n{'='*60}\n🧪 GEKKO3 BACKTESTER (ALL STRATEGIES + VOLUME PROFILE)\n{'='*60}")
    print(f"Symbol: {symbol} | Period: {days} days\n")
    
    # Initialize engines (larger lookback for Volume Profile)
    engine = AlphaEngine(lookback_minutes=600)
    regime_engine = RegimeEngine(engine)
    accountant = BacktestAccountant(initial_equity=100000.0)
    
    # CRITICAL: Fetch both price data AND VIX data for accurate regime detection
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as executor:
        # Fetch price data for the symbol
        price_data = await loop.run_in_executor(None, fetch_data_sync, symbol, days)
        # Fetch VIX data for regime detection (REAL DATA, not simulation)
        vix_data = await loop.run_in_executor(None, fetch_data_sync, 'VIX', days)
    
    if not price_data:
        print("❌ Failed to load price data. Exiting.")
        return
    
    # Process VIX data into a lookup dictionary for fast minute-by-minute access
    vix_map = {}
    for row in vix_data:
        ts_val = row.get('time')
        if ts_val:
            ts = pd.to_datetime(ts_val)
        elif row.get('timestamp'):
            ts = pd.to_datetime(row.get('timestamp'), unit='s')
        else:
            continue
        
        # Floor to minute for matching with price data timestamps
        ts_minute = ts.floor('min')
        vix_close = float(row.get('close', 0))
        if vix_close > 0:  # Only store valid VIX values
            vix_map[ts_minute] = vix_close
    
    # Process price data into DataFrame
    clean_data = []
    for row in price_data:
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
        
    df = pd.DataFrame(clean_data).sort_values('timestamp').reset_index(drop=True)
    
    # Data Quality Checks
    print(f"\n📊 DATA VALIDATION:")
    print(f"   Total Candles: {len(df)}")
    if not df.empty:
        print(f"   Date Range: {df['timestamp'].min()} to {df['timestamp'].max()}")
        print(f"   Price Range: ${df['close'].min():.2f} to ${df['close'].max():.2f}")
        print(f"   Avg Volume: {df['volume'].mean():,.0f}")
        print(f"   Missing Data: {df.isnull().sum().sum()} cells")
        print(f"   VIX Data Points: {len(vix_map)}")
        if vix_map:
            vix_values = list(vix_map.values())
            print(f"   VIX Range: {min(vix_values):.2f} - {max(vix_values):.2f}")
        
        # Check for suspicious data
        if df['close'].nunique() < 10:
            print(f"⚠️  WARNING: Only {df['close'].nunique()} unique prices (suspicious)")
        if (df['volume'] == 0).sum() > len(df) * 0.1:
            print(f"⚠️  WARNING: {(df['volume'] == 0).sum()} candles with zero volume (>10%)")
        if len(vix_map) < len(df) * 0.5:
            print(f"⚠️  WARNING: VIX coverage is {len(vix_map)/len(df)*100:.1f}% (may affect regime detection)")

    print(f"\n▶️  Starting replay of {len(df)} candles with REAL VIX data...\n")
    
    open_trades = []
    warmup_complete_at = None
    
    # Track signal history
    last_signals = {}
    daily_signals = {}
    last_proposal_time = {}  # Cooldown tracking
    
    # Track last valid VIX for fallback
    last_valid_vix = None
    
    for idx, row in df.iterrows():
        timestamp = pd.to_datetime(row['timestamp'])
        price = float(row['close'])
        current_hour = timestamp.hour
        current_minute = timestamp.minute
        current_date = timestamp.date()
        
        # 1. Update Engine
        engine.update(symbol, price, int(row['volume']), timestamp=timestamp)
        
        # Update VIX from real historical data (CRITICAL for accurate regime detection)
        # Look for VIX data at this exact minute, or nearby minutes if not found
        ts_minute = timestamp.floor('min')
        vix_val = vix_map.get(ts_minute)
        
        # Fallback: Check nearby minutes if exact match not found (within 5 minutes)
        if vix_val is None and vix_map:
            for offset_minutes in [1, -1, 2, -2, 5, -5]:
                check_ts = ts_minute + timedelta(minutes=offset_minutes)
                if check_ts in vix_map:
                    vix_val = vix_map[check_ts]
                    break
        
        # Use found VIX value or fallback to last valid VIX
        if vix_val and vix_val > 0:
            engine.set_vix(vix_val, timestamp)
            last_valid_vix = vix_val
        elif last_valid_vix:
            # Fallback: Use last valid VIX value (better than simulation or None)
            engine.set_vix(last_valid_vix, timestamp)
        # If no VIX data at all, RegimeEngine will default to LOW_VOL_CHOP (handled in regime_engine.py)
        
        # 2. Check Exits (All strategies except Scalper exit at EOD)
        for trade in open_trades[:]:
            should_close = False
            
            # EOD Exit (Hard stop at 15:55)
            if current_hour == 15 and current_minute >= 55:
                should_close = True
            
            # Scalper Specific Exit (Mean Reversion)
            if 'SCALP' in str(trade.signal):
                rsi_now = engine.get_rsi(symbol, period=2)
                if trade.bias == 'BULLISH' and rsi_now and rsi_now > 60:
                    should_close = True
                elif trade.bias == 'BEARISH' and rsi_now and rsi_now < 40:
                    should_close = True
            
            if should_close:
                trade.close(price, timestamp)
                accountant.close_trade(trade)
                open_trades.remove(trade)

        # 3. Get Current State
        indicators = engine.get_indicators(symbol)
        is_warm = indicators.get('is_warm', False)
        
        if is_warm and warmup_complete_at is None:
            warmup_complete_at = idx
            print(f"✅ Warmup complete at {timestamp}")
        
        # Get current regime
        current_regime = regime_engine.get_regime(symbol)
        
        # Signal Deduplication
        if current_date not in daily_signals:
            daily_signals[current_date] = {}
        
        signal = None
        strategy = None
        side = None
        
        # Cooldown check (5 minutes between signals for same symbol)
        last_proposal = last_proposal_time.get(symbol)
        can_generate_signal = True
        if last_proposal:
            time_since = (timestamp - last_proposal).total_seconds()
            if time_since < 300:  # 5 minute cooldown
                can_generate_signal = False
        
        if not can_generate_signal or not is_warm:
            # Skip signal generation but continue processing (exits, etc.)
            if (idx + 1) % 5000 == 0:
                print(f"📊 Progress: {(idx+1)/len(df)*100:.1f}%", flush=True)
            continue
        
        # -----------------------------------------------
        # STRATEGY 1: ORB (Opening Range Breakout)
        # PERMISSION: All Regimes EXCEPT Event Risk
        # -----------------------------------------------
        is_orb_window = (current_hour == 10) or (current_hour == 11 and current_minute < 30)
        
        if current_regime.value != 'EVENT_RISK' and is_orb_window and indicators.get('candle_count', 0) >= 30:
            orb_key = f"ORB_{current_date}"
            if orb_key not in daily_signals[current_date]:
                orb = engine.get_opening_range(symbol)
                if orb['complete']:
                    price_val = indicators['price']
                    velocity = indicators['volume_velocity']
                    
                    if price_val > orb['high'] and velocity > 1.5:
                        signal = 'ORB_BREAKOUT_BULL'
                        strategy = 'ORB'
                        side = 'OPEN'
                    elif price_val < orb['low'] and velocity > 1.5:
                        signal = 'ORB_BREAKOUT_BEAR'
                        strategy = 'ORB'
                        side = 'OPEN'
                    if signal:
                        daily_signals[current_date][orb_key] = True

        # -----------------------------------------------
        # STRATEGY 2: RANGE FARMER (Iron Condor)
        # PERMISSION: ONLY in LOW_VOL_CHOP
        # -----------------------------------------------
        if not signal and current_regime.value == 'LOW_VOL_CHOP' and current_hour == 13 and 0 <= current_minute < 5:
            farmer_key = f"FARMER_{current_date}"
            if farmer_key not in daily_signals[current_date]:
                adx = engine.get_adx(symbol)
                if adx is not None and adx < 20:
                    # Volume Profile Filter: Only enter if price is near POC (within $2)
                    poc = indicators.get('poc', 0)
                    current_price = indicators['price']
                    
                    if poc > 0 and abs(current_price - poc) < 2.00:
                        signal = 'IRON_CONDOR'
                        strategy = 'RANGE_FARMER'
                        side = 'OPEN'
                        daily_signals[current_date][farmer_key] = True

        # -----------------------------------------------
        # STRATEGY 3: SCALPER (0DTE)
        # PERMISSION: TRENDING or HIGH_VOL_EXPANSION
        # -----------------------------------------------
        if not signal and current_regime.value in ['TRENDING', 'HIGH_VOL_EXPANSION']:
            rsi_2 = engine.get_rsi(symbol, period=2)
            if rsi_2 is not None and (rsi_2 < 5 or rsi_2 > 95):
                if 9 <= current_hour < 16:  # Market hours
                    if rsi_2 < 5:
                        signal = 'SCALP_BULL_PUT'
                        strategy = 'SCALPER'
                        side = 'OPEN'
                    elif rsi_2 > 95:
                        # Don't short a strong uptrend
                        trend_strength = engine.get_adx(symbol)
                        if trend_strength is None or trend_strength <= 40:
                            signal = 'SCALP_BEAR_CALL'
                            strategy = 'SCALPER'
                            side = 'OPEN'

        # -----------------------------------------------
        # STRATEGY 4: TREND ENGINE (Enhanced with Market Structure S/R)
        # PERMISSION: ONLY in TRENDING
        # -----------------------------------------------
        if not signal and current_regime.value == 'TRENDING':
            trend = indicators['trend']
            rsi = indicators['rsi']
            flow = indicators['flow_state']
            
            # Market Structure (Support/Resistance via Volume)
            poc = indicators.get('poc', 0)
            vah = indicators.get('vah', 0)
            val = indicators.get('val', 0)
            current_price = indicators['price']
            
            if poc > 0 and flow != 'NEUTRAL':
                # --- BULLISH LOGIC ---
                if trend == 'UPTREND':
                    # Setup 1: The Breakout (Price > Resistance)
                    if current_price > vah and rsi < 60:
                        signal = 'BULL_PUT_SPREAD'
                        strategy = 'TREND_ENGINE'
                        side = 'OPEN'
                    # Setup 2: The Value Pullback (Price Retests Value)
                    elif current_price > poc and current_price < vah and rsi < 30:
                        signal = 'BULL_PUT_SPREAD'
                        strategy = 'TREND_ENGINE'
                        side = 'OPEN'
                
                # --- BEARISH LOGIC ---
                elif trend == 'DOWNTREND':
                    # Setup 1: The Breakdown (Price < Support)
                    if current_price < val and rsi > 40:
                        signal = 'BEAR_CALL_SPREAD'
                        strategy = 'TREND_ENGINE'
                        side = 'OPEN'
                    # Setup 2: The Value Rally (Price Retests Resistance)
                    elif current_price < poc and current_price > val and rsi > 70:
                        signal = 'BEAR_CALL_SPREAD'
                        strategy = 'TREND_ENGINE'
                        side = 'OPEN'

        # -----------------------------------------------
        # STRATEGY 5: IRON BUTTERFLY ("The Pin")
        # PERMISSION: CHOP Regime + High IV
        # -----------------------------------------------
        if not signal and current_regime.value == 'LOW_VOL_CHOP' and current_hour == 12:
            iv_rank = engine.get_iv_rank(symbol)
            if iv_rank and iv_rank > 50:
                poc = indicators.get('poc', 0)
                current_price = indicators['price']
                
                if poc > 0 and abs(current_price - poc) < 2.00:
                    signal = 'IRON_BUTTERFLY'
                    strategy = 'IRON_BUTTERFLY'
                    side = 'OPEN'

        # -----------------------------------------------
        # STRATEGY 6: RATIO SPREAD ("The Hedge")
        # PERMISSION: ANY Regime (Defense) + Low IV
        # -----------------------------------------------
        if not signal and current_minute == 30:  # Check once an hour
            iv_rank = engine.get_iv_rank(symbol)
            if iv_rank and iv_rank < 20:
                signal = 'RATIO_SPREAD'
                strategy = 'RATIO_SPREAD'
                side = 'OPEN'

        # Execute Signal
        if signal:
            last_signals[symbol] = {'signal': signal, 'timestamp': timestamp}
            last_proposal_time[symbol] = timestamp
            
            # Dynamic Sizing based on current equity (matches live system)
            size = accountant.get_trade_size(spread_width=5.0)
            
            # Create Trade
            trade = BacktestTrade(
                symbol, strategy, side, price, timestamp, 
                size, signal=signal, regime=current_regime.value
            )
            open_trades.append(trade)
            accountant.log_trade(trade)
            
            # Display Volume Profile diagnostics if available
            poc = indicators.get('poc', 0)
            vah = indicators.get('vah', 0)
            val = indicators.get('val', 0)
            if poc > 0:
                print(f"   📊 Vol Profile: POC=${poc:.2f} | VAH=${vah:.2f} | VAL=${val:.2f} | Price=${indicators['price']:.2f}")

        if (idx + 1) % 5000 == 0:
            print(f"📊 Progress: {(idx+1)/len(df)*100:.1f}%", flush=True)

    # Final Report
    print(accountant.get_summary())

if __name__ == "__main__":
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'SPY'
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    asyncio.run(run_backtest(symbol, days))
