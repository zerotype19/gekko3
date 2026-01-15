# Gekko3 System Limits & Constraints

**Last Updated:** 2026-01-14  
**Purpose:** Complete inventory of all limits that could prevent testing or trading

---

## 🚨 **GATEKEEPER CONSTITUTION** (`src/config.ts`)

### **Position Limits**
- **`maxOpenPositions: 20`** ⚠️ **TESTING MODE** (was 3, increased for testing)
  - Maximum total open positions across all symbols
  - **Impact:** Can open up to 20 concurrent trades
  
- **`maxConcentrationPerSymbol: 20`** ⚠️ **TESTING MODE** (was 2, increased for testing)
  - Maximum positions per individual symbol (SPY, QQQ, IWM, DIA)
  - **Impact:** Can have up to 20 positions on SPY simultaneously

### **Daily Loss Protection**
- **`maxDailyLossPercent: 0.02`** (2% hard stop)
  - If account equity drops 2% from start-of-day, system **LOCKS** and rejects all new trades
  - **Impact:** Safety feature - prevents catastrophic losses

### **DTE (Days To Expiration) Limits**
- **`minDte: 0`** (Allows 0DTE for Scalper strategy)
- **`maxDte: 60`** (Allows up to 60-day expirations)
  - **Impact:** Very permissive - allows all strategies (Scalper, Trend, Farmer)

### **Correlation Guard** (Phase C)
- **`maxCorrelatedPositions: 2`** ⚠️ **RESTRICTIVE**
  - Maximum directional trades per correlation group
  - Groups: `US_INDICES` (SPY, QQQ, IWM, DIA), `TECH` (QQQ, XLK)
  - **Impact:** Can only have 2 bullish OR 2 bearish trades across all indices
  - **Example:** If you have 2 bullish SPY trades, a 3rd bullish QQQ trade will be **REJECTED**
  
- **`maxTotalPositions: 5`** ⚠️ **RESTRICTIVE**
  - Maximum total positions across all correlation groups
  - **Impact:** Even if `maxOpenPositions=20`, correlation guard caps at 5
  - **CONFLICT:** This is **LOWER** than `maxOpenPositions: 20`, so correlation guard will trigger first

### **Execution Constraints**
- **`staleProposalMs: 10000`** (10 seconds)
  - Rejects proposals older than 10 seconds
  - **Impact:** Prevents stale orders from executing

- **`forceEodCloseEt: '15:45'`** (3:45 PM ET)
  - All positions automatically closed at end of day
  - **Impact:** No overnight positions

---

## 🧠 **PYTHON BRAIN LIMITS** (`brain/src/market_feed.py`)

### **Proposal Rate Limiting**
- **`min_proposal_interval: 1 minute`** (per symbol)
  - Minimum time between proposals for the same symbol
  - **Impact:** Can send 1 proposal per symbol per minute maximum
  - **Testing Impact:** ⚠️ **SLOW** - if testing on SPY, must wait 1 minute between signals

### **Signal Cooldowns** (Strategy-Specific)
- **Trend Engine / Scalper:** 5-minute cooldown for duplicate signals
  - If same signal fires twice within 5 minutes, second is ignored
  - **Impact:** Prevents spam, but may miss valid re-entries

### **Strategy Timing Windows**
- **ORB (Opening Range Breakout):** 10:00 AM - 11:30 AM ET only
  - **Impact:** Only fires in morning window
  
- **Range Farmer (Iron Condor):** 1:00 PM - 1:05 PM ET only
  - **Impact:** Only fires at lunchtime
  
- **Earnings Assassin:** 3:55 PM - 4:00 PM ET only (if symbol in `EARNINGS_TODAY` list)
  - **Impact:** Manual list, currently empty `[]`
  
- **Weekend Warrior:** Friday 3:55 PM - 4:00 PM ET only
  - **Impact:** Only fires on Fridays

### **Strategy Regime Gates**
- **ORB:** Blocked in `EVENT_RISK` regime
- **Range Farmer:** Only in `LOW_VOL_CHOP` regime
- **Scalper:** Only in `TRENDING` or `HIGH_VOL_EXPANSION` regimes
- **Trend Engine:** Only in `TRENDING` regime
- **Iron Butterfly:** Only in `LOW_VOL_CHOP` regime + IV Rank > 50
- **Ratio Spread:** Any regime, but requires IV Rank < 20

