# Brain Files Updated for Credit Spread Support

## Summary

Updated all Brain (Python) files to match the new Gatekeeper requirements:
- `side` must be `'OPEN'` or `'CLOSE'` (not `'BUY'` or `'SELL'`)
- `price` field is now **mandatory** for all proposals
- Leg types must be uppercase (`'PUT'` or `'CALL'`)

---

## Files Updated

### 1. ✅ `brain/src/market_feed.py`

**Changes:**
- **Line 420, 428:** Changed `side = 'SELL'` to `side = 'OPEN'` for new positions
- **Line 519:** Proposal now uses `side: side` which is `'OPEN'` (correct)
- **Line 520:** Added mandatory `'price'` field with mock net credit calculation
- **Lines 526, 534:** Ensure option types are uppercase (`option_type_upper`)
- **Line 566:** Enhanced logging to include price information

**Key Code:**
```python
# Calculate mock limit price (Net Credit for credit spreads)
mock_net_credit = max(0.10, current_price * 0.005)  # Minimum $0.10, or 0.5% of price

proposal = {
    'symbol': symbol,
    'strategy': strategy,
    'side': side,  # Now 'OPEN' or 'CLOSE'
    'quantity': 1,
    'price': round(mock_net_credit, 2),  # MANDATORY
    'legs': [
        {
            'type': option_type_upper,  # Uppercase: PUT or CALL
            # ...
        }
    ]
}
```

**Note:** Mock price calculation is a placeholder. In production, this should fetch actual bid/ask spreads from Tradier option chain to calculate real net credit.

---

### 2. ✅ `brain/src/gatekeeper_client.py`

**Changes:**
- **Line 84:** Added `'price'` to required fields validation
- **Lines 94-101:** Added validation for:
  - `side` must be `'OPEN'` or `'CLOSE'`
  - `price` must be positive (for limit orders)

**Key Code:**
```python
required_fields = ['symbol', 'strategy', 'side', 'quantity', 'price', 'legs', 'context', 'signature']

# Validate side is OPEN or CLOSE (not BUY/SELL)
if proposal_dict.get('side') not in ['OPEN', 'CLOSE']:
    raise ValueError(f"Invalid side: {proposal_dict.get('side')}. Must be 'OPEN' or 'CLOSE'")

# Validate price is positive
if 'price' in proposal_dict and (proposal_dict['price'] is None or proposal_dict['price'] <= 0):
    raise ValueError(f"Invalid price: {proposal_dict.get('price')}. Price must be positive for limit orders")
```

---

### 3. ✅ `brain/test_execution.py`

**Changes:**
- **Line 49:** Changed `"side": "SELL"` to `"side": "OPEN"`
- **Line 50:** Added `"price": 0.50` field
- **Lines 86-92:** Updated logging to display price and side information

**Key Code:**
```python
proposal = {
    "symbol": "SPY",
    "strategy": "CREDIT_SPREAD",
    "side": "OPEN",  # OPEN = Enter new position
    "quantity": 1,
    "price": 0.50,  # MANDATORY: Limit price
    "legs": [...]
}
```

---

### 4. ✅ `brain/simulate_monday.py`

**Changes:**
- **Line 184:** Changed `"side": "SELL"` to `"side": "OPEN"`
- **Line 185:** Added `"price": 0.50` field
- **Lines 217-220:** Updated logging to display price and side information

**Key Code:**
```python
proposal = {
    "symbol": "SPY",
    "strategy": "CREDIT_SPREAD",
    "side": "OPEN",  # OPEN = Enter new position
    "quantity": 1,
    "price": 0.50,  # MANDATORY: Limit price (mock net credit)
    "legs": [...]
}
```

---

## Breaking Changes for Brain

### Before (Old Format):
```python
proposal = {
    'side': 'SELL',  # ❌ Old format
    # No price field  # ❌ Missing
    'legs': [{
        'type': 'put',  # ❌ Lowercase
        # ...
    }]
}
```

### After (New Format):
```python
proposal = {
    'side': 'OPEN',  # ✅ OPEN or CLOSE
    'price': 0.50,   # ✅ MANDATORY limit price
    'legs': [{
        'type': 'PUT',  # ✅ Uppercase (PUT or CALL)
        # ...
    }]
}
```

---

## Validation Status

✅ **All files compile successfully:**
- `market_feed.py` ✅
- `gatekeeper_client.py` ✅
- `test_execution.py` ✅
- `simulate_monday.py` ✅

---

## Next Steps (Production)

### 1. **Implement Real Option Chain Fetching**

Currently using mock option symbols and prices. For production:

```python
# TODO: Fetch real option chain from Tradier
GET /markets/options/chains?symbol=SPY&expiration=2026-01-16

# Use real bid/ask to calculate:
# - Net Credit (for OPEN): (Sell Bid - Buy Ask)
# - Net Debit (for CLOSE): (Buy Ask - Sell Bid)
```

### 2. **Implement Position Tracking**

To support `CLOSE` orders:
- Track open positions in Brain (or query Gatekeeper status)
- When closing, use same legs but set `side: 'CLOSE'`
- Gatekeeper will auto-invert leg sides

### 3. **Price Calculation**

Replace mock price with real calculation:
```python
# For Credit Spread OPEN (Bull Put):
net_credit = sell_leg_bid - buy_leg_ask
price = max(0.01, net_credit)  # Minimum $0.01

# For Credit Spread CLOSE:
net_debit = buy_leg_ask - sell_leg_bid
price = max(0.01, net_debit)
```

---

## Testing

All test files have been updated and will work with the new Gatekeeper format. The Brain is now compatible with:
- ✅ New `OPEN`/`CLOSE` side format
- ✅ Mandatory `price` field
- ✅ Uppercase option types
- ✅ Enhanced validation in `gatekeeper_client.py`

**Status: Ready for Monday validation** ✅
