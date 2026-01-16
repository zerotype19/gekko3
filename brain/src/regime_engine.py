"""
Regime Engine
The Governance Layer: Determines the global market state.
Strategies must ask the Regime Engine for permission before firing.
"""

from enum import Enum
from datetime import datetime
from typing import Optional, List
from src.alpha_engine import AlphaEngine

class MarketRegime(Enum):
    COMPRESSED = "COMPRESSED"           # VIX < 13, Range < 0.5%. BEST for Calendar Spreads (Long Vol).
    LOW_VOL_CHOP = "LOW_VOL_CHOP"       # VIX 13-20, Low ADX. BEST for Iron Condors (Short Vol).
    TRENDING = "TRENDING"               # High ADX (>25) OR Breakout Flow. BEST for Trend Engine / Ratios.
    HIGH_VOL_EXPANSION = "HIGH_VOL_EXPANSION" # VIX > 25. BEST for Short Scalps / Hedging.
    EVENT_RISK = "EVENT_RISK"           # FOMC/CPI Days. No New Entries.

class RegimeEngine:
    def __init__(self, alpha_engine: AlphaEngine):
        self.alpha_engine = alpha_engine
        
        # Manual Override for Event Days
        # Ideally, this should be fetched from an external config or API
        self.restricted_dates: List[str] = [
            '2026-01-31', # FOMC Placeholder
            '2026-02-13', # CPI Placeholder
        ]

    def get_regime(self, symbol: str = 'SPY') -> MarketRegime:
        """
        Determines the current market regime based on SPY (The Market Proxy).
        """
        # 1. Event Risk Check (Hard Stop)
        today_str = datetime.now().strftime('%Y-%m-%d')
        if today_str in self.restricted_dates:
            return MarketRegime.EVENT_RISK
        
        # 2. Get Core Metrics from Alpha Engine
        indicators = self.alpha_engine.get_indicators(symbol)
        vix = indicators.get('vix')
        adx = self.alpha_engine.get_adx(symbol)
        iv_rank = self.alpha_engine.get_iv_rank(symbol)
        
        # Volume/Price Metrics
        price = indicators.get('price', 0)
        vwap = indicators.get('vwap', 0)
        volume_velocity = indicators.get('volume_velocity', 1.0)
        
        # Safety: Default to defensive CHOP if data missing
        if vix is None or price == 0:
            return MarketRegime.LOW_VOL_CHOP

        # 3. Determine Regime (Priority Order Matters)
        
        # A. High Volatility / Crisis (VIX > 25)
        # Defense is priority. No Spreads. Hedging only.
        if vix > 25:
            return MarketRegime.HIGH_VOL_EXPANSION
            
        # B. Trending Market (The "Grind" or "Breakout")
        # Logic: High Trend Strength (ADX) OR Strong Momentum (Price vs VWAP + Volume)
        # Note: We catch trends even if VIX is low (Grinding Bull)
        is_strong_trend = (adx is not None and adx > 25)
        is_breakout = (volume_velocity > 1.5 and abs(price - vwap) / vwap > 0.003)
        
        if is_strong_trend or is_breakout:
            return MarketRegime.TRENDING
            
        # C. Compression (The "Coil")
        # Logic: Very Low VIX (< 13) AND Low Trend.
        # This is where we BUY volatility (Calendars), not sell it.
        # Selling Iron Condors here is dangerous (Gamma explosion risk).
        if vix < 13.5 and adx < 20:
            return MarketRegime.COMPRESSED
            
        # D. Low Volatility / Chop (The "Zone")
        # Default state: Normal VIX (13-25), No Trend.
        # Safe for Premium Selling (Iron Condors).
        return MarketRegime.LOW_VOL_CHOP
