# Local Development Notes

## 🖥️ Development Environment

**Machine:** Mac Mini (Local Development)
**Location:** `/Users/kevinmcgovern/gekko3-brain`

## 🔧 Quick Updates

Since this runs locally on the Mac Mini, you can update components directly via Cursor:

### Strategy Logic (Edit Anytime, Restart Brain)
```bash
# Edit strategy calculations
vim src/alpha_engine.py
# OR use Cursor to edit

# Restart Brain
python main.py
```

**Files you can edit locally:**
- `src/alpha_engine.py` - Flow state logic, indicators, thresholds
- `src/market_feed.py` - Signal generation, WebSocket handling
- `src/gatekeeper_client.py` - Proposal formatting
- `main.py` - Event loop, initialization

**No redeployment needed** - Just restart the Brain after editing.

### Risk Rules (Requires Cloudflare Redeploy)
```bash
# Edit risk limits
cd /Users/kevinmcgovern/gekko3
vim src/config.ts
# OR use Cursor to edit

# Redeploy to Cloudflare
cd /Users/kevinmcgovern/gekko3
npx wrangler deploy
```

**Files that require Cloudflare redeploy:**
- `/gekko3/src/config.ts` - Constitution (risk limits)
- `/gekko3/src/GatekeeperDO.ts` - Risk evaluation logic
- `/gekko3/src/lib/security.ts` - Authentication

## 🚀 Common Tasks

### Update Strategy Parameters
```python
# src/alpha_engine.py
# Change RSI thresholds
if trend == 'UPTREND' and rsi < 25:  # Changed from 30
    # ...

# Change volume velocity threshold
if price > vwap and volume_velocity > 1.5:  # Changed from 1.2
    # ...
```

### Add New Indicators
```python
# src/alpha_engine.py
def _calculate_bollinger_bands(self, symbol: str):
    # Your implementation
    pass
```

### Change Signal Logic
```python
# src/market_feed.py
async def _check_signals(self, symbol: str):
    # Modify conditions
    if trend == 'UPTREND' and rsi < 25 and some_new_condition:
        # ...
```

### Update Dependencies
```bash
# Add new package
pip install new-package
pip freeze > requirements.txt
```

## 📝 Testing Workflow

1. **Make changes** in Cursor
2. **Test locally**: `python test_connection.py`
3. **Run Brain**: `python main.py`
4. **Monitor output** for signals
5. **Iterate** on strategy

## 🔍 Debugging

### View Logs
```bash
python main.py 2>&1 | tee brain.log
```

### Test Individual Components
```python
# test_alpha_engine.py
from src.alpha_engine import AlphaEngine

engine = AlphaEngine()
engine.update('SPY', 450.25, 1000)
indicators = engine.get_indicators('SPY')
print(indicators)
```

### Check Gatekeeper Status
```bash
python test_connection.py
```

## ⚠️ Important Reminders

1. **Local Changes = Restart Brain Only**
   - No Cloudflare redeploy needed for strategy changes
   - Edit → Save → Restart → Test

2. **Cloudflare Changes = Redeploy**
   - Risk rules require `npx wrangler deploy`
   - Changes to Gatekeeper logic require redeploy

3. **Sandbox First**
   - Always test in sandbox before production
   - Update `TRADIER_ACCESS_TOKEN` in `.env` when switching

4. **Environment Variables**
   - `.env` file is local (not committed)
   - Update credentials as needed
   - Copy from `env.template` if missing

## 📦 Project Structure

```
gekko3-brain/              ← LOCAL (Mac Mini)
├── src/
│   ├── alpha_engine.py   ← Edit strategy here
│   ├── market_feed.py    ← Edit signals here
│   └── gatekeeper_client.py
├── main.py               ← Edit event loop here
└── .env                  ← Local secrets

gekko3/                   ← CLOUDFLARE (Remote)
├── src/
│   ├── config.ts        ← Edit risk rules here (redeploy needed)
│   └── GatekeeperDO.ts  ← Edit risk logic here (redeploy needed)
└── wrangler.toml
```

## 🎯 Separation of Concerns

- **Strategy = Local** (Fast iteration, no redeploy)
- **Risk = Cloudflare** (Protected, immutable at runtime)

This separation is your safety net - strategy can change quickly, but risk rules are protected behind a deployment gate.

