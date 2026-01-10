# 🔥 CRITICAL CODE AUDIT - Issues Found

## Executive Summary

**Status: NOT PRODUCTION READY**  
The code contains placeholder data, incorrect calculations, and unverified logic that could cause:
- False trade signals
- Invalid risk checks
- Memory leaks
- Incorrect trend detection

---

## Issue #1: VIX Placeholder (CRITICAL)

**Location:** `brain/src/market_feed.py:406`

```python
'context': {
    'vix': 15.0,  # Placeholder - fetch actual VIX in production
    ...
}
```

**Problem:**
- Gatekeeper checks `if vix > 28: REJECT`
- Brain always sends `15.0`
- **Result: VIX check is completely bypassed - VOLATILITY VETO IS BROKEN**

**Fix Required:**
- Implement VIX REST API poller
- Fetch VIX from Tradier or alternative source every minute
- Update context with real VIX value

**Severity: 🔴 CRITICAL - Trading with broken risk check**

---

## Issue #2: RSI Calculation is WRONG (CRITICAL)

**Location:** `brain/src/alpha_engine.py:196-216`

```python
def _calculate_rsi(self, symbol: str, period: int = 14) -> float:
    closes = self.candles[symbol]['close'].tail(period + 1)
    deltas = closes.diff()
    
    gains = deltas.where(deltas > 0, 0)
    losses = -deltas.where(deltas < 0, 0)
    
    avg_gain = gains.tail(period).mean()  # ❌ WRONG: Simple mean
    avg_loss = losses.tail(period).mean()  # ❌ WRONG: Simple mean
```

**Problem:**
- Uses **simple mean** (arithmetic average)
- RSI requires **Wilder's Smoothing** (exponential-like)
- Standard RSI formula: `RS = (AvgGain / AvgLoss)` where averages use:
  - First: Simple average
  - Subsequent: `NewAvg = ((OldAvg * (N-1)) + NewValue) / N`

**Current Behavior:**
- RSI values will be **incorrect**
- Signals based on RSI < 30 or > 70 are **unreliable**
- Could trigger false signals or miss real signals

**Fix Required:**
- Implement proper Wilder's smoothing RSI
- Track running averages between calculations
- Verify against known RSI calculators

**Severity: 🔴 CRITICAL - Trading decisions based on wrong data**

---

## Issue #3: SMA on Partial Data (HIGH)

**Location:** `brain/src/alpha_engine.py:183-194`

```python
def _calculate_sma(self, symbol: str, period: int = 200) -> float:
    if self.candles[symbol].empty or len(self.candles[symbol]) < period:
        # If not enough data, use available data or return current price
        if not self.candles[symbol].empty:
            return self.candles[symbol]['close'].mean()  # ❌ WRONG: Mean of 50 candles != SMA(200)
```

**Problem:**
- First 200 minutes (3+ hours), SMA is calculated on partial data
- Example: After 50 minutes, returns `mean(50 candles)` but labeled as "SMA(200)"
- Trend detection (`price > sma`) will be **unreliable** until full 200 candles
- Could give false UPTREND/DOWNTREND signals

**Current Behavior:**
- Trend flips erratically during first 3 hours
- Signals generated with incomplete trend data
- No warning that trend is "partial"

**Fix Required:**
- Return `None` or `'INSUFFICIENT_DATA'` if candles < period
- Only calculate trend when we have full period
- Add flag to indicate if trend is "partial" vs "full"

**Severity: 🟡 HIGH - Wrong trend signals during startup**

---

## Issue #4: Volume Velocity Uses Stale Data (MEDIUM)

**Location:** `brain/src/alpha_engine.py:164-181`

```python
def _calculate_volume_velocity(self, symbol: str) -> float:
    # ...
    if not self.candles[symbol].empty:
        current_volume = self.candles[symbol]['volume'].iloc[-1]  # ❌ Last CLOSED candle
    else:
        current_volume = self.current_bars.get(symbol, {}).get('volume', 0)
```

**Problem:**
- `current_volume` is from the **last closed candle** (up to 1 minute old)
- Should use **current accumulating bar's volume** for real-time velocity
- Compares "1 minute ago" volume vs "20-period average" - not truly "current"

**Current Behavior:**
- Volume velocity lagged by up to 1 minute
- Flow state decisions based on slightly stale data
- Could miss rapid volume changes

**Fix Required:**
- Use `self.current_bars[symbol]['volume']` if available (current bar)
- Fall back to last candle only if no current bar exists
- Document the 1-minute lag if intentional

**Severity: 🟡 MEDIUM - Minor timing issue, not critical**

---

## Issue #5: Flow State Flip-Flop Risk (MEDIUM)

**Location:** `brain/src/alpha_engine.py:247-252`

```python
elif price > vwap and volume_velocity > 1.2:
    flow_state = 'RISK_ON'
elif price < vwap and volume_velocity > 1.2:
    flow_state = 'RISK_OFF'
```

