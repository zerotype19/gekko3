# 🔥 Gekko3 Code Audit - Full Report

**Date:** 2026-01-10  
**Status:** ❌ **NOT PRODUCTION READY**  
**Risk Level:** 🔴 **HIGH - Critical Issues Found**

---

## Executive Summary

The codebase contains **2 CRITICAL** bugs that render risk checks ineffective, plus **3 HIGH/MEDIUM** issues that affect signal quality. These must be fixed before any production trading.

---

## 🔴 CRITICAL ISSUES (Must Fix Immediately)

### Issue #1: VIX Placeholder Breaks Volatility Veto

**Location:** `brain/src/market_feed.py:406`

**Code:**
```python
'context': {
    'vix': 15.0,  # Placeholder - fetch actual VIX in production
    ...
}
```

**Gatekeeper Check:** `src/GatekeeperDO.ts:294`
```typescript
if (proposal.context.vix !== undefined && proposal.context.vix > 28) {
  return { status: 'REJECTED', rejectionReason: `VIX too high: ${proposal.context.vix} (max: 28)` };
}
```

**Problem:**
- Brain **always** sends `vix: 15.0`
- Gatekeeper checks `if vix > 28` → **NEVER triggers** (15.0 < 28)
- **Volatility Veto is completely bypassed**
- System will trade during high volatility (VIX > 28) when it should reject

**Impact:** 🔴 **CRITICAL** - Trading in dangerous market conditions

**Fix Required:**
```python
# Need to implement VIX fetcher
# Option 1: Tradier REST API (if available)
# Option 2: Yahoo Finance API
# Option 3: Alpha Vantage
# Refresh every 60 seconds
```

---

### Issue #2: RSI Calculation is Mathematically Incorrect

**Location:** `brain/src/alpha_engine.py:196-216`

**Current (WRONG) Code:**
```python
def _calculate_rsi(self, symbol: str, period: int = 14) -> float:
    closes = self.candles[symbol]['close'].tail(period + 1)
    deltas = closes.diff()
    gains = deltas.where(deltas > 0, 0)
    losses = -deltas.where(deltas < 0, 0)
    
    avg_gain = gains.tail(period).mean()  # ❌ WRONG
    avg_loss = losses.tail(period).mean()  # ❌ WRONG
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi
```

**Problem:**
- Uses **simple arithmetic mean** for averages
- RSI requires **Wilder's Smoothing** (exponential-like)
- Standard RSI formula uses:
  - First calculation: Simple average of gains/losses
  - Subsequent: `NewAvg = ((OldAvg * (period - 1)) + NewValue) / period`

**Example:**
- Current: Calculates `mean([1, 2, 3]) = 2.0` every time (throws away history)
- Correct: First = `2.0`, then `NewAvg = ((2.0 * 13) + 4) / 14 = 2.14` (smoothed)

**Impact:** 🔴 **CRITICAL** - RSI values are wrong, signals unreliable

**Current Behavior:**
- RSI oscillates wildly (recalculates from scratch each time)
- No memory of previous averages
- Signals based on RSI < 30 or > 70 are **unreliable**

**Fix Required:**
- Store previous `avg_gain` and `avg_loss` per symbol
- Use Wilder's smoothing formula
- Verify against TradingView/other RSI calculators

---

## 🟡 HIGH PRIORITY ISSUES

### Issue #3: SMA on Partial Data Gives False Trend Signals

**Location:** `brain/src/alpha_engine.py:183-194`

**Code:**
```python
def _calculate_sma(self, symbol: str, period: int = 200) -> float:
    if self.candles[symbol].empty or len(self.candles[symbol]) < period:
        if not self.candles[symbol].empty:
            return self.candles[symbol]['close'].mean()  # ❌ Returns mean(50) but claims it's SMA(200)
```

**Problem:**
- After 50 minutes: Returns `mean(50 candles)` but labeled as "SMA(200)"
- Trend detection uses this: `trend = 'UPTREND' if price > sma else 'DOWNTREND'`
- **False trend signals for first 200 minutes (3+ hours)**

**Example:**
- Minute 50: Price = 450, SMA = mean(50 candles) = 449.5 → "UPTREND"
- Minute 200: Price = 450, SMA = mean(200 candles) = 451.0 → "DOWNTREND"
- **Trend flips incorrectly** because SMA wasn't actually SMA(200) before

**Impact:** 🟡 **HIGH** - Wrong trend signals during startup period

**Fix Required:**
- Return `None` or special value if `len(candles) < period`
- Only calculate trend when full period available
- Add `trend_quality: 'PARTIAL' | 'FULL'` flag

---

### Issue #4: Volume Velocity Uses Stale Data (1-minute lag)

**Location:** `brain/src/alpha_engine.py:175-179`

**Code:**
```python
# Use the most recent bar's volume, or current bar if available
if not self.candles[symbol].empty:
    current_volume = self.candles[symbol]['volume'].iloc[-1]  # ❌ Last CLOSED candle
else:
    current_volume = self.current_bars.get(symbol, {}).get('volume', 0)
```

