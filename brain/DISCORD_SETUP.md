# Discord Notifications Setup

## Quick Setup Guide

### Step 1: Create Discord Webhook

1. Open **Discord** (desktop or web)
2. Right-click your server (or create a private one for Gekko3)
3. Go to **Server Settings** → **Apps & Integrations** → **Webhooks**
4. Click **New Webhook**
5. Name it: `Gekko Brain`
6. Optionally: Choose a channel (or create a `#gekko-alerts` channel)
7. Click **Copy Webhook URL**

### Step 2: Add Webhook URL to Environment

Edit `brain/.env` and add:

```bash
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN
```

**Note:** If you don't have a `.env` file yet, copy from `env.template`:
```bash
cd brain
cp env.template .env
# Then edit .env and add DISCORD_WEBHOOK_URL
```

### Step 3: Test Notifications

Restart the Brain:

```bash
cd /Users/kevinmcgovern/gekko3/brain
python3 main.py
```

**Expected:** You should immediately see a Discord notification:
```
🧠 Gekko3 Brain is ONLINE

Mode: Supervisor
Market Hours: 09:25 - 16:05 ET
Timezone: America/New_York
```

---

## Notification Types

### 🟢 Green (Success) - Startup & Signals
- **Brain Startup:** When the supervisor starts
- **Market Open:** When market hours begin
- **Trade Signals:** When a setup is detected
- **Trade Executed:** When Gatekeeper approves a proposal

### 🔵 Blue (Info) - Status Updates
- **Trend Changes:** When trend switches (INSUFFICIENT_DATA → UPTREND, etc.)
- **Weekend Mode:** When system enters weekend sleep

### 🟡 Yellow (Warning) - Warnings
- **Market Closed:** When market closes
- **Proposal Rejected:** When Gatekeeper rejects a trade

### 🔴 Red (Error) - Critical
- **Brain Shutdown:** When system shuts down
- **Proposal Errors:** When sending to Gatekeeper fails

---

## What You'll See on Monday

### During Validation:

1. **9:30 AM ET:**
   ```
   🟢 Market Open
   Connecting to market feed...
   ```

2. **Throughout Day:**
   ```
   📊 VIX updated: 18.45
   (No signals - system in warmup)
   ```

3. **~12:50 PM ET (First Trend Confirmation):**
   ```
   📈 Trend Changed: SPY
   INSUFFICIENT_DATA → UPTREND
   Price: $425.67
   SMA 200: $423.12
   VIX: 18.45
   ```

4. **If Signal Detected:**
   ```
   🚨 SIGNAL DETECTED 🚨
   
   Symbol: SPY
   Strategy: BULL_PUT_SPREAD
   Side: SELL PUT
   
   Indicators:
   • Trend: UPTREND
   • RSI: 28.5
   • Flow: risk_on
   • VIX: 18.45
   • Price: $425.67
   ```

5. **If Gatekeeper Approves:**
   ```
   ✅ Proposal APPROVED: SPY
   
   Strategy: BULL_PUT_SPREAD
   Order ID: 12345
   ```

---

## Troubleshooting

### "Discord notifications disabled" in logs
- **Fix:** Add `DISCORD_WEBHOOK_URL` to your `.env` file

### Notifications not appearing
- Check webhook URL is correct (should start with `https://discord.com/api/webhooks/`)
- Verify webhook is in the correct Discord channel
- Check Discord server notifications aren't muted
- Look for errors in Brain logs: `⚠️ Discord notification failed`

### Too many notifications
- Notifications are designed to be informational, not spammy
- Trend changes only fire when trend actually changes
- Market state notifications only fire on state transitions
- If you want fewer notifications, you can disable specific ones in code

---

## Privacy Note

**Discord Webhooks are PUBLIC URLs.** Anyone with the webhook URL can send messages to your channel.

- Don't share your webhook URL
- Don't commit `.env` to git
- Consider using Discord server permissions to limit who can see the alerts channel

---

## Future: Gatekeeper Notifications

The Gatekeeper (Cloudflare Worker) will eventually send its own notifications for:
- Risk limit hits
- System locks
- Order execution confirmations
- Daily P&L summaries

This will be added in a future update.
