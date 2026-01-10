# Code Breakdown - Exact Issues Found

## Issue #1: VIX Placeholder (CRITICAL)

**File:** `brain/src/market_feed.py`  
**Line:** 406

```python
# CURRENT (WRONG):
'context': {
    'vix': 15.0,  # ❌ PLACEHOLDER - Gatekeeper checks vix > 28, this never triggers
    ...
}

# NEEDED:
# Fetch real VIX from API every 60 seconds
# Options: Tradier REST API, Yahoo Finance, Alpha Vantage
```

**Gatekeeper Check (DOESN'T WORK):**
```typescript
// src/GatekeeperDO.ts:294
if (proposal.context.vix !== undefined && proposal.context.vix > 28) {
  return { status: 'REJECTED', ... };  // ❌ Never executes because vix is always 15.0
}
```

---

## Issue #2: RSI Calculation (CRITICAL)

**File:** `brain/src/alpha_engine.py`  
**Lines:** 196-216

```python
# CURRENT (WRONG):
def _calculate_rsi(self, symbol: str, period: int = 14) -> float:
    closes = self.candles[symbol]['close'].tail(period + 1)
    deltas = closes.diff()
    gains = deltas.where(deltas > 0, 0)
    losses = -deltas.where(deltas < 0, 0)
    
    avg_gain = gains.tail(period).mean()  # ❌ WRONG: Simple mean every time
    avg_loss = losses.tail(period).mean()  # ❌ WRONG: No memory, recalculates from scratch
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

# PROBLEM:
# - Recalculates averages from scratch every time (no smoothing)
# - Throws away previous average history
# - RSI oscillates wildly, not smooth
# - Standard RSI requires Wilder's Smoothing:
#   First: avg = mean(first 14 periods)
#   Next:  avg = ((prev_avg * 13) + new_value) / 14

# NEEDED:
class AlphaEngine:
    def __init__(self):
        self.rsi_state = {}  # Store: {symbol: {'avg_gain': float, 'avg_loss': float, 'last_close': float}}
    
    def _calculate_rsi(self, symbol: str, period: int = 14) -> float:
        if len(self.candles[symbol]) < period + 1:
            return 50.0
        
        # Get latest close
        current_close = self.candles[symbol]['close'].iloc[-1]
        
        # Initialize or update state
        if symbol not in self.rsi_state:
            # First calculation: simple average
            closes = self.candles[symbol]['close'].tail(period + 1)
            deltas = closes.diff().dropna()
            gains = deltas.where(deltas > 0, 0)
            losses = -deltas.where(deltas < 0, 0)
            avg_gain = gains.tail(period).mean()
            avg_loss = losses.tail(period).mean()
            self.rsi_state[symbol] = {
                'avg_gain': avg_gain,
                'avg_loss': avg_loss,
                'last_close': current_close
            }
        else:
            # Subsequent: Wilder's Smoothing
            prev_close = self.rsi_state[symbol]['last_close']
            change = current_close - prev_close
            gain = max(change, 0)
            loss = max(-change, 0)
            
            # Wilder's formula
            self.rsi_state[symbol]['avg_gain'] = (
                (self.rsi_state[symbol]['avg_gain'] * (period - 1) + gain) / period
            )
            self.rsi_state[symbol]['avg_loss'] = (
                (self.rsi_state[symbol]['avg_loss'] * (period - 1) + loss) / period
            )
            self.rsi_state[symbol]['last_close'] = current_close
        
        # Calculate RSI
        avg_gain = self.rsi_state[symbol]['avg_gain']
        avg_loss = self.rsi_state[symbol]['avg_loss']
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
```

---

## Issue #3: SMA on Partial Data (HIGH)

**File:** `brain/src/alpha_engine.py`  
**Lines:** 183-194

```python
# CURRENT (MISLEADING):
def _calculate_sma(self, symbol: str, period: int = 200) -> float:
    if self.candles[symbol].empty or len(self.candles[symbol]) < period:
        if not self.candles[symbol].empty:
            return self.candles[symbol]['close'].mean()  # ❌ Returns mean(50) but claims SMA(200)
        # ...
    return self.candles[symbol]['close'].tail(period).mean()

# PROBLEM:
# - After 50 minutes: Returns mean(50 candles) but code treats it as SMA(200)
# - Trend detection uses this: if price > sma: 'UPTREND'
# - Gives false trend signals during first 200 minutes

# NEEDED:
def _calculate_sma(self, symbol: str, period: int = 200) -> Tuple[Optional[float], bool]:
    """
    Returns: (sma_value, is_full_period)
    """
    if self.candles[symbol].empty:
        return None, False
    
    candle_count = len(self.candles[symbol])
    
    if candle_count < period:
        return None, False  # ❌ Don't return partial data as if it's full SMA
    
    sma = self.candles[symbol]['close'].tail(period).mean()
    return sma, True

# Then update get_trend():
def get_trend(self, symbol: str) -> Tuple[str, Optional[float], bool]:
    price = self.get_current_price(symbol)
    sma, is_full = self._calculate_sma(symbol, period=200)
    
    if sma is None:
        return 'INSUFFICIENT_DATA', None, False
    
    trend = 'UPTREND' if price > sma else 'DOWNTREND'
    return trend, sma, is_full
```

---

## Issue #4: Volume Velocity Stale Data (MEDIUM)

**File:** `brain/src/alpha_engine.py`  
**Lines:** 175-179

```python
# CURRENT (STALE):
if not self.candles[symbol].empty:
    current_volume = self.candles[symbol]['volume'].iloc[-1]  # ❌ Last CLOSED candle (up to 1 min old)
else:
    current_volume = self.current_bars.get(symbol, {}).get('volume', 0)

# PROBLEM:
# Uses volume from last closed candle (could be 1 minute stale)
# Should use current accumulating bar for real-time velocity

# NEEDED:
# Prioritize current bar (real-time)
if symbol in self.current_bars and self.current_bars[symbol].get('volume', 0) > 0:
    current_volume = self.current_bars[symbol]['volume']  # ✅ Real-time
elif not self.candles[symbol].empty:
    current_volume = self.candles[symbol]['volume'].iloc[-1]  # Fallback to last closed
else:
    current_volume = 0
```

---

## Issue #5: Flow State Flip-Flop (MEDIUM)

**File:** `brain/src/alpha_engine.py`  
**Lines:** 247-252

```python
# CURRENT (FLIP-FLOP RISK):
elif price > vwap and volume_velocity > 1.2:
    flow_state = 'RISK_ON'
elif price < vwap and volume_velocity > 1.2:
    flow_state = 'RISK_OFF'

# PROBLEM:
# In choppy markets, price oscillates around VWAP
# Flow state flips every tick: RISK_ON → RISK_OFF → RISK_ON

# NEEDED:
# Option 1: Add buffer zone
BUFFER = 0.001  # 0.1%
if price > vwap * (1 + BUFFER) and volume_velocity > 1.2:
    flow_state = 'RISK_ON'
elif price < vwap * (1 - BUFFER) and volume_velocity > 1.2:
    flow_state = 'RISK_OFF'
else:
    # Keep previous state if in buffer zone (requires state tracking)
    flow_state = 'NEUTRAL'

# Option 2: Use smoothed price (5-min average) instead of raw tick
# Option 3: Require state to persist for N seconds before triggering signals
```

---

## Summary Table

| Issue | Severity | File | Line | Status |
|-------|----------|------|------|--------|
| VIX Placeholder | 🔴 CRITICAL | `market_feed.py` | 406 | ❌ Broken risk check |
| RSI Calculation | 🔴 CRITICAL | `alpha_engine.py` | 196-216 | ❌ Wrong formula |
| SMA Partial Data | 🟡 HIGH | `alpha_engine.py` | 183-194 | ⚠️ Misleading |
| Volume Velocity | 🟡 MEDIUM | `alpha_engine.py` | 175-179 | ⚠️ Stale data |
| Flow State Flip | 🟡 MEDIUM | `alpha_engine.py` | 247-252 | ⚠️ No hysteresis |

---

## Next Steps

1. **Read the full audit:** `AUDIT_FULL_REPORT.md`
2. **Fix critical issues first:** VIX + RSI
3. **Test calculations:** Verify against known good values
4. **Then fix high/medium issues**
5. **Sandbox test for 1 week minimum**
