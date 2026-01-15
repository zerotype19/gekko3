# Gekko3 Brain - Quick Start Guide

## Prerequisites

- Python 3.10 or higher
- Tradier sandbox account (for testing)
- Gatekeeper deployed and accessible

## Setup (One-Time)

```bash
# 1. Navigate to project directory
cd /Users/kevinmcgovern/gekko3-brain

# 2. Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # On macOS/Linux
# OR: venv\Scripts\activate  # On Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create .env file
cp env.template .env

# 5. Edit .env with your credentials
# TRADIER_ACCESS_TOKEN=XFE6d2z7hJnleNbpQ789otJmvW3z (already set in template)
# GATEKEEPER_URL=https://gekko3-core.kevin-mcgovern.workers.dev (already set)
# API_SECRET=0d03cc45af09744228164da6003b865883598cdd6fc85065672a51eba31c718f (already set)
```

## Testing

### 1. Test Gatekeeper Connection
```bash
python test_connection.py
```

Expected output:
```
✅ Connection successful! Gatekeeper is alive.
📊 System Status:
   Status: NORMAL
   Positions: 0
   Daily P&L: 0.0000
```

### 2. Run the Brain
```bash
python main.py
```

Expected output:
```
🧠 Initializing Gekko3 Brain...
✅ Alpha Engine initialized
✅ Gatekeeper Client initialized
✅ Market Feed initialized

🚀 Starting Gekko3 Brain
🔌 Connecting to Tradier WebSocket...
✅ Connected to Tradier WebSocket
📡 Subscribed to symbols: SPY, QQQ
🚀 Market Feed running...
   Monitoring: SPY, QQQ
```

## What Happens When Running

1. **Connects to Tradier WebSocket** - Streams live market data
2. **Aggregates 1-minute candles** - Builds price/volume history
3. **Calculates indicators**:
   - VWAP (Volume Weighted Average Price)
   - Volume Velocity (current vs 20-period average)
   - RSI(14) - Relative Strength Index
   - SMA(200) - Trend indicator
4. **Generates flow state**:
   - `RISK_ON`: Price > VWAP AND Volume Velocity > 1.2
   - `RISK_OFF`: Price < VWAP AND Volume Velocity > 1.2
   - `NEUTRAL`: Otherwise
5. **Detects signals**:
   - **Bull Put Spread**: When oversold (RSI < 30) in uptrend
   - **Bear Call Spread**: When overbought (RSI > 70) in downtrend
6. **Sends proposals** to Gatekeeper (rate limited: 1 per symbol per minute)

## Signal Examples

When a signal is detected, you'll see:
```
🎯 Signal detected for SPY: BULL_PUT_SPREAD
   Trend: UPTREND, RSI: 28.45, Flow: RISK_ON
📤 Proposal sent to Gatekeeper: APPROVED
   ✅ Order ID: 12345
```

## Troubleshooting

### WebSocket Connection Issues
- **Error: "Failed to connect to WebSocket"**
  - Check TRADIER_ACCESS_TOKEN in .env
  - Verify token is valid for sandbox/production
  - Check network connectivity

### Gatekeeper Connection Issues
- **Error: "401 Unauthorized"**
  - Verify API_SECRET matches between Brain and Gatekeeper
  - Check GATEKEEPER_URL is correct
  - Ensure Gatekeeper is deployed and running

### No Signals Generated
- **Normal behavior** - Signals only trigger when conditions are met
- Need at least 30 candles of data before signals can be generated
- Check that market data is flowing (you should see price updates)

## Stopping the Brain

Press `Ctrl+C` for graceful shutdown:
```
⚠️  Keyboard interrupt received
🛑 Shutting down Gekko3 Brain...
✅ Shutdown complete
```

## Production Considerations

Before running with real money:

1. **Switch to Production Tradier API**:
   - Update TRADIER_ACCESS_TOKEN in .env
   - Set `use_sandbox=False` in `main.py`

2. **Update Option Chain Logic**:
   - Replace mock option symbols with actual Tradier option chain API calls
   - Implement proper strike selection based on delta/DTE requirements

3. **VIX Integration**:
   - Replace placeholder VIX value (15.0) with actual VIX data
   - Can use Tradier REST API or another data source

4. **Error Handling**:
   - Add more robust error handling for WebSocket reconnection
   - Implement logging to file
   - Add monitoring/alerting

5. **Risk Management**:
   - Review signal logic and thresholds
   - Test thoroughly in sandbox first
   - Start with small position sizes

