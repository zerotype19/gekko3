# Tradier Setup Guide - Hybrid Token Configuration

## Important: Sandbox vs Production Tokens

**Tradier Sandbox accounts DO NOT support WebSocket streaming.** This is a limitation of the Tradier API.

### Solution: Hybrid Token Setup

We use different tokens for different purposes:

1. **Brain (Data Collection)**: Uses **PRODUCTION** token
   - Required for WebSocket streaming (market data)
   - Even if account is unfunded, production accounts can stream data
   - Configured in `gekko3-brain/.env`

2. **Gatekeeper (Execution)**: Uses **SANDBOX** token
   - Used for order execution (paper trading)
   - Configured in Cloudflare secrets
   - Keeps trades safe in sandbox environment

## Setup Instructions

### Step 1: Get Your Production Token

1. Log in to [Tradier Dashboard](https://dash.tradier.com)
2. Go to **Settings > API Access**
3. Copy your **Production Access Token** (NOT the Sandbox one)

### Step 2: Update Brain Configuration

Edit `/Users/kevinmcgovern/gekko3-brain/.env`:

```ini
# Production token for data streaming (required for WebSocket)
TRADIER_ACCESS_TOKEN=wDp7ad3HAPeLCYPnjmzU6dQFM9kh

# Gatekeeper URL (unchanged)
GATEKEEPER_URL=https://gekko3-core.kevin-mcgovern.workers.dev

# API Secret (unchanged)
API_SECRET=0d03cc45af09744228164da6003b865883598cdd6fc85065672a51eba31c718f
```

### Step 3: Verify Gatekeeper Uses Sandbox

The Gatekeeper (Cloudflare) already has the Sandbox token configured:
- `TRADIER_ACCESS_TOKEN`: `XFE6d2z7hJnleNbpQ789otJmvW3z` (Sandbox)
- `TRADIER_ACCOUNT_ID`: `VA13978285` (Sandbox)

This means:
- ✅ Brain sees real market data (Production token)
- ✅ Brain sends proposals to Gatekeeper
- ✅ Gatekeeper executes in Sandbox (safe paper trading)

## How It Works

```
┌─────────────────┐
│  Tradier API    │
│  (Production)   │
└────────┬────────┘
         │
         │ WebSocket Stream
         ▼
┌─────────────────┐
│  Gekko3 Brain   │ ◄── Uses PRODUCTION token
│  (Local Mac)    │     for data streaming
└────────┬────────┘
         │
         │ Signed Proposals
         ▼
┌─────────────────┐
│   Gatekeeper    │ ◄── Uses SANDBOX token
│  (Cloudflare)   │     for order execution
└────────┬────────┘
         │
         │ Paper Trades
         ▼
┌─────────────────┐
│  Tradier API    │
│  (Sandbox)      │
└─────────────────┘
```

## Session Creation Fix

The updated `market_feed.py` now properly:

1. **Creates Session First**: Calls `POST /v1/markets/events/session` via HTTP
2. **Gets Session ID**: Extracts `sessionid` from response
3. **Connects WebSocket**: Uses session ID to authenticate WebSocket connection
4. **Subscribes**: Sends subscription with session ID

This fixes the `"session not found"` error.

## Testing

After updating `.env` with your production token:

```bash
cd /Users/kevinmcgovern/gekko3-brain
python3 main.py
```

You should see:
```
🔌 Creating Market Session...
✅ Session created: xxxxxxxx...
🔑 Session Created. Connecting to WebSocket...
✅ Connected to Tradier WebSocket
📡 Subscribed to: SPY, QQQ
🚀 Market Feed running...
```

## Troubleshooting

### "session not found" Error
- ✅ Fixed by proper session creation via HTTP first
- Ensure you're using PRODUCTION token (not sandbox)

### "401 Unauthorized" on Session Creation
- Check that your production token is correct
- Verify token has streaming permissions
- Token should be from Production API Access section

### WebSocket Connection Fails
- Check network connectivity
- Verify Tradier service status
- Session ID may have expired, will auto-retry

## Important Notes

- **Production Token**: Required for streaming, but account doesn't need to be funded
- **Sandbox Token**: Used for execution, keeps trades safe
- **Session Management**: Automatically handles reconnection and session renewal
- **Rate Limiting**: WebSocket maintains connection, no need to recreate session frequently

## Reference

- [Tradier Streaming API Docs](https://developer.tradier.com/documentation/streaming/getting-started)
- Relevant Video: [Tradier API - Stock and Option Trading with Python](https://www.youtube.com/watch?v=FQ9hVHV_xV8)

