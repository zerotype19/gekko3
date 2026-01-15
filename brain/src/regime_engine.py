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
    LOW_VOL_CHOP = "LOW_VOL_CHOP"           # VIX < 25, Low ADX. Best for Iron Condors.
    TRENDING = "TRENDING"                   # High ADX (>25) or VIX 15-25. Best for Trend Engine.
    HIGH_VOL_EXPANSION = "HIGH_VOL_EXPANSION" # VIX > 25. Best for Short Scalps / Hedging.
    EVENT_RISK = "EVENT_RISK"               # FOMC/CPI Days. No New Entries.

class RegimeEngine:
    def __init__(self, alpha_engine: AlphaEngine):
        self.alpha_engine = alpha_engine
        
        # Manual Override for Event Days (Populate this list manually or via API)
        # Format: 'YYYY-MM-DD'
        self.restricted_dates: List[str] = [
            '2024-01-31', # FOMC Example
            '2024-02-13', # CPI Example
        ]

    def get_regime(self, symbol: str = 'SPY') -> MarketRegime:
        """
        Determines the current market regime based on SPY (The Market Proxy).
        """
        # 1. Event Risk Check (FOMC/CPI Days - No New Entries)
        today_str = datetime.now().strftime('%Y-%m-%d')
        if today_str in self.restricted_dates:
            return MarketRegime.EVENT_RISK
        
        # 2. Get Core Metrics
        indicators = self.alpha_engine.get_indicators(symbol)
        vix = indicators.get('vix')
        adx = self.alpha_engine.get_adx(symbol)
        
        # Safety: If VIX is not yet loaded, default to defensive CHOP
        if vix is None:
            return MarketRegime.LOW_VOL_CHOP

        # 3. Determine Regime
        
        # A. High Volatility / Expansion (Crisis or Correction)
        if vix > 25:
            return MarketRegime.HIGH_VOL_EXPANSION
            
        # B. Trending Market (Healthy Bull or Bear)
        # CRITICAL FIX: If ADX > 25, we are trending, even if VIX is low (Grinding Bull Market)
        # Example: VIX=12, ADX=40 → TRENDING (not LOW_VOL_CHOP)
        # This prevents selling Iron Condors in front of a moving train
        # We check VIX < 25 implicitly because the check above failed
        if adx is not None and adx > 25:
            return MarketRegime.TRENDING
            
        # C. Low Volatility / Chop (The "Grind")
        # Default state: Low VIX (< 25) AND Low ADX (< 25)
        # This is the safe state for premium selling (Iron Condors)
        return MarketRegime.LOW_VOL_CHOP
