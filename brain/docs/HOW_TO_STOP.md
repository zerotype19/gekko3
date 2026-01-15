# How to Stop the Gekko3 Brain

## Quick Answer

**Press `Ctrl + C` in the terminal where the Brain is running.**

That's it! The Supervisor will handle graceful shutdown.

## What You'll See

When you press `Ctrl + C`:

```
⚠️  Interrupted by user
🛑 Shutdown requested...
🔌 Disconnect requested...
✅ WebSocket closed
🔌 Disconnected from Market Feed
👋 Goodbye!
```

## If Ctrl+C Doesn't Work

If the Brain is stuck or unresponsive:

1. **Find the process:**
   ```bash
   ps aux | grep "python.*main.py"
   ```

2. **Kill the process:**
   ```bash
   kill <PID>
   # Or force kill:
   kill -9 <PID>
   ```

3. **Or kill all Python processes (use with caution):**
   ```bash
   pkill -f "python.*main.py"
   ```

## If Running in Background

If you started it in the background with `&` or `nohup`:

1. **Find the job:**
   ```bash
   jobs
   ```

2. **Bring to foreground and stop:**
   ```bash
   fg
   # Then press Ctrl+C
   ```

3. **Or kill by process name:**
   ```bash
   pkill -f "python.*main.py"
   ```

## Verifying It's Stopped

Check if the process is still running:

```bash
ps aux | grep "python.*main.py"
```

If you see no results (except the `grep` command itself), it's stopped.

## Restarting

To start again:

```bash
cd /Users/kevinmcgovern/gekko3/brain
python3 main.py
```
