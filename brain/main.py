"""
Gekko3 Brain - Main Entry Point with Market Supervisor
The Strategy Engine that runs locally and sends proposals to the Cloudflare Gatekeeper
Includes intelligent market hours detection and connection management
"""

import asyncio
import logging
import signal
import sys
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Load environment variables FIRST (before any other imports that might need them)
load_dotenv()

from src.alpha_engine import AlphaEngine
from src.market_feed import MarketFeed
from src.gatekeeper_client import GatekeeperClient
from src.regime_engine import RegimeEngine
from src.notifier import get_notifier

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


class BrainSupervisor:
    """Supervisor that manages market hours and feed lifecycle"""

    def __init__(self):
        self.running = True
        self.tz = ZoneInfo("America/New_York")
        
        # Discord Notifier
        self.notifier = get_notifier()
        
        # Components
        logging.info("🧠 Initializing Gekko3 Brain (Supervisor Mode)...")
        self.gatekeeper = GatekeeperClient()
        logging.info("✅ Gatekeeper Client initialized")
        
        self.alpha_engine = AlphaEngine(lookback_minutes=400)  # Increased to support SMA(200) + buffer
        logging.info("✅ Alpha Engine initialized")
        
        self.regime_engine = RegimeEngine(self.alpha_engine)
        logging.info("✅ Regime Engine initialized")
        
        self.market_feed = MarketFeed(
            alpha_engine=self.alpha_engine,
            gatekeeper_client=self.gatekeeper,
            regime_engine=self.regime_engine,
            symbols=['SPY', 'QQQ', 'IWM', 'DIA']
        )
        logging.info("✅ Market Feed initialized")
        
        # Market schedule (ET timezone)
        self.market_open = time(9, 30)
        self.market_close = time(16, 0)
        self.pre_market_buffer = time(9, 25)  # Connect 5 mins early
        self.post_market_buffer = time(16, 5)  # Disconnect 5 mins late
        
        # Feed task tracking
        self.feed_task = None
        
        # State tracking for notifications (avoid duplicates)
        self.last_market_state = None
        
        # Heartbeat timing (Phase C: Final Polish)
        self.last_heartbeat_time = 0

    def is_market_hours(self):
        """
        Returns tuple (should_run, reason)
        Determines if we should be connected based on market hours
        """
        now = datetime.now(self.tz)
        
        # 1. Check Weekend (5=Sat, 6=Sun)
        if now.weekday() >= 5:
            return False, "Weekend"

        # 2. Check Time
        current_time = now.time()
        if self.pre_market_buffer <= current_time <= self.post_market_buffer:
            return True, "Market Open"
        
        # Before pre-market buffer
        if current_time < self.pre_market_buffer:
            return False, "Pre-Market"
        
        # After post-market buffer
        return False, "Post-Market"

    async def run(self):
        """Main supervisor loop"""
        logging.info("🚀 Supervisor started - Monitoring market hours...")
        logging.info(f"   Market Hours: {self.pre_market_buffer} - {self.post_market_buffer} ET")
        logging.info(f"   Timezone: {self.tz}")
        
        # Notify startup
        await self.notifier.send_success(
            "🧠 **Gekko3 Brain is ONLINE**\n\n"
            f"Mode: Supervisor\n"
            f"Market Hours: {self.pre_market_buffer.strftime('%H:%M')} - {self.post_market_buffer.strftime('%H:%M')} ET\n"
            f"Timezone: {self.tz}",
            title="Brain Startup"
        )
        
        while self.running:
            # Check shutdown flag at start of each iteration
            if not self.running:
                break
                
            should_run, reason = self.is_market_hours()
            
            if should_run:
                # Market is open - ensure feed is running
                # Check shutdown flag before starting feed
                if not self.running:
                    break
                    
                if not self.market_feed.is_connected:
                    logging.info(f"🟢 {reason}: Starting Market Feed...")
                    # Notify market open (only when state changes)
                    if self.last_market_state != "Market Open":
                        await self.notifier.send_success(
                            f"🟢 **Market Open**\n\nConnecting to market feed...\n"
                            f"Time: {datetime.now(self.tz).strftime('%H:%M:%S %Z')}",
                            title="Market State"
                        )
                        self.last_market_state = "Market Open"
                    # Run the feed in a separate task so we can monitor it
                    self.feed_task = asyncio.create_task(self.market_feed.connect())
                elif self.feed_task and self.feed_task.done():
                    # Check shutdown before restarting
                    if not self.running:
                        break
                    # Monitor the feed task (restart if it crashed)
                    try:
                        self.feed_task.result()  # Raise exception if one occurred
                    except Exception as e:
                        if self.running:  # Only restart if still running
                            logging.error(f"❌ Feed crashed: {e}. Restarting...")
                            self.feed_task = asyncio.create_task(self.market_feed.connect())
                
                # Send heartbeat every minute with rich state (Phase C: Final Polish)
                now_ts = datetime.now(self.tz).timestamp()
                if now_ts - self.last_heartbeat_time >= 60:  # Every 60 seconds
                    try:
                        # Collect Rich State (safe access to avoid crashes during startup)
                        regime_val = "UNKNOWN"
                        try:
                            regime_val = self.regime_engine.get_regime('SPY').value
                        except:
                            pass
                        
                        iv_rank_val = 0
                        try:
                            iv_rank_val = self.alpha_engine.get_iv_rank('SPY')
                        except:
                            pass
                        
                        brain_state = {
                            'regime': regime_val,
                            'greeks': self.market_feed.portfolio_greeks,  # Live Delta/Theta/Vega
                            'iv_rank_spy': iv_rank_val
                        }
                        
                        # Send to Gatekeeper
                        await self.gatekeeper.send_heartbeat(brain_state)
                        self.last_heartbeat_time = now_ts
                        logging.debug("💓 Heartbeat sent with rich state")
                    except Exception as e:
                        # Fallback to simple heartbeat if state collection fails
                        try:
                            await self.gatekeeper.send_heartbeat()
                            self.last_heartbeat_time = now_ts
                        except:
                            pass
                        logging.debug(f"Heartbeat failed (non-critical): {e}")
                
                # Pulse check every minute during market hours (with shutdown checks)
                if not self.running:
                    break
                # Sleep in 1-second chunks to allow immediate shutdown response
                for _ in range(60):
                    if not self.running:
                        break
                    await asyncio.sleep(1)
            else:
                # Market is closed - ensure feed is stopped
                if self.market_feed.is_connected:
                    logging.info(f"🔴 {reason}: Stopping Market Feed...")
                    # Notify market closed (only when state changes)
                    if self.last_market_state != reason:
                        await self.notifier.send_warning(
                            f"🔴 **Market Closed**\n\n{reason}\n"
                            f"Time: {datetime.now(self.tz).strftime('%H:%M:%S %Z')}\n"
                            f"Feed disconnected.",
                            title="Market State"
                        )
                        self.last_market_state = reason
                    await self.market_feed.disconnect()
                    
                    # Wait for feed task to complete
                    if self.feed_task and not self.feed_task.done():
                        try:
                            await asyncio.wait_for(self.feed_task, timeout=5.0)
                        except asyncio.TimeoutError:
                            logging.warning("Feed task did not complete in time")
                    
                    self.feed_task = None
                
                # Check shutdown before sleeping
                if not self.running:
                    break
                
                # Determine sleep duration based on reason
                if reason == "Weekend":
                    sleep_seconds = 4 * 60 * 60  # 4 hours on weekends
                    logging.info(f"💤 {reason}. Sleeping for 4 hours...")
                    # Notify weekend mode (only once when state changes)
                    if self.last_market_state != "Weekend":
                        await self.notifier.send_info(
                            f"💤 **Weekend Mode**\n\nSleeping for 4 hours...\n"
                            f"Will reconnect on Monday at {self.pre_market_buffer.strftime('%H:%M')} ET",
                            title="Market State"
                        )
                        self.last_market_state = "Weekend"
                elif reason == "Pre-Market":
                    sleep_seconds = 5 * 60  # 5 minutes before market
                    logging.info(f"💤 {reason}. Checking again in 5 minutes...")
                else:  # Post-Market
                    # Calculate seconds until next market open (tomorrow 9:25 AM)
                    now = datetime.now(self.tz)
                    tomorrow = now.replace(hour=9, minute=25, second=0, microsecond=0) + timedelta(days=1)
                    sleep_seconds = (tomorrow - now).total_seconds()
                    logging.info(f"💤 {reason}. Sleeping until tomorrow 9:25 AM ET...")
                
                # Sleep in chunks to allow immediate shutdown response
                max_sleep = min(sleep_seconds, 3600)  # Max 1 hour checks
                slept = 0
                while slept < max_sleep and self.running:
                    chunk = min(10, max_sleep - slept)  # Check every 10 seconds
                    await asyncio.sleep(chunk)
                    slept += chunk

    async def shutdown(self):
        """Graceful shutdown"""
        if self.running:
            logging.info("\n🛑 Shutdown requested...")
            
            # Notify shutdown
            await self.notifier.send_error(
                "🛑 **Gekko3 Brain is SHUTTING DOWN**\n\n"
                f"Time: {datetime.now(self.tz).strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
                "Disconnecting from market feed...",
                title="Brain Shutdown"
            )
            
            self.running = False
            
            # Immediately stop the feed
            if self.market_feed.is_connected:
                logging.info("🔌 Stopping Market Feed...")
                await self.market_feed.disconnect()
            
            # Cancel the feed task if it's running
            if self.feed_task and not self.feed_task.done():
                logging.info("⏹️  Cancelling feed task...")
                self.feed_task.cancel()
                try:
                    await self.feed_task
                except asyncio.CancelledError:
                    pass


async def main():
    """Main entry point"""
    brain = None
    
    try:
        brain = BrainSupervisor()
        
        # Handle Ctrl+C - use event to trigger async shutdown
        shutdown_event = asyncio.Event()
        
        def signal_handler(sig, frame):
            logging.info("\n⚠️  Received shutdown signal")
            shutdown_event.set()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Run supervisor in background task
        supervisor_task = asyncio.create_task(brain.run())
        
        # Wait for shutdown signal or supervisor completion
        try:
            done, pending = await asyncio.wait(
                [supervisor_task, asyncio.create_task(shutdown_event.wait())],
                return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            # Shutdown was requested via signal
            if shutdown_event.is_set():
                await brain.shutdown()
                if not supervisor_task.done():
                    supervisor_task.cancel()
                    try:
                        await supervisor_task
                    except asyncio.CancelledError:
                        pass
        
    except KeyboardInterrupt:
        logging.info("\n⚠️  Interrupted by user")
        if brain:
            brain.shutdown()
    except Exception as e:
        logging.error(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        if brain:
            brain.shutdown()
    finally:
        # Final cleanup (additional safety)
        if brain and brain.running:
            await brain.shutdown()
            
        logging.info("👋 Goodbye!")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
        sys.exit(0)