### **Warmup Requirements**
- **SMA(200) Calculation:** Requires 200 candles (~200 minutes = 3.3 hours)
- **ORB Strategy:** Requires 30 candles minimum
- **Impact:** System won't trade until warmup complete

---

## 💰 **CAPITAL CONSTRAINTS**

### **No Explicit Capital Limits**
- System does **NOT** enforce:
  - Maximum capital per trade
  - Maximum capital per symbol
  - Maximum total capital deployed
  - Position sizing limits

### **Implicit Limits**
- Limited by account buying power (Tradier enforces)
- Limited by `maxOpenPositions: 20` (if correlation guard allows)
- Each credit spread typically requires ~$500-$2000 margin per contract

---

## ⚠️ **POTENTIAL TESTING BOTTLENECKS**

### **1. Correlation Guard Conflict** 🔴 **CRITICAL**
```
maxOpenPositions: 20
maxTotalPositions: 5  ← THIS IS THE REAL LIMIT
```
**Problem:** Correlation guard will reject trades after 5 positions, even though Constitution says 20.

**Fix Needed:** Increase `maxTotalPositions` to match `maxOpenPositions`:
```typescript
riskLimits: {
  maxCorrelatedPositions: 2,
  maxTotalPositions: 20,  // ← Change from 5 to 20
}
```

### **2. 1-Minute Proposal Interval** 🟡 **MODERATE**
- Can only test 1 trade per symbol per minute
- If testing multiple strategies, must wait between signals
- **Impact:** Testing will be slow, but not blocked

### **3. Strategy Timing Windows** 🟡 **MODERATE**
- ORB only fires 10:00-11:30 AM
- Range Farmer only fires 1:00-1:05 PM
- **Impact:** Must test during specific windows

### **4. Regime Gates** 🟡 **MODERATE**
- Strategies only fire in specific market regimes
- If market is in wrong regime, strategies won't trigger
- **Impact:** May need to wait for correct market conditions

### **5. Warmup Period** 🟡 **MODERATE**
- 200 minutes (~3.3 hours) before SMA(200) available
- ORB needs 30 minutes minimum
- **Impact:** Must wait for warmup before testing

---

## ✅ **RECOMMENDATIONS FOR TESTING**

### **Immediate Fixes:**
1. **Increase `maxTotalPositions` to 20** in `src/config.ts`
   ```typescript
   riskLimits: {
     maxCorrelatedPositions: 2,
     maxTotalPositions: 20,  // Match maxOpenPositions
   }
   ```

2. **Consider reducing `min_proposal_interval`** for testing:
   ```python
   self.min_proposal_interval = timedelta(seconds=10)  # Instead of 1 minute
   ```

### **Testing Strategy:**
1. **Start Brain early** (before market open) to complete warmup
2. **Test during strategy windows:**
   - ORB: 10:00-11:30 AM
   - Range Farmer: 1:00-1:05 PM
   - Scalper: All day (if regime allows)
   - Trend: All day (if regime allows)
3. **Monitor correlation guard** - it will block after 5 positions (until fixed)
4. **Check regime** - use dashboard to see current regime before testing

---

## 📊 **CURRENT LIMITS SUMMARY**

| Limit Type | Value | Impact on Testing |
|------------|-------|-------------------|
| Max Open Positions | 20 | ✅ Good for testing |
| Max Per Symbol | 20 | ✅ Good for testing |
| Max Total (Correlation) | **5** | 🔴 **BLOCKING** - Will reject after 5 |
| Max Correlated (Directional) | 2 | 🟡 Moderate - Limits directional bias |
| Daily Loss Stop | 2% | ✅ Safety feature |
| DTE Range | 0-60 days | ✅ Very permissive |
| Proposal Interval | 1 minute | 🟡 Slow but acceptable |
| Signal Cooldown | 5 minutes | 🟡 Prevents spam |
| Warmup Required | 200 candles | 🟡 Must wait ~3.3 hours |

---

## 🎯 **BOTTOM LINE**

**Current State:** System is configured for testing with high position limits (20), but **correlation guard will block after 5 positions**.

**Action Required:** Update `maxTotalPositions: 5` → `maxTotalPositions: 20` in `src/config.ts` to match `maxOpenPositions`.

**After Fix:** System can open up to 20 positions for testing, with 1-minute intervals between proposals per symbol.
