# Gekko3-Brain

The Strategy Engine that runs locally on a Mac Mini. Connects to Tradier Streaming, calculates "Tier A" Flow/Alpha, and sends signed proposals to the Cloudflare Gatekeeper.

## Important: Local Development

**This code runs locally on the Mac Mini.** All local components (Python Brain, dependencies, configuration) can be updated directly on this machine via Cursor. You don't need to redeploy Cloudflare to change strategy logic - just edit `src/alpha_engine.py` and restart the Brain.

- **Strategy Logic (Local)**: Edit `src/alpha_engine.py` → Restart Brain
- **Risk Rules (Cloudflare)**: Edit `src/config.ts` in gekko3-core → Redeploy Worker

## Setup

1. **Create `.env` file** (copy from `env.template`):
   ```bash
   cp env.template .env
   ```

2. **Edit `.env`** with your actual values:
   - `TRADIER_ACCESS_TOKEN`: Your Tradier API access token
   - `GATEKEEPER_URL`: Your deployed Gatekeeper URL (e.g., `https://gekko3-core.your-subdomain.workers.dev`)
   - `API_SECRET`: The secret key shared with the Gatekeeper (`0d03cc45af09744228164da6003b865883598cdd6fc85065672a51eba31c718f`)

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Test connection**:
   ```bash
   python test_connection.py
   ```

## Project Structure

```
/gekko3-brain
  ├── .env                 # Secrets (create from env.template)
  ├── env.template         # Template for .env file
  ├── requirements.txt     # Python dependencies
  ├── test_connection.py   # Connection test script
  ├── main.py              # Entry point (to be implemented)
  └── src/
      ├── gatekeeper_client.py  # HTTP client (✅ Complete)
      ├── market_feed.py        # WebSocket client (to be implemented)
      └── alpha_engine.py       # Strategy logic (to be implemented)
```

## Current Status

✅ **Complete:**
- `gatekeeper_client.py` - Full implementation with HMAC signing
- `test_connection.py` - Connection verification script
- `alpha_engine.py` - Tier A Flow metrics calculation (VWAP, Volume Velocity, RSI, Trend)
- `market_feed.py` - Tradier WebSocket client with signal generation
- `main.py` - Main event loop with graceful shutdown

## Running the Brain

1. **Ensure `.env` is configured** (copy from `env.template`):
   ```bash
   cp env.template .env
   # Edit .env with your actual TRADIER_ACCESS_TOKEN
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Test connection first** (optional):
   ```bash
   python test_connection.py
   ```

4. **Run the Brain**:
   ```bash
   python main.py
   ```

The Brain will:
- Connect to Tradier WebSocket
- Monitor SPY and QQQ in real-time
- Calculate flow states, trends, and RSI
- Generate trade proposals when signals are detected
- Send signed proposals to the Cloudflare Gatekeeper

## Signal Logic

The Brain generates proposals based on:

**Bull Put Spread (Credit Spread on Puts):**
- Condition: UPTREND + RSI < 30 + Flow State != NEUTRAL
- Strategy: Sell higher strike PUT, Buy lower strike PUT

**Bear Call Spread (Credit Spread on Calls):**
- Condition: DOWNTREND + RSI > 70 + Flow State != NEUTRAL
- Strategy: Sell lower strike CALL, Buy higher strike CALL

**Rate Limiting:**
- Maximum 1 proposal per symbol per minute
- Prevents signal spam

## Note on Tradier WebSocket

The Tradier WebSocket API format may need adjustment based on actual API documentation. The current implementation follows a standard WebSocket subscription pattern, but you may need to:

1. Initialize a session first
2. Adjust subscription message format
3. Handle authentication differently
4. Parse message format according to Tradier's actual response structure

Refer to [Tradier WebSocket Documentation](https://developer.tradier.com/documentation/streaming/getting-started) for exact API specifications.

