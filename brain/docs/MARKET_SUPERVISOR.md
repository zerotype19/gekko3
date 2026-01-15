# Market Supervisor Pattern

## Overview

The Brain now includes an intelligent **Market Supervisor** that only connects during market hours, preventing wasted API calls, log spam, and potential API abuse flags.

## How It Works

The Supervisor continuously monitors the time (ET timezone) and makes intelligent decisions:

### Schedule Rules

1. **Weekend (Saturday/Sunday)**: SLEEP
   - Checks every 4 hours
   - No connections attempted

2. **Pre-Market (< 9:25 AM ET)**: SLEEP
   - Checks every 5 minutes
   - Connects at 9:25 AM (5 min before market open)

3. **Market Hours (9:25 AM - 4:05 PM ET)**: CONNECT
   - Active WebSocket connection
   - Streaming market data
   - Generating signals

4. **Post-Market (> 4:05 PM ET)**: DISCONNECT & SLEEP
   - Calculates time until next market open
   - Sleeps until tomorrow 9:25 AM ET

## Benefits

### ✅ Set It and Forget It
- Can run 24/7 on Mac Mini
- Automatically handles weekends and market closures
- No manual intervention needed

### ✅ API Health
- No zombie connections during off-hours
- Prevents potential API abuse flags
- Respects Tradier's resources

### ✅ Log Hygiene
- Clean logs without "Connection Closed" spam
- Only active during relevant hours
- Clear status messages

### ✅ Data Quality
- Indicators only calculated during market hours
- No garbage data from post-market noise
- Volume Velocity and SMA remain meaningful

## Implementation Details

### BrainSupervisor Class

```python
class BrainSupervisor:
    - is_market_hours()  # Checks if market is open
    - run()              # Main supervisor loop
    - shutdown()         # Graceful shutdown
```

### Market Feed Enhancements

Added to `MarketFeed`:
- `is_connected` property (public, for supervisor)
- `stop_signal` flag (graceful shutdown control)
- `disconnect()` method (clean disconnection)

### Connection Lifecycle

```
1. Supervisor checks time → Market Open?
   ↓ YES
2. Creates MarketFeed task
   ↓
3. MarketFeed creates session → Connects WebSocket
   ↓
4. Streams data, generates signals
   ↓
5. Supervisor monitors (checks every 60s)
   ↓
6. Market closes → Supervisor calls disconnect()
   ↓
7. MarketFeed closes WebSocket, stops loop
   ↓
8. Supervisor sleeps until next market open
```

## Usage

Simply run the Brain as before:

```bash
python3 main.py
```

The Supervisor handles everything automatically:

```
🧠 Initializing Gekko3 Brain (Supervisor Mode)...
✅ Gatekeeper Client initialized
✅ Alpha Engine initialized
✅ Market Feed initialized
🚀 Supervisor started - Monitoring market hours...
   Market Hours: 09:25:00 - 16:05:00 ET
   Timezone: America/New_York

💤 Pre-Market. Checking again in 5 minutes...
[5 minutes later]
🟢 Market Open: Starting Market Feed...
🔌 Creating Market Session...
✅ Session created: xxxxxxxx...
🔑 Session Created. Connecting to WebSocket...
✅ Connected to Tradier WebSocket
📡 Subscribed to: SPY, QQQ
🚀 Market Feed running...
   Monitoring: SPY, QQQ
[Market trading...]
🔴 Post-Market: Stopping Market Feed...
🔌 Disconnect requested...
✅ WebSocket closed
💤 Post-Market. Sleeping until tomorrow 9:25 AM ET...
```

## Configuration

Market hours can be adjusted in `main.py`:

```python
self.market_open = time(9, 30)          # Market opens 9:30 AM ET
self.market_close = time(16, 0)         # Market closes 4:00 PM ET
self.pre_market_buffer = time(9, 25)    # Connect 5 mins early
self.post_market_buffer = time(16, 5)   # Disconnect 5 mins late
```

## Timezone Handling

Uses `zoneinfo.ZoneInfo("America/New_York")` for accurate ET timezone handling:
- Automatically handles DST (Daylight Saving Time)
- Always uses correct market hours regardless of server timezone
- Python 3.9+ built-in (no extra dependencies needed)

## Error Handling

- **Feed Crash**: Supervisor automatically restarts the feed task
- **Connection Lost**: MarketFeed handles reconnection internally
- **Shutdown**: Graceful shutdown on Ctrl+C or SIGTERM
- **Market Closed**: Clean disconnection when market closes

## Monitoring

The Supervisor logs all state changes:
- `🟢 Market Open`: Starting feed
- `🔴 Market Closed`: Stopping feed  
- `💤 Sleeping`: Waiting for next market open
- `⚠️ Weekend`: No trading today

## Comparison: Before vs After

### Before (Rookie Behavior)
```
❌ Connected 24/7
❌ Logs filled with "Connection Closed" errors
❌ Wasted bandwidth during off-hours
❌ Indicators fed garbage post-market data
❌ Potential API abuse flags
```

### After (Professional)
```
✅ Connected only during market hours
✅ Clean logs with meaningful status
✅ Efficient bandwidth usage
✅ Quality data for indicators
✅ API-friendly connection pattern
```

## Troubleshooting

### "Market Feed crashed"
- Supervisor will automatically restart
- Check logs for underlying error
- Verify Tradier token is valid

### "Not connecting during market hours"
- Check system timezone
- Verify ET timezone is correct
- Check market schedule (holidays not handled yet)

### "ZoneInfo not found"
- Python 3.9+ includes zoneinfo
- For older Python, install: `pip install backports.zoneinfo`

## Future Enhancements

- [ ] Holiday detection (skip trading on market holidays)
- [ ] Extended hours support (pre/post market)
- [ ] Configurable market hours via .env
- [ ] Health check endpoints
- [ ] Metrics/telemetry during market hours

