"""
Alpha Engine
Calculates "Tier A" Flow metrics from market data
Determines RISK_ON, RISK_OFF, or NEUTRAL flow states
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from collections import defaultdict


class AlphaEngine:
    """Calculates flow state, trend, and technical indicators from market data"""

    def __init__(self, lookback_minutes: int = 60):
        """
        Initialize the Alpha Engine
        
        Args:
            lookback_minutes: Number of minutes of historical data to keep (default: 60)
        """
        self.lookback_minutes = lookback_minutes
        # Store 1-minute candles for each symbol
        # Structure: {symbol: DataFrame with columns [timestamp, open, high, low, close, volume]}
        self.candles: Dict[str, pd.DataFrame] = defaultdict(lambda: pd.DataFrame())
        
        # Current tick data for bar aggregation
        # Structure: {symbol: {'price': float, 'volume': int, 'bar_start': datetime}}
        self.current_bars: Dict[str, Dict] = defaultdict(dict)
        
        # Session VWAP tracking (resets daily)
        self.session_vwap: Dict[str, float] = {}
        self.session_volume: Dict[str, float] = {}
        self.session_pv: Dict[str, float] = {}  # Price * Volume sum
        
        # Session start time (for VWAP calculation)
        self.session_start: Optional[datetime] = None
        
        # VIX state (updated by external poller)
        self.current_vix: Optional[float] = None
        self.vix_timestamp: Optional[datetime] = None
        
        # RSI state for Wilder's smoothing (per symbol)
        # Structure: {symbol: {'avg_gain': float, 'avg_loss': float, 'last_close': float, 
        #                      'last_bar_timestamp': datetime, 'initialized': bool}}
        self.rsi_state: Dict[str, Dict] = defaultdict(lambda: {
            'avg_gain': None,
            'avg_loss': None,
            'last_close': None,
            'last_bar_timestamp': None,
            'initialized': False
        })

    def _get_session_start(self, current_time: datetime) -> datetime:
        """Get the session start time (market open: 9:30 AM ET)"""
        # Simplified: Assume market opens at 9:30 AM ET
        # For production, use proper timezone handling
        session_start = current_time.replace(hour=9, minute=30, second=0, microsecond=0)
        if current_time.hour < 9 or (current_time.hour == 9 and current_time.minute < 30):
            # Before market open, use previous day
            session_start = session_start - timedelta(days=1)
        return session_start

    def _is_new_session(self, current_time: datetime) -> bool:
        """Check if we've crossed into a new trading session"""
        if self.session_start is None:
            return True
        
        new_session_start = self._get_session_start(current_time)
        return new_session_start.date() > self.session_start.date()

    def _reset_session(self, current_time: datetime):
        """Reset session metrics for a new trading day"""
        self.session_start = self._get_session_start(current_time)
        self.session_vwap = {}
        self.session_pv = {}
        self.session_volume = {}
        # Reset RSI state on new session (optional - could maintain across sessions)
        # For now, reset to recalculate from fresh session data
        self.rsi_state.clear()

    def update(self, symbol: str, price: float, volume: int, timestamp: Optional[datetime] = None):
        """
        Update the engine with a new tick
        
        Args:
            symbol: Symbol (e.g., 'SPY', 'QQQ')
            price: Current price
            volume: Volume for this tick
            timestamp: Timestamp (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.now()

        # Check for new session
        if self._is_new_session(timestamp):
            self._reset_session(timestamp)

        # Initialize current bar if needed
        if symbol not in self.current_bars:
            bar_start = timestamp.replace(second=0, microsecond=0)
            self.current_bars[symbol] = {
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'volume': volume,
                'bar_start': bar_start,
                'pv_sum': price * volume  # Price * Volume for VWAP
            }
        else:
            bar = self.current_bars[symbol]
            bar['high'] = max(bar['high'], price)
            bar['low'] = min(bar['low'], price)
            bar['close'] = price
            bar['volume'] += volume
            bar['pv_sum'] += price * volume

        # Update session VWAP metrics
        if symbol not in self.session_pv:
            self.session_pv[symbol] = 0.0
            self.session_volume[symbol] = 0.0

        self.session_pv[symbol] += price * volume
        self.session_volume[symbol] += volume

        # Check if we should close the current bar (new minute)
        bar_start_minute = self.current_bars[symbol]['bar_start'].minute
        current_minute = timestamp.minute
        
        if current_minute != bar_start_minute or timestamp.hour != self.current_bars[symbol]['bar_start'].hour:
            # Close the bar and add to candles
            self._close_bar(symbol, timestamp)

            # Start new bar
            new_bar_start = timestamp.replace(second=0, microsecond=0)
            self.current_bars[symbol] = {
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'volume': volume,
                'bar_start': new_bar_start,
                'pv_sum': price * volume
            }

    def _close_bar(self, symbol: str, timestamp: datetime):
        """Close the current 1-minute bar and add to candles DataFrame"""
        bar = self.current_bars[symbol]
        
        new_row = pd.DataFrame([{
            'timestamp': bar['bar_start'],
            'open': bar['open'],
            'high': bar['high'],
            'low': bar['low'],
            'close': bar['close'],
            'volume': bar['volume']
        }])

        if self.candles[symbol].empty:
            self.candles[symbol] = new_row
        else:
            self.candles[symbol] = pd.concat([self.candles[symbol], new_row], ignore_index=True)

        # Trim to lookback window
        cutoff_time = timestamp - timedelta(minutes=self.lookback_minutes)
        self.candles[symbol] = self.candles[symbol][
            self.candles[symbol]['timestamp'] >= cutoff_time
        ].reset_index(drop=True)
        
        # Reset RSI state when new bar closes (so it recalculates on next get_rsi call)
        # This ensures RSI updates even if close price is unchanged (gain=0, loss=0)
        if symbol in self.rsi_state and self.rsi_state[symbol]['initialized']:
            # Mark that we need to update state on next RSI calculation
            # We track by checking if the last_close matches the current close
            # If it matches, it means we already processed this bar
            # If it doesn't match (or this is first bar after reset), we need to update
            pass  # State update handled in _calculate_rsi based on close price change

    def _calculate_vwap(self, symbol: str) -> float:
        """Calculate Volume Weighted Average Price for the session"""
        if symbol not in self.session_pv or self.session_volume[symbol] == 0:
            return 0.0
        
        vwap = self.session_pv[symbol] / self.session_volume[symbol]
        self.session_vwap[symbol] = vwap
        return vwap

    def _calculate_volume_velocity(self, symbol: str) -> float:
        """
        Calculate volume velocity (current volume / 20-period average)
        Uses current accumulating bar volume for real-time calculation
        """
        if self.candles[symbol].empty or len(self.candles[symbol]) < 20:
            return 1.0  # Default to neutral if not enough data

        recent_volumes = self.candles[symbol]['volume'].tail(20)
        avg_volume = recent_volumes.mean()
        
        if avg_volume == 0:
            return 1.0

        # Prioritize current accumulating bar (real-time) over last closed candle
        if symbol in self.current_bars and self.current_bars[symbol].get('volume', 0) > 0:
            current_volume = self.current_bars[symbol]['volume']  # Real-time current bar
        elif not self.candles[symbol].empty:
            current_volume = self.candles[symbol]['volume'].iloc[-1]  # Fallback to last closed candle
        else:
            current_volume = 0

        return current_volume / avg_volume if avg_volume > 0 else 1.0

    def _calculate_sma(self, symbol: str, period: int = 200) -> Optional[float]:
        """
        Calculate Simple Moving Average
        
        Returns:
            SMA value if enough data, None if insufficient data
            DO NOT return partial data as if it's a full SMA - this causes false trend signals
        """
        if self.candles[symbol].empty:
            return None
        
        if len(self.candles[symbol]) < period:
            # Insufficient data - return None instead of misleading partial mean
            return None

        return float(self.candles[symbol]['close'].tail(period).mean())

    def _calculate_rsi(self, symbol: str, period: int = 14) -> float:
        """
        Calculate Relative Strength Index using Wilder's Smoothing
        
        Proper RSI calculation:
        - First calculation: Simple average of first 14 periods
        - Subsequent: NewAvg = (OldAvg * (period - 1) + NewValue) / period
        
        This maintains state between calls for proper smoothing
        """
        if self.candles[symbol].empty or len(self.candles[symbol]) < period + 1:
            return 50.0  # Neutral RSI if not enough data

        # Get close prices and last bar timestamp
        closes = self.candles[symbol]['close']
        timestamps = self.candles[symbol]['timestamp']
        current_close = float(closes.iloc[-1])
        current_bar_timestamp = timestamps.iloc[-1]
        
        # Get or initialize RSI state
        rsi_state = self.rsi_state[symbol]
        
        # Check if we need to update state (new bar closed or first calculation)
        # Track by timestamp to handle cases where close price is unchanged
        needs_update = False
        if not rsi_state['initialized']:
            needs_update = True
        elif rsi_state['last_bar_timestamp'] is None:
            needs_update = True
        elif rsi_state['last_bar_timestamp'] != current_bar_timestamp:
            # New bar closed (different timestamp) - update state
            # This handles both changed and unchanged close prices
            needs_update = True
        
        if needs_update:
            if not rsi_state['initialized']:
                # First calculation: Simple average of first period values
                if len(closes) < period + 1:
                    return 50.0
                
                initial_closes = closes.tail(period + 1)
                deltas = initial_closes.diff().dropna()
                
                gains = deltas.where(deltas > 0, 0.0)
                losses = -deltas.where(deltas < 0, 0.0)
                
                # Simple average for initial calculation
                avg_gain = float(gains.tail(period).mean())
                avg_loss = float(losses.tail(period).mean())
                
                rsi_state['avg_gain'] = avg_gain
                rsi_state['avg_loss'] = avg_loss
                rsi_state['last_close'] = current_close
                rsi_state['last_bar_timestamp'] = current_bar_timestamp
                rsi_state['initialized'] = True
            else:
                # Subsequent calculation: Wilder's Smoothing (when new bar closes)
                last_close = rsi_state['last_close']
                change = current_close - last_close
                
                gain = max(change, 0.0)
                loss = max(-change, 0.0)
                
                # Wilder's formula: NewAvg = (OldAvg * (period - 1) + NewValue) / period
                # Note: Even if change=0 (same close), we still update (gain=0, loss=0)
                # This ensures we account for the time period even if price didn't move
                rsi_state['avg_gain'] = (rsi_state['avg_gain'] * (period - 1) + gain) / period
                rsi_state['avg_loss'] = (rsi_state['avg_loss'] * (period - 1) + loss) / period
                rsi_state['last_close'] = current_close
                rsi_state['last_bar_timestamp'] = current_bar_timestamp
        
        # Calculate RSI
        avg_gain = rsi_state['avg_gain']
        avg_loss = rsi_state['avg_loss']
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return float(rsi)

    def get_current_price(self, symbol: str) -> float:
        """Get the current price for a symbol"""
        if not self.candles[symbol].empty:
            return float(self.candles[symbol]['close'].iloc[-1])
        elif symbol in self.current_bars:
            return float(self.current_bars[symbol]['close'])
        else:
            return 0.0

    def get_flow_state(self, symbol: str) -> Tuple[str, Dict]:
        """
        Get the flow state for a symbol
        
        Returns:
            Tuple of (flow_state, metadata)
            flow_state: 'RISK_ON', 'RISK_OFF', or 'NEUTRAL'
            metadata: Dict with details (vwap, volume_velocity, price, etc.)
        """
        if self.candles[symbol].empty and symbol not in self.current_bars:
            return 'NEUTRAL', {'reason': 'No data available'}

        price = self.get_current_price(symbol)
        vwap = self._calculate_vwap(symbol)
        volume_velocity = self._calculate_volume_velocity(symbol)

        # Flow state logic with buffer to prevent flip-flop
        VWAP_BUFFER = 0.001  # 0.1% buffer to prevent oscillation
        
        if vwap == 0:
            flow_state = 'NEUTRAL'
            reason = 'VWAP not available'
        elif price > vwap * (1 + VWAP_BUFFER) and volume_velocity > 1.2:
            flow_state = 'RISK_ON'
            reason = f'Price > VWAP ({price:.2f} > {vwap * (1 + VWAP_BUFFER):.2f}) and Vol Velocity > 1.2 ({volume_velocity:.2f})'
        elif price < vwap * (1 - VWAP_BUFFER) and volume_velocity > 1.2:
            flow_state = 'RISK_OFF'
            reason = f'Price < VWAP ({price:.2f} < {vwap * (1 - VWAP_BUFFER):.2f}) and Vol Velocity > 1.2 ({volume_velocity:.2f})'
        else:
            flow_state = 'NEUTRAL'
            reason = f'Vol Velocity {volume_velocity:.2f} <= 1.2 or price within {VWAP_BUFFER*100:.1f}% of VWAP (buffer zone)'

        metadata = {
            'price': price,
            'vwap': vwap,
            'volume_velocity': volume_velocity,
            'reason': reason,
            'candle_count': len(self.candles[symbol])
        }

        return flow_state, metadata

    def get_trend(self, symbol: str) -> Tuple[str, Optional[float]]:
        """
        Get the trend for a symbol
        
        Returns:
            Tuple of (trend, sma_value)
            trend: 'UPTREND', 'DOWNTREND', or 'INSUFFICIENT_DATA' if SMA unavailable
            sma_value: The 200-period SMA value, or None if insufficient data
        """
        price = self.get_current_price(symbol)
        sma = self._calculate_sma(symbol, period=200)

        if sma is None:
            return 'INSUFFICIENT_DATA', None

        if sma == 0:
            return 'NEUTRAL', 0.0

        trend = 'UPTREND' if price > sma else 'DOWNTREND'
        return trend, sma

    def get_rsi(self, symbol: str, period: int = 14) -> float:
        """Get RSI for a symbol"""
        return self._calculate_rsi(symbol, period)

    def set_vix(self, vix_value: float, timestamp: Optional[datetime] = None):
        """
        Update VIX value (called by VIX poller)
        
        Args:
            vix_value: Current VIX value
            timestamp: When VIX was fetched (defaults to now)
        """
        self.current_vix = vix_value
        self.vix_timestamp = timestamp or datetime.now()

    def get_vix(self) -> Optional[float]:
        """Get current VIX value"""
        return self.current_vix

    def get_indicators(self, symbol: str) -> Dict:
        """
        Get all indicators for a symbol in one call
        
        Returns:
            Dict with flow_state, trend, rsi, vix, and all metadata
            Note: trend will be 'INSUFFICIENT_DATA' if < 200 candles
        """
        flow_state, flow_metadata = self.get_flow_state(symbol)
        trend, sma = self.get_trend(symbol)
        rsi = self.get_rsi(symbol)
        vix = self.get_vix()

        return {
            'symbol': symbol,
            'flow_state': flow_state,
            'trend': trend,
            'rsi': rsi,
            'vix': vix,  # Real VIX value (None if not fetched yet)
            'price': flow_metadata['price'],
            'vwap': flow_metadata['vwap'],
            'volume_velocity': flow_metadata['volume_velocity'],
            'sma_200': sma,  # None if < 200 candles
            'candle_count': flow_metadata['candle_count'],
            'is_warm': sma is not None and vix is not None  # Warmup status
        }

