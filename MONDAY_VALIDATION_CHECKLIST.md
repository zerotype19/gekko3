# Monday Morning "Burn-In" Validation Checklist

## ⚠️ IMPORTANT: Do NOT Trade on Monday
**Monday is for DATA VALIDATION only. This is a verification day, not a trading day.**

---

## Pre-Market Setup (Before 9:30 AM ET)

- [ ] **Gatekeeper Deployed** - Latest version with VIX validation is live
- [ ] **Brain Running** - Supervisor started and monitoring market hours
- [ ] **TradingView Open** - SPY 1-minute chart with RSI(14) indicator ready
- [ ] **Logs Monitored** - Terminal output visible for real-time validation

---

## Validation Tests (During Market Hours: 9:30 AM - 4:00 PM ET)

### Test 1: VIX Polling ✅

**What to Look For:**
```
📊 VIX updated: [Real Value (e.g., 18.45, 22.10, etc.)]
```

**Validation Criteria:**
- [ ] VIX updates appear every 60 seconds
- [ ] Values are **realistic** (typically 10-30 range, not 15.0 placeholder)
- [ ] Values change over time (if market volatility changes)
- [ ] No errors in VIX poller logs

**If VIX is still showing 15.0 or null:**
- ❌ **STOP** - The polling is not working. Check Tradier API credentials.

---

### Test 2: RSI Accuracy Verification ✅

**Timeline:** After 9:40 AM ET (need 14+ minutes of data)

**What to Do:**
1. **In Brain Logs:** Look for signal checks that include:
   ```
   RSI: [value]
   ```

2. **In TradingView:**
   - Open SPY 1-minute chart
   - Add RSI(14) indicator
   - Note the current RSI value

3. **Compare Values:**
   - [ ] Brain RSI vs TradingView RSI should be **within 0.5 - 1.0 points**
   - [ ] If difference > 1.0, RSI calculation may still be wrong
   - [ ] Re-check at different times (10 AM, 11 AM, 1 PM) to verify consistency