**Problem:**
- `current_volume` comes from **last closed candle** (up to 1 minute old)
- Should use **current accumulating bar** for real-time velocity
- Flow state decisions based on slightly stale data

**Impact:** 🟡 **MEDIUM** - Minor timing lag, not critical but suboptimal

**Fix:**
```python
# Use current bar if available (real-time)
if symbol in self.current_bars and self.current_bars[symbol].get('volume', 0) > 0:
    current_volume = self.current_bars[symbol]['volume']
elif not self.candles[symbol].empty:
    current_volume = self.candles[symbol]['volume'].iloc[-1]  # Fallback
```

---

### Issue #5: Flow State Flip-Flop in Choppy Markets

**Location:** `brain/src/alpha_engine.py:247-252`

**Code:**
```python
elif price > vwap and volume_velocity > 1.2:
    flow_state = 'RISK_ON'
elif price < vwap and volume_velocity > 1.2:
    flow_state = 'RISK_OFF'
```

**Problem:**
- In choppy markets, price oscillates around VWAP
- Flow state could flip **every tick** (RISK_ON → RISK_OFF → RISK_ON)
- Example: Price 450.00, VWAP 450.01 → RISK_OFF; Price 450.02 → RISK_ON; Price 450.00 → RISK_OFF
- No hysteresis or smoothing

**Impact:** 🟡 **MEDIUM** - Signal noise, but rate-limited so won't spam

**Fix Options:**
1. Add buffer: `price > vwap * 1.001` (0.1% buffer zone)
2. Use smoothed price (5-minute average) instead of raw tick
3. Require state to persist for N seconds before triggering signals

---

## 🟢 LOW PRIORITY (Verify/Monitor)

### Issue #6: Memory Leak Potential in DataFrame Operations

**Location:** `brain/src/alpha_engine.py:144-153`

**Analysis:**
- `pd.concat([df, new_row])` creates new DataFrame (old one in memory until GC)
- With high-frequency ticks, temporary memory spikes possible
- Trim logic looks correct, but should monitor

**Recommendation:** Monitor memory over 24 hours, consider pre-allocated array

---

### Issue #7: Timezone Handling in Session Reset

**Location:** `brain/src/alpha_engine.py:41-49`

**Analysis:**
- Uses `datetime.now()` without timezone
- Assumes server timezone matches ET
- Could cause VWAP reset at wrong time

**Recommendation:** Verify all timestamps use `ZoneInfo("America/New_York")`

---

## Code Review: Alpha Engine (`alpha_engine.py`)

### ✅ **GOOD:**
- Rolling window trimming (Lines 149-153) - looks correct
- Session VWAP tracking structure is sound
- Bar aggregation logic is reasonable

### ❌ **BAD:**
- RSI calculation (Lines 196-216) - **WRONG FORMULA**
- SMA partial data handling (Lines 185-188) - **MISLEADING**
- Volume velocity stale data (Lines 175-179) - **SUBOPTIMAL**
- Flow state no hysteresis (Lines 247-252) - **FLIP-FLOP RISK**

### ⚠️ **UNCERTAIN:**
- Memory efficiency of `pd.concat` in tight loop
- Timezone handling throughout (uses `datetime.now()` in places)

---

## Code Review: Market Feed (`market_feed.py`)

### ✅ **GOOD:**
- Session creation via HTTP first (correct)
- Rate limiting logic (1 per minute)
- Signal deduplication (tracks last signal)

### ❌ **BAD:**
- VIX placeholder `15.0` (Line 406) - **CRITICAL**
- Mock option strikes (known, but will cause rejections)
- No VIX fetching mechanism

---

## Action Items

### 🔴 **Before Any Trading:**
1. [ ] **Fix VIX:** Implement REST API poller (Tradier/Yahoo/Alpha Vantage)
2. [ ] **Fix RSI:** Implement proper Wilder's smoothing with state tracking

### 🟡 **Before Production:**
3. [ ] **Fix SMA:** Return None for partial data, add quality flag
4. [ ] **Fix Volume Velocity:** Use current bar volume
5. [ ] **Fix Flow State:** Add hysteresis/buffer to prevent flip-flop

### 🟢 **Ongoing:**
6. [ ] Monitor memory usage over 24 hours
7. [ ] Verify timezone handling with actual ET market times
8. [ ] Integrate real option chain API (currently documented limitation)

---

## Testing Requirements

Before declaring any component "ready":

- [ ] RSI matches TradingView for same period/data
- [ ] SMA returns None when candles < 200
- [ ] VIX fetches real-time data (not 15.0)
- [ ] Flow state doesn't flip every tick
- [ ] Volume velocity uses current bar
- [ ] Memory stable over 24-hour run
- [ ] Session resets at correct ET time

---

## Conclusion

**The system is architecturally sound but contains critical calculation errors and placeholder data that make it unsafe for trading.**

**Recommendation:** Fix the 2 critical issues (VIX + RSI) immediately. Then address the high-priority items. Only then proceed with sandbox testing.
