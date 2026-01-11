# End-of-Day (EOD) P&L Reporter Implementation

## Overview

Automated daily performance report that runs at **4:30 PM ET** (21:30 UTC) every weekday via Cloudflare Worker Cron Triggers. Calculates day's P&L and sends a formatted message to Discord.

## Implementation Details

### 1. Cron Trigger (`wrangler.toml`)

Added cron trigger to fire at 4:30 PM ET (21:30 UTC) every weekday:

```toml
[[triggers.crons]]
cron = "30 21 * * 1-5"  # 4:30 PM ET (21:30 UTC) Monday-Friday
```

**Note:** This uses UTC time. During Daylight Savings Time (EDT = UTC-4), the report will run at 5:30 PM EDT. Adjust if needed.

### 2. Environment Configuration (`src/config.ts`)

Added optional `DISCORD_WEBHOOK_URL` to the `Env` interface:

```typescript
DISCORD_WEBHOOK_URL?: string; // Optional: Discord webhook for EOD reports
```

### 3. EOD Report Method (`src/GatekeeperDO.ts`)

Added `generateEndOfDayReport()` method that:

1. **Fetches Current Equity** from Tradier API
2. **Tracks Start of Day Equity** in Durable Object persistent storage
   - Stores equity at first run of the day
   - Resets daily based on UTC date
3. **Calculates Day P&L**:
   - Dollar amount: `Current Equity - Start of Day Equity`
   - Percentage: `(Day P&L $ / Start of Day Equity) * 100`
4. **Queries Today's Trade Activity** from `proposals` table:
   - Counts approved vs rejected proposals
   - Calculates approval rate (proxy for performance)
5. **Formats Discord Embed** with:
   - Starting Equity
   - Ending Equity
   - Net Profit/Loss ($ and %)
   - Trades Taken (approved / total)
   - Approval Rate
6. **Sends to Discord** via webhook (if configured)

### 4. Routing (`src/index.ts` & `src/GatekeeperDO.ts`)

- **Scheduled Handler** (`src/index.ts`): Routes cron trigger to Gatekeeper DO
- **Fetch Handler** (`src/GatekeeperDO.ts`): Added route `/scheduler/eod-report` to trigger report generation

## Setup Instructions

### Step 1: Configure Discord Webhook

1. Go to your Discord server
2. Server Settings → Apps & Integrations → Webhooks
3. Create a new webhook
4. Copy the webhook URL

### Step 2: Set Cloudflare Secret

```bash
npx wrangler secret put DISCORD_WEBHOOK_URL
# Paste your Discord webhook URL when prompted
```

### Step 3: Deploy

```bash
npx wrangler deploy
```

The cron trigger will automatically activate after deployment.

## How It Works

1. **4:30 PM ET (21:30 UTC)** - Cloudflare Worker cron trigger fires
2. **Scheduled Handler** routes request to Gatekeeper Durable Object
3. **Gatekeeper DO** generates the report:
   - Fetches current equity from Tradier
   - Calculates P&L vs start of day baseline
   - Queries today's proposals from database
   - Formats Discord embed
   - Sends to Discord webhook
4. **Discord** displays the report in the configured channel

## Report Contents

The Discord embed includes:

- **Starting Equity**: Equity at start of trading day
- **Ending Equity**: Current equity at report time
- **Net Profit/Loss**: Dollar amount and percentage
- **Trades Taken**: Count of approved trades / total proposals
- **Approval Rate**: Percentage of proposals approved

## Notes & Limitations

1. **Start of Day Equity**: Currently uses UTC date boundaries. Ideally should track equity at market open (9:30 AM ET), but current implementation uses first run of the day as baseline.

2. **Approval Rate vs Win Rate**: Reports "approval rate" (approved proposals / total proposals) rather than actual win rate. True win rate would require tracking realized P&L per trade, which isn't currently implemented.

3. **Timezone Handling**: Report time is in UTC. During DST, report will run at 5:30 PM EDT instead of 4:30 PM. To fix, adjust cron expression or use a timezone library.

4. **Graceful Degradation**: If Discord webhook is not configured, the report is skipped (logged but doesn't fail).

5. **No Trades Table**: Uses `proposals` table instead of `trades_archive` (which doesn't exist in current schema). This is a reasonable proxy for trade activity.

## Testing

To test the report without waiting for the cron:

```bash
# Trigger manually via curl (adjust URL to your worker)
curl -X POST https://your-worker.workers.dev/v1/admin/eod-report
```

**Note:** This endpoint doesn't exist yet - you'd need to add a manual trigger route if desired.

## Future Enhancements

1. **Market Open Baseline**: Track equity at 9:30 AM ET (market open) instead of first run
2. **True Win Rate**: Track realized P&L per trade for accurate win rate calculation
3. **Timezone Library**: Use proper timezone handling (e.g., `date-fns-tz`) for accurate ET calculations
4. **Historical Reports**: Store reports in database for historical analysis
5. **Multiple Channels**: Support multiple Discord channels/webhooks