**Expected Behavior:**
- RSI should update every minute (after each bar closes)
- Values should trend smoothly (Wilder's smoothing prevents jagged jumps)
- RSI should range between 0-100

**If RSI values are wildly different:**
- ❌ **STOP** - RSI calculation may be incorrect. Compare implementation with standard RSI formula.

---

### Test 3: SMA Warmup Period ✅

**Timeline:** 9:30 AM - ~12:50 PM ET (200 minutes = 3 hours 20 minutes)

**What to Look For:**
```
Trend: INSUFFICIENT_DATA  (for first 200 minutes)
```

**Validation Criteria:**
- [ ] **First 200 minutes (9:30 AM - 12:50 PM ET):**
  - [ ] `get_trend()` returns `'INSUFFICIENT_DATA'`
  - [ ] `sma_200` is `None` in indicators
  - [ ] No trading signals generated (even if RSI < 30 or > 70)
  - [ ] Logs may show: `⏳ [SYMBOL]: Waiting for SMA data...`

- [ ] **After 12:50 PM ET (~1:00 PM):**
  - [ ] `get_trend()` switches to `'UPTREND'` or `'DOWNTREND'`
  - [ ] `sma_200` has a real value (e.g., `425.67`)
  - [ ] System is now "warm" - signals can be generated (if other conditions met)

**Expected Timeline:**
- 9:30 AM: Market opens, `Trend: INSUFFICIENT_DATA`
- 10:30 AM: Still `INSUFFICIENT_DATA` (only 60 candles)
- 11:30 AM: Still `INSUFFICIENT_DATA` (only 120 candles)
- 12:30 PM: Still `INSUFFICIENT_DATA` (only 180 candles)
- 12:50 PM: **First valid SMA calculation** (200 candles)
- 1:00 PM: `Trend: UPTREND` or `DOWNTREND` (if conditions met)

**If trend becomes `UPTREND`/`DOWNTREND` before 12:50 PM:**
- ❌ **CRITICAL BUG** - SMA is returning partial data. System is not safe.

---

### Test 4: Flow State Stability ✅

**Timeline:** Throughout the day

**What to Look For:**
```
Flow State: RISK_ON / RISK_OFF / NEUTRAL
```

**Validation Criteria:**
- [ ] Flow state should **not flip every tick** in choppy markets
- [ ] Should require 0.1% buffer (price must be > 0.1% above/below VWAP)
- [ ] Should require volume_velocity > 1.2 for RISK_ON/RISK_OFF
- [ ] NEUTRAL should be common when markets are range-bound

**Red Flags:**
- ❌ Flow state flipping every second = Buffer not working
- ❌ Always RISK_ON = VWAP calculation may be wrong

---

### Test 5: VIX Gatekeeper Validation ✅

**Timeline:** Test manually via API call

**Test Method:**
Send a test proposal with `vix: null` or missing VIX:

```bash
curl -X POST https://gekko3-core.kevin-mcgovern.workers.dev/v1/proposal \
  -H "Content-Type: application/json" \
  -H "X-GW-Signature: [test]" \
  -d '{
    "symbol": "SPY",
    "strategy": "CREDIT_SPREAD",
    "side": "SELL",
    "context": {
      "vix": null,
      "flow_state": "risk_on"
    }
  }'
```

**Expected Result:**
```json
{
  "status": "REJECTED",
  "rejectionReason": "VIX not available - system not warmed up or data fetch failed"
}
```

**Validation Criteria:**
- [ ] Gatekeeper **rejects** proposals with `vix: null`
- [ ] Gatekeeper **rejects** proposals with `vix > 28`
- [ ] Gatekeeper **accepts** proposals with `vix: 15.0` (if other checks pass)

---

## Success Criteria

### ✅ System is VALIDATED if:

1. **VIX:** Real values updating every 60 seconds (not placeholder)
2. **RSI:** Matches TradingView within 0.5-1.0 points consistently
3. **SMA Warmup:** Stays `INSUFFICIENT_DATA` until ~12:50 PM ET
4. **Flow State:** Stable (doesn't flip constantly)
5. **Gatekeeper:** Rejects missing/invalid VIX

### ❌ System FAILS validation if:

1. **VIX still 15.0 or null** → Polling broken
2. **RSI off by > 2 points** → Calculation wrong
3. **Trend appears before 12:50 PM** → SMA partial data bug
4. **Flow state oscillates rapidly** → Buffer not working
5. **Gatekeeper accepts null VIX** → Validation not deployed

---

## Post-Validation Actions

### If ALL Tests Pass ✅

**Tuesday is ready for:**
- Sandbox trading test (with paper money)
- Monitor first real signals
- Verify order execution (in sandbox)

**Continue monitoring for 1 week minimum before considering production.**

### If ANY Test Fails ❌

**STOP IMMEDIATELY:**
- Do not proceed to trading
- Document the failure
- Fix the issue
- Re-run validation on next market day

---

## Log Monitoring Tips

**Key Log Patterns to Watch:**

```bash
# Good - VIX updating
📊 VIX updated: 18.45

# Good - Warmup mode
⏳ SPY: Waiting for SMA data...
Trend: INSUFFICIENT_DATA

# Good - System warm
🎯 Signal detected for SPY: BULL_PUT_SPREAD
   Trend: UPTREND, RSI: 28.5, Flow: risk_on, VIX: 18.45

# Bad - Missing VIX
⚠️  SPY: VIX missing - system in warmup mode, skipping signals

# Bad - VIX poller error
❌ VIX poller error: [error message]
```

---

## Emergency Contacts & Resources

**If Issues Arise:**
- Check `PHASE4_FIXES_COMPLETE.md` for implementation details
- Review `AUDIT_CRITICAL_ISSUES.md` for what was fixed
- Verify all fixes in `FIXES_APPLIED.md`

**Testing Tools:**
- TradingView: https://www.tradingview.com
- Tradier API Docs: https://developer.tradier.com
- Cloudflare Worker Logs: `npx wrangler tail`

---

**Remember: Monday is a validation day, not a trading day. Be patient. Verify everything works correctly before risking capital.**