**Problem:**
- In choppy markets, price oscillates around VWAP
- Flow state could flip **every tick** (RISK_ON → RISK_OFF → RISK_ON)
- No hysteresis or smoothing
- Could trigger rapid-fire signal spam (rate-limited, but still noisy)

**Example Scenario:**
- Price: 450.00, VWAP: 450.01 → RISK_OFF
- Price: 450.02 (next tick), VWAP: 450.01 → RISK_ON
- Price: 450.00 (next tick), VWAP: 450.01 → RISK_OFF
- **Result: State flips every second**

**Fix Required:**
- Add buffer/hysteresis: `price > vwap * 1.001` (0.1% buffer)
- Or use smoothed price (e.g., 5-minute average) instead of raw tick
- Or require state to persist for N seconds before triggering signals

**Severity: 🟡 MEDIUM - Could cause signal noise, but rate-limited**

---

## Issue #6: Memory Leak Potential (LOW - but verify)

**Location:** `brain/src/alpha_engine.py:149-153`

```python
# Trim to lookback window
cutoff_time = timestamp - timedelta(minutes=self.lookback_minutes)
self.candles[symbol] = self.candles[symbol][
    self.candles[symbol]['timestamp'] >= cutoff_time
].reset_index(drop=True)
```

**Analysis:**
- Logic looks correct - filters by timestamp and resets index
- However, pandas `pd.concat` creates new DataFrame each time
- If `_close_bar` called frequently, could accumulate memory

**Potential Issue:**
- `pd.concat([df, new_row])` creates new DataFrame (old one in memory until GC)
- With high-frequency ticks, temporary memory spikes possible
- `reset_index(drop=True)` should help, but not guaranteed

**Verification Needed:**
- Test with 24 hours of continuous data
- Monitor memory usage
- Consider using pre-allocated array or more efficient structure

**Severity: 🟢 LOW - Probably fine, but should monitor**

---

## Issue #7: Session Reset Logic (LOW)

**Location:** `brain/src/alpha_engine.py:41-49`

```python
def _get_session_start(self, current_time: datetime) -> datetime:
    session_start = current_time.replace(hour=9, minute=30, second=0, microsecond=0)
    if current_time.hour < 9 or (current_time.hour == 9 and current_time.minute < 30):
        session_start = session_start - timedelta(days=1)
```

**Problem:**
- No timezone handling - assumes `current_time` is already in ET
- If server is UTC or different timezone, session start is wrong
- VWAP resets at wrong time → wrong VWAP calculations

**Fix Required:**
- Use `ZoneInfo("America/New_York")` for all datetime operations
- Ensure `timestamp` parameter is timezone-aware
- Test session reset on actual market open

**Severity: 🟢 LOW - Timezone should be handled, but might work if server is ET**

---

## Issue #8: Mock Option Strikes (KNOWN - Documented)

**Location:** `brain/src/market_feed.py:372-379`

```python
# Mock strikes (in production, calculate proper strikes from option chain)
if option_type == 'PUT':
    sell_strike = int(current_price * 0.98)  # 2% OTM
    buy_strike = int(current_price * 0.96)   # 4% OTM
```

**Status:** ✅ **KNOWN LIMITATION** - Documented as placeholder
- Not a bug, but incomplete implementation
- Needs Tradier Option Chain API integration
- Gatekeeper will reject these proposals anyway (DTE check should fail with mock data)

**Severity: 🟢 LOW - Documented, not production blocker (will be rejected)**

---

## Summary of Required Fixes

### 🔴 CRITICAL (Must Fix Before Trading):
1. **VIX Placeholder** - Implement real VIX fetching
2. **RSI Calculation** - Fix Wilder's smoothing

### 🟡 HIGH/MEDIUM (Should Fix):
3. **SMA Partial Data** - Return None/flag for insufficient data
4. **Volume Velocity Stale Data** - Use current bar volume
5. **Flow State Flip-Flop** - Add hysteresis/smoothing

### 🟢 LOW (Nice to Have):
6. Memory leak verification
7. Timezone handling verification
8. Option chain integration (already documented)

---

## Recommended Action Plan

1. **STOP** - Do not run in production until Critical issues fixed
2. **Fix VIX** - Implement REST API poller (1-minute refresh)
3. **Fix RSI** - Implement proper Wilder's smoothing
4. **Fix SMA** - Add insufficient data handling
5. **Test** - Verify all calculations against known values
6. **Monitor** - Run in sandbox for 1 week before production

---

## Testing Checklist

- [ ] VIX fetches real data (not 15.0)
- [ ] RSI matches TradingView/other calculators
- [ ] SMA returns None if < 200 candles
- [ ] Flow state doesn't flip every tick
- [ ] Volume velocity uses current bar
- [ ] Memory usage stable over 24 hours
- [ ] Session resets at correct ET time
