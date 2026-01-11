# Monday Go-Live Checklist

## Critical: Credential Separation

**IMPORTANT:** Python Brain and Cloudflare Gatekeeper use **separate credentials**.

- **Python Brain (`.env` file)**: Uses credentials for market data (WebSocket streaming)
- **Cloudflare Gatekeeper (`wrangler secrets`)**: Uses credentials for trade execution

These **MUST match** the environment you're targeting.

---

## Pre-Launch Verification

### Step 1: Verify Credential Alignment

**For Sandbox Testing (Current):**
```bash
# Check Python Brain .env
cat brain/.env | grep TRADIER

# Check Cloudflare Secrets (via status endpoint)
curl https://gekko3-core.kevin-mcgovern.workers.dev/v1/status
# Look for initialization logs showing SANDBOX mode
```

**For Production Launch:**
```bash
# Update Cloudflare to Production credentials
npx wrangler secret put TRADIER_ACCESS_TOKEN  # Your REAL production token
npx wrangler secret put TRADIER_ACCOUNT_ID    # Your REAL production account ID

# Deploy with Production environment
npx wrangler deploy --env production

# Verify it's using Production API
# Check Cloudflare logs - should see: "[Tradier] Initialized in PRODUCTION mode"
```

---

## Safety Measures for First Trade

### Option 1: Micro-Size (Recommended for Validation)

Set `maxOpenPositions: 1` in `src/config.ts` to allow only **one contract** at a time.

**Why:**
- Limits exposure to ~$50-$100 max risk per trade
- Validates the entire pipeline (order entry → execution → reporting)
- Real error messages (unlike Sandbox 500 errors)

**After successful validation:**
- Increase back to 8 for normal operation

### Option 2: Keep Current Limits (8/4)

If you're confident in the system and want to test multiple positions simultaneously.

---

## Monday Morning Protocol

1. **09:00 AM**: Coffee ☕

2. **09:15 AM**: Final Verification
   ```bash
   # Verify credentials are set correctly
   npx wrangler secret list
   
   # Verify deployment
   curl https://gekko3-core.kevin-mcgovern.workers.dev/v1/status
   ```

3. **09:25 AM**: Start Brain
   ```bash
   cd /Users/kevinmcgovern/gekko3/brain
   python3 main.py
   ```

4. **09:30 AM**: Watch for:
   - ✅ Discord notification: "🧠 Gekko3 Brain is ONLINE"
   - ✅ Market Feed connects
   - ✅ VIX polling starts
   - ✅ Gatekeeper status shows NORMAL

5. **First Signal**: Monitor Discord for trade signals

6. **First Trade**: 
   - Watch Cloudflare logs for execution
   - Verify Order ID returned
   - Check Tradier dashboard for order status

---

## Known Limitations

### Sandbox Execution Engine

**Status:** Broken for multileg orders (500 errors)

**Impact:** 
- Cannot properly paper trade credit spreads in Sandbox
- Orders will fail with 500 errors even if format is correct

**Solution:**
- Production API works correctly (verified by gekkoworks2)
- Micro-size first trade in Production validates the pipeline

---

## Emergency Procedures

### Kill Switch Options

1. **Stop Brain (Fastest)**
   ```bash
   # In Brain terminal
   Ctrl+C
   ```

2. **Lock Gatekeeper**
   ```bash
   curl -X POST https://gekko3-core.kevin-mcgovern.workers.dev/v1/admin/lock \
     -H "Content-Type: application/json" \
     -d '{}'
   ```

3. **Nuclear Option (If Brain won't stop)**
   ```bash
   # Invalidate Cloudflare credentials
   npx wrangler secret put TRADIER_ACCESS_TOKEN
   # Enter: "INVALID"
   ```

---

## Post-Launch Verification

After first successful trade:

1. ✅ Order ID received from Tradier
2. ✅ Order appears in Tradier dashboard
3. ✅ Gatekeeper logs show APPROVED status
4. ✅ Position synced to D1 database
5. ✅ Dashboard shows active position

---

## Notes

- **Sandbox is broken** - Don't waste time testing execution there
- **Production API works** - Verified by independent implementation
- **Micro-size is smart** - $50-$100 is reasonable "testing cost"
- **Real errors are better** - Production errors are actionable, Sandbox 500s are not
