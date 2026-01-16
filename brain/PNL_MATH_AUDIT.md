# P&L Math Audit - Critical Issues Found

## Issues Identified

### 1. **CRITICAL: `cost_to_close <= 0` check skips valid closes** (Line 1155)
- **Problem**: Calendar Spreads (debit strategies) may close for a credit, making `cost_to_close` negative
- **Current**: `if cost_to_close <= 0: continue` (skips calculation)
- **Fix**: Allow negative `cost_to_close` and handle debit/credit closes correctly

### 2. **CRITICAL: P&L formula assumes entry is always credit** (Line 1160)
- **Problem**: `pnl_pct = ((entry_credit - cost_to_close) / entry_credit) * 100`
- **Issue**: For Calendar/Ratio spreads:
  - Entry might be DEBIT (we paid money) → entry_price is positive but represents debit paid
  - Exit might be CREDIT (we receive money) → cost_to_close would be negative
  - Current formula: `(debit - credit) / debit` = wrong!
  - **Correct formula**: Need to know if entry is credit or debit

### 3. **CRITICAL: Entry price loses sign information** (Line 1993)
- **Problem**: `entry_price = max(abs(net_credit), 0.01)` stores entry_price as always positive
- **Issue**: Calendar Spreads opened as debit lose the "debit" information
- **Fix**: Need to track whether entry is credit or debit (store sign or use separate flag)

### 4. **Cost to Close Calculation** (Lines 1138-1147)
- **Analysis**: Logic appears correct:
  - SELL leg (short): `cost_to_close += price * qty` (pay to buy back) ✓
  - BUY leg (long): `cost_to_close -= price * qty` (receive to sell) ✓
- **Sign Convention**: `cost_to_close` = net amount we PAY (positive) or RECEIVE (negative)
- **Status**: ✓ Correct (but validation at line 1155 is wrong)

### 5. **Realized P&L at Close** (Line 973)
- **Problem**: Same formula `pnl_dollars = entry_price - exit_price`
- **Issue**: Assumes entry is credit, exit is debit
- **Fix**: Need strategy-aware P&L calculation

## Recommended Fixes

### Fix 1: Store entry type (credit/debit) in position
- Add `entry_type: 'credit' | 'debit'` to position dict
- Or use negative entry_price for debits (but this breaks existing code)

### Fix 2: Strategy-aware P&L calculation
```python
if pos['strategy'] in ['CREDIT_SPREAD', 'IRON_CONDOR', 'IRON_BUTTERFLY']:
    # Credit strategies: entry = credit received, exit = debit paid
    # P&L = entry - exit (both positive)
    pnl_dollars = entry_price - abs(cost_to_close)
elif pos['strategy'] in ['CALENDAR_SPREAD', 'RATIO_SPREAD']:
    # Debit strategies: entry = debit paid (positive), exit might be credit (negative)
    # If cost_to_close < 0: We close for credit → P&L = entry - (-exit) = entry + exit
    # If cost_to_close > 0: We close for debit → P&L = entry - exit (negative = loss)
    if cost_to_close < 0:
        # Closing for credit: profit = entry_debit + exit_credit
        pnl_dollars = entry_price + abs(cost_to_close)
    else:
        # Closing for larger debit: loss = entry_debit - exit_debit
        pnl_dollars = entry_price - cost_to_close
```

### Fix 3: Remove `cost_to_close <= 0` check
- Allow negative `cost_to_close` (represents credit received to close)
- Handle both debit and credit closes

### Fix 4: Normalize entry_price storage
- Option A: Store `entry_price` and `entry_type` separately
- Option B: Store negative entry_price for debits (breaking change)
- Option C: Always store absolute value, add `is_debit` flag
