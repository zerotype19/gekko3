# Gekko3 - Risk Gatekeeper & Execution Venue

Complete trading system with Cloudflare Worker (Gatekeeper) and Local Python Brain.

## Project Structure

```
gekko3/
├── brain/                    # Python Brain (Local Strategy Engine)
│   ├── main.py              # Entry point with Market Supervisor
│   ├── src/                 # Brain source code
│   │   ├── alpha_engine.py  # Flow metrics & indicators
│   │   ├── market_feed.py   # Tradier WebSocket client
│   │   └── gatekeeper_client.py  # HTTP client for Gatekeeper
│   ├── .env                 # Brain configuration (create from env.template)
│   ├── requirements.txt     # Python dependencies
│   └── *.md                 # Brain documentation
│
├── src/                     # Cloudflare Worker (Gatekeeper)
│   ├── index.ts            # Main Worker router
│   ├── GatekeeperDO.ts     # Durable Object (Risk Engine)
│   ├── config.ts           # Constitution (Risk Rules)
│   ├── types.ts            # TypeScript types
│   └── lib/
│       ├── tradier.ts      # Tradier API wrapper
│       └── security.ts     # HMAC verification
│
├── schema.sql              # D1 Database schema
├── wrangler.toml           # Cloudflare configuration
├── package.json            # Node dependencies
└── tsconfig.json           # TypeScript config
```

## Quick Start

### 1. Cloudflare Gatekeeper (Deployed)

The Gatekeeper is already deployed and running:
- URL: `https://gekko3-core.kevin-mcgovern.workers.dev`
- Status: Check `/v1/status` endpoint

**Secrets configured:**
- `TRADIER_ACCESS_TOKEN` (Sandbox)
- `TRADIER_ACCOUNT_ID` (Sandbox)
- `API_SECRET`

### 2. Python Brain (Local)

**Setup:**
```bash
cd brain
pip3 install -r requirements.txt
cp env.template .env
# Edit .env with your production Tradier token for streaming
```

**Run:**
```bash
cd brain
python3 main.py
```

**Stop:**
Press `Ctrl+C` in the terminal

See `brain/HOW_TO_STOP.md` for details.

## Documentation

### Brain (Python)
- `brain/README.md` - Brain overview
- `brain/QUICKSTART.md` - Quick start guide
- `brain/HOW_TO_STOP.md` - How to stop the Brain
- `brain/MARKET_SUPERVISOR.md` - Market hours supervisor
- `brain/TRADIER_SETUP.md` - Tradier configuration
- `brain/LOCAL_DEVELOPMENT.md` - Local development guide

### Gatekeeper (Cloudflare)
- `SETUP_SECRETS.md` - Secrets setup guide
- `TRADIER_CREDENTIALS.md` - Tradier credentials reference

## Architecture

```
┌─────────────────┐
│  Tradier API    │
│  (Production)   │
└────────┬────────┘
         │ WebSocket Stream
         ▼
┌─────────────────┐
│  Python Brain   │ ◄── Local (Mac Mini)
│  (Local)        │     - Calculates signals
│                 │     - Sends proposals
└────────┬────────┘
         │ Signed Proposals
         ▼
┌─────────────────┐
│   Gatekeeper    │ ◄── Cloudflare Worker
│  (Cloudflare)   │     - Validates risk
│                 │     - Executes trades
└────────┬────────┘
         │ Orders
         ▼
┌─────────────────┐
│  Tradier API    │
│  (Sandbox)      │
└─────────────────┘
```

## Key Features

- **Market Supervisor**: Only connects during market hours (9:25 AM - 4:05 PM ET)
- **Risk Gatekeeper**: Hard-coded risk rules (Constitution) in Cloudflare
- **Hybrid Tokens**: Production token for data, Sandbox token for execution
- **Audit Trail**: All proposals logged in D1 database
- **Auto-Recovery**: Handles disconnections and crashes

## Development

### Edit Strategy (Local - No Redeploy)
```bash
# Edit brain/src/alpha_engine.py
vim brain/src/alpha_engine.py
# Restart Brain
```

### Edit Risk Rules (Requires Redeploy)
```bash
# Edit src/config.ts
vim src/config.ts
# Redeploy
npx wrangler deploy
```

## Status

✅ Gatekeeper deployed and running
✅ Brain ready to run locally
✅ Market Supervisor implemented
✅ Session management fixed
✅ Hybrid token configuration complete
