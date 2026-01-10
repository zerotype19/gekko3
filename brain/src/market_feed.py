"""
Market Feed
Connects to Tradier WebSocket and feeds data to AlphaEngine
Generates trading signals based on technical indicators
"""

import asyncio
import json
import websockets
import aiohttp
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Optional, Set
from dotenv import load_dotenv

from src.alpha_engine import AlphaEngine
from src.gatekeeper_client import GatekeeperClient
from src.notifier import get_notifier

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Tradier WebSocket URLs
TRADIER_WS_URL = "wss://ws.tradier.com/v1/markets/events"
# Note: Session creation must use PRODUCTION API even for streaming
# Sandbox accounts do not support WebSocket streaming
TRADIER_SESSION_URL = "https://api.tradier.com/v1/markets/events/session"

# Tradier REST API URLs (for VIX polling)
TRADIER_API_BASE = "https://api.tradier.com/v1"


class MarketFeed:
    """Connects to Tradier WebSocket and processes market data"""

    def __init__(
        self,
        alpha_engine: AlphaEngine,
        gatekeeper_client: GatekeeperClient,
        symbols: list = None
    ):
        """
        Initialize the Market Feed
        
        Args:
            alpha_engine: AlphaEngine instance for calculations
            gatekeeper_client: GatekeeperClient for sending proposals
            symbols: List of symbols to subscribe to (default: ['SPY', 'QQQ'])
            
        Note: Requires PRODUCTION Tradier token for WebSocket streaming.
        Sandbox accounts do not support WebSocket streaming - only REST API.
        """
        self.alpha_engine = alpha_engine
        self.gatekeeper_client = gatekeeper_client
        self.symbols = symbols or ['SPY', 'QQQ']
        
        # Get Tradier access token (MUST be production token for streaming)
        self.access_token = os.getenv('TRADIER_ACCESS_TOKEN', '')
        if not self.access_token:
            raise ValueError('TRADIER_ACCESS_TOKEN must be set in .env (use PRODUCTION token for streaming)')
        
        # WebSocket connection
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.connected = False
        self.is_connected = False  # Public property for supervisor
        self.stop_signal = False  # Control flag for graceful shutdown
        
        # Rate limiting: track last proposal time per symbol
        self.last_proposal_time: Dict[str, datetime] = {}
        self.min_proposal_interval = timedelta(minutes=1)
        
        # Signal tracking to avoid duplicate signals
        self.last_signals: Dict[str, Dict] = {}
        
        # Trend tracking for notifications
        self.last_trend: Dict[str, str] = {}
        
        # VIX polling task
        self.vix_poller_task: Optional[asyncio.Task] = None
        self.vix_poller_running = False
        
        # Discord Notifier
        self.notifier = get_notifier()

    async def _poll_vix_loop(self):
        """
        Poll VIX from Tradier REST API every 60 seconds
        VIX cannot be streamed via WebSocket, so we poll it separately
        """
        self.vix_poller_running = True
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Accept': 'application/json'
        }
        
        logging.info("📊 VIX poller started (60s interval)")
        
        while self.vix_poller_running and not self.stop_signal:
            try:
                async with aiohttp.ClientSession() as session:
                    url = f'{TRADIER_API_BASE}/markets/quotes'
                    params = {'symbols': 'VIX'}
                    
                    async with session.get(url, headers=headers, params=params) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            # Parse Tradier response format
                            quotes = data.get('quotes', {})
                            quote = quotes.get('quote', None)
                            
                            if quote:
                                # Handle both single quote and array response
                                if isinstance(quote, list):
                                    quote = quote[0]
                                
                                vix_value = quote.get('last', None)
                                if vix_value is not None:
                                    vix_value = float(vix_value)
                                    self.alpha_engine.set_vix(vix_value, datetime.now())
                                    logging.debug(f"📊 VIX updated: {vix_value:.2f}")
                                else:
                                    logging.warning("⚠️  VIX quote missing 'last' price")
                            else:
                                logging.warning(f"⚠️  No VIX quote in response: {data}")
                        else:
                            error_text = await resp.text()
                            logging.error(f"❌ Failed to fetch VIX: {resp.status} - {error_text}")
                            
            except Exception as e:
                logging.error(f"❌ VIX poller error: {e}")
            
            # Sleep 60 seconds (with stop signal checks every 10s)
            for _ in range(6):
                if self.stop_signal or not self.vix_poller_running:
                    break
                await asyncio.sleep(10)
        
        logging.info("📊 VIX poller stopped")

    async def _create_session(self) -> Optional[str]:
        """
        Creates a streaming session via HTTP to get a sessionid.
        This is REQUIRED before connecting to WebSocket.
        Note: Must use PRODUCTION API URL even for data streaming.
        """
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Accept': 'application/json'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(TRADIER_SESSION_URL, headers=headers) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logging.error(f"Failed to create session: {resp.status} - {text}")
                        return None
                    
                    data = await resp.json()
                    session_id = data.get('stream', {}).get('sessionid')
                    if not session_id:
                        logging.error(f"No sessionid in response: {data}")
                        return None
                    
                    logging.info(f"✅ Session created: {session_id[:8]}...")
                    return session_id
                    
        except Exception as e:
            logging.error(f"Error creating Tradier session: {e}")
            return None

    async def connect(self):
        """Connect to Tradier WebSocket with proper session initialization"""
        self.stop_signal = False
        self.is_connected = False
        
        # Session creation loop with retry
        while not self.stop_signal:
            if self.stop_signal:
                break
                
            logging.info("🔌 Creating Market Session...")
            session_id = await self._create_session()
            
            if not session_id:
                if self.stop_signal:
                    break
                logging.error("❌ Could not create session. Retrying in 10s...")
                await asyncio.sleep(10)
                continue
            
            if self.stop_signal:
                break
            
            logging.info("🔑 Session Created. Connecting to WebSocket...")
            
            try:
                # Start VIX poller as background task
                if not self.vix_poller_running:
                    self.vix_poller_task = asyncio.create_task(self._poll_vix_loop())
                    logging.info("📊 Started VIX poller")
                
                # Connect to WebSocket (no auth headers needed, session ID is used)
                async with websockets.connect(
                    TRADIER_WS_URL,
                    ping_interval=20,
                    ping_timeout=20
                ) as websocket:
                    self.ws = websocket
                    self.connected = True
                    self.is_connected = True
                    logging.info("✅ Connected to Tradier WebSocket")
                    
                    # Subscribe to symbols
                    await self._subscribe(session_id)
                    
                    # Run the message loop
                    await self.run(websocket)
                    
            except websockets.exceptions.ConnectionClosed as e:
                self.connected = False
                self.is_connected = False
                if not self.stop_signal:
                    logging.warning(f"⚠️  WebSocket disconnected: {e}. Reconnecting...")
                    await asyncio.sleep(2)
                    continue
                else:
                    logging.info("🔌 WebSocket closed (shutdown requested)")
                    break
            except Exception as e:
                self.connected = False
                self.is_connected = False
                if not self.stop_signal:
                    logging.error(f"❌ WebSocket error: {e}")
                    await asyncio.sleep(5)
                    continue
                else:
                    break
        
        # Stop VIX poller
        self.vix_poller_running = False
        if self.vix_poller_task and not self.vix_poller_task.done():
            self.vix_poller_task.cancel()
            try:
                await self.vix_poller_task
            except asyncio.CancelledError:
                pass
        
        # Clean shutdown
        self.connected = False
        self.is_connected = False
        logging.info("🔌 Market Feed connection loop ended")

    async def _subscribe(self, session_id: str):
        """Subscribe to trade and quote events for symbols"""
        if not self.ws:
            return

        # Tradier WebSocket subscription format
        subscription = {
            "symbols": self.symbols,
            "filter": ["trade", "quote"],
            "sessionid": session_id
        }
        
        try:
            await self.ws.send(json.dumps(subscription))
            logging.info(f"📡 Subscribed to: {', '.join(self.symbols)}")
        except Exception as e:
            logging.error(f"⚠️  Subscription error: {e}")

    async def _handle_message(self, data: dict):
        """Process incoming WebSocket message"""
        try:
            # Handle different message types
            msg_type = data.get('type', '')
            
            if msg_type == 'trade':
                await self._handle_trade(data)
                # Check for signals after trade update
                symbol = data.get('symbol')
                if symbol:
                    await self._check_signals(symbol)
            elif msg_type == 'quote':
                await self._handle_quote(data)
            elif msg_type == 'ping':
                await self._send_pong()
            elif 'error' in data:
                logging.error(f"❌ WebSocket error: {data.get('error')}")
            else:
                # Log unknown message types for debugging
                logging.debug(f"Unknown message type: {msg_type}")
                    
        except Exception as e:
            logging.error(f"⚠️  Error handling message: {e}")

    async def _handle_trade(self, data: dict):
        """Handle trade event"""
        # Extract trade data from Tradier WebSocket format
        symbol = data.get('symbol', '')
        price = float(data.get('price', data.get('last', 0)))
        size = int(data.get('size', data.get('volume', 1)))
        timestamp_str = data.get('date', data.get('time', ''))
        
        if not symbol or price <= 0:
            return
        
        # Parse timestamp
        try:
            if timestamp_str:
                # Tradier timestamp format: ISO 8601
                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            else:
                timestamp = datetime.now()
        except:
            timestamp = datetime.now()
        
        # Update alpha engine with trade data
        self.alpha_engine.update(symbol, price, size, timestamp)

    async def _handle_quote(self, data: dict):
        """Handle quote event"""
        # Extract quote data
        symbol = data.get('symbol', '')
        bid = float(data.get('bid', 0))
        ask = float(data.get('ask', 0))
        
        if not symbol or (bid == 0 and ask == 0):
            return
        
        # Use mid price for quotes
        price = (bid + ask) / 2 if (bid > 0 and ask > 0) else (bid or ask)
        volume = 0  # Quotes don't have volume, only use for price updates
        
        # Update alpha engine with quote data (minimal volume impact)
        self.alpha_engine.update(symbol, price, volume)

    async def _send_pong(self):
        """Respond to ping with pong"""
        if self.ws:
            try:
                await self.ws.send(json.dumps({"type": "pong"}))
            except:
                pass

    async def _check_signals(self, symbol: str):
        """Check for trading signals and send proposals if conditions are met"""
        if not symbol or symbol not in self.symbols:
            return
        
        # Rate limiting check
        now = datetime.now()
        if symbol in self.last_proposal_time:
            time_since_last = now - self.last_proposal_time[symbol]
            if time_since_last < self.min_proposal_interval:
                return  # Skip if too soon
        
        # Get indicators
        indicators = self.alpha_engine.get_indicators(symbol)
        
        # Warmup mode enforcement: Need sufficient data AND VIX
        if not indicators.get('is_warm', False):
            # Not ready - missing SMA data or VIX
            if indicators.get('sma_200') is None:
                return  # Skip if SMA not available (need 200 candles)
            if indicators.get('vix') is None:
                logging.debug(f"⏳ {symbol}: Waiting for VIX data...")
                return  # Skip if VIX not available yet
        
        if indicators['candle_count'] < 30:  # Need minimum candles for RSI
            return
        
        flow_state = indicators['flow_state']
        trend = indicators['trend']
        rsi = indicators['rsi']
        vix = indicators.get('vix')
        
        # Track trend changes for notifications
        if symbol in self.last_trend and self.last_trend[symbol] != trend:
            # Trend changed - notify
            trend_emoji = "📈" if trend == "UPTREND" else "📉" if trend == "DOWNTREND" else "⏳"
            await self.notifier.send_info(
                f"{trend_emoji} **Trend Changed: {symbol}**\n\n"
                f"**{self.last_trend[symbol]}** → **{trend}**\n"
                f"Price: ${indicators['price']:.2f}\n"
                f"SMA 200: ${indicators.get('sma_200', 'N/A')}\n"
                f"VIX: {vix:.2f if vix else 'N/A'}",
                title="Trend Change"
            )
        
        self.last_trend[symbol] = trend
        
        # Additional safety: Reject if VIX is missing (shouldn't happen if is_warm, but double-check)
        if vix is None:
            if indicators.get('candle_count', 0) % 60 == 0:  # Log every minute to avoid spam
                logging.warning(f"⚠️  {symbol}: VIX missing - system in warmup mode, skipping signals")
            return
        
        # Signal logic (The "Tier A" Setup)
        signal = None
        strategy = None
        side = None
        option_type = None
        
        # Only generate signals with valid trend (not INSUFFICIENT_DATA)
        if trend == 'INSUFFICIENT_DATA':
            return  # Skip signals if trend not available
        
        # Bull Put Spread (credit spread on puts) - when oversold in uptrend
        if trend == 'UPTREND' and rsi < 30 and flow_state != 'NEUTRAL':
            signal = 'BULL_PUT_SPREAD'
            strategy = 'CREDIT_SPREAD'
            side = 'SELL'
            option_type = 'PUT'
            bias = 'bullish'
        
        # Bear Call Spread (credit spread on calls) - when overbought in downtrend
        elif trend == 'DOWNTREND' and rsi > 70 and flow_state != 'NEUTRAL':
            signal = 'BEAR_CALL_SPREAD'
            strategy = 'CREDIT_SPREAD'
            side = 'SELL'
            option_type = 'CALL'
            bias = 'bearish'
        
        # Check if signal changed (avoid duplicate signals)
        last_signal = self.last_signals.get(symbol, {})
        if signal and last_signal.get('signal') == signal:
            # Same signal as before, skip
            return
        
        if signal:
            logging.info(f"🎯 Signal detected for {symbol}: {signal}")
            logging.info(f"   Trend: {trend}, RSI: {rsi:.2f}, Flow: {flow_state}, VIX: {vix:.2f}")
            
            # Notify signal detection
            signal_emoji = "🚨"
            await self.notifier.send_success(
                f"{signal_emoji} **SIGNAL DETECTED** {signal_emoji}\n\n"
                f"**Symbol:** {symbol}\n"
                f"**Strategy:** {signal}\n"
                f"**Side:** {side} {option_type}\n\n"
                f"**Indicators:**\n"
                f"• Trend: {trend}\n"
                f"• RSI: {rsi:.2f}\n"
                f"• Flow: {flow_state}\n"
                f"• VIX: {vix:.2f}\n"
                f"• Price: ${indicators['price']:.2f}",
                title="Trade Signal"
            )
            
            # Send proposal to gatekeeper (with real VIX)
            await self._send_proposal(symbol, strategy, side, option_type, indicators, bias)
            
            # Update rate limiting and signal tracking
            self.last_proposal_time[symbol] = now
            self.last_signals[symbol] = {
                'signal': signal,
                'timestamp': now,
                'indicators': indicators
            }

    async def _send_proposal(
        self,
        symbol: str,
        strategy: str,
        side: str,
        option_type: str,
        indicators: dict,
        bias: str
    ):
        """Send a trade proposal to the Gatekeeper"""
        # For V1, create a simplified proposal
        # In production, this would need actual option chain data to construct legs
        
        # Mock option legs (in production, fetch from Tradier option chain)
        # This is a placeholder - real implementation needs:
        # 1. Fetch option chain for symbol
        # 2. Select strikes based on delta/DTE requirements
        # 3. Construct proper SpreadLeg objects
        
        from datetime import datetime, timedelta
        
        # Calculate expiration date (next Friday, or 1-7 DTE)
        today = datetime.now()
        days_until_friday = (4 - today.weekday()) % 7
        if days_until_friday == 0:
            days_until_friday = 7  # If today is Friday, use next Friday
        expiration_date = today + timedelta(days=days_until_friday)
        
        # Adjust to ensure 1-7 DTE
        if days_until_friday < 1:
            expiration_date = today + timedelta(days=1)
        elif days_until_friday > 7:
            expiration_date = today + timedelta(days=7)
        
        current_price = indicators['price']
        
        # Mock strikes (in production, calculate proper strikes from option chain)
        if option_type == 'PUT':
            # Bull Put Spread: Sell higher strike, buy lower strike
            sell_strike = int(current_price * 0.98)  # 2% OTM
            buy_strike = int(current_price * 0.96)   # 4% OTM
        else:  # CALL
            # Bear Call Spread: Sell lower strike, buy higher strike
            sell_strike = int(current_price * 1.02)  # 2% OTM
            buy_strike = int(current_price * 1.04)   # 4% OTM
        
        # Create proposal
        proposal = {
            'symbol': symbol,
            'strategy': strategy,
            'side': side,
            'quantity': 1,  # Default to 1 contract
            'legs': [
                {
                    'symbol': f"{symbol}{expiration_date.strftime('%y%m%d')}{option_type[0]}{sell_strike:08d}",
                    'expiration': expiration_date.strftime('%Y-%m-%d'),
                    'strike': sell_strike,
                    'type': option_type,
                    'quantity': 1,
                    'side': 'SELL'
                },
                {
                    'symbol': f"{symbol}{expiration_date.strftime('%y%m%d')}{option_type[0]}{buy_strike:08d}",
                    'expiration': expiration_date.strftime('%Y-%m-%d'),
                    'strike': buy_strike,
                    'type': option_type,
                    'quantity': 1,
                    'side': 'BUY'
                }
            ],
            'context': {
                'vix': indicators.get('vix'),  # Real VIX value from poller (None if not available)
                'flow_state': indicators['flow_state'].lower(),
                'trend_state': bias,
                'vol_state': 'normal',  # Placeholder for V1
                'rsi': indicators['rsi'],
                'vwap': indicators['vwap'],
                'volume_velocity': indicators['volume_velocity'],
                'imbalance_score': 0,  # Placeholder for V1
                'sma_200': indicators.get('sma_200'),
                'candle_count': indicators.get('candle_count', 0)
            }
        }
        
        try:
            result = await self.gatekeeper_client.send_proposal(proposal)
            status = result.get('status')
            logging.info(f"📤 Proposal sent to Gatekeeper: {status}")
            
            if status == 'REJECTED':
                reason = result.get('reason', 'Unknown')
                logging.warning(f"   Reason: {reason}")
                # Notify rejection
                await self.notifier.send_warning(
                    f"❌ **Proposal REJECTED: {symbol}**\n\n"
                    f"**Strategy:** {strategy}\n"
                    f"**Reason:** {reason}",
                    title="Proposal Rejected"
                )
            elif status == 'APPROVED':
                order_id = result.get('data', {}).get('order_id', 'N/A')
                logging.info(f"   ✅ Order ID: {order_id}")
                # Notify approval
                await self.notifier.send_success(
                    f"✅ **Proposal APPROVED: {symbol}**\n\n"
                    f"**Strategy:** {strategy}\n"
                    f"**Order ID:** {order_id}",
                    title="Trade Executed"
                )
        except Exception as e:
            logging.error(f"❌ Failed to send proposal: {e}")
            # Notify error
            await self.notifier.send_error(
                f"❌ **Proposal Error: {symbol}**\n\n"
                f"**Strategy:** {strategy}\n"
                f"**Error:** {str(e)}",
                title="Proposal Error"
            )

    async def run(self, websocket):
        """Main run loop - receives and processes messages"""
        logging.info("🚀 Market Feed running...")
        logging.info(f"   Monitoring: {', '.join(self.symbols)}")
        
        try:
            async for message in websocket:
                # Check stop signal
                if self.stop_signal:
                    logging.info("🛑 Stop signal received, closing connection...")
                    break
                    
                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError as e:
                    logging.warning(f"Failed to parse message: {e}")
                except Exception as e:
                    logging.error(f"Error processing message: {e}")
        except websockets.exceptions.ConnectionClosed:
            if not self.stop_signal:
                logging.warning("⚠️  WebSocket connection closed unexpectedly")
            self.connected = False
            self.is_connected = False
        except Exception as e:
            logging.error(f"❌ Error in run loop: {e}")
            self.connected = False
            self.is_connected = False
            if not self.stop_signal:
                raise

    async def disconnect(self):
        """Disconnect from WebSocket gracefully"""
        logging.info("🔌 Disconnect requested...")
        self.stop_signal = True
        self.is_connected = False
        self.vix_poller_running = False
        
        # Stop VIX poller
        if self.vix_poller_task and not self.vix_poller_task.done():
            self.vix_poller_task.cancel()
            try:
                await self.vix_poller_task
            except asyncio.CancelledError:
                pass
        
        if self.ws:
            try:
                await self.ws.close()
                logging.info("✅ WebSocket closed")
            except Exception as e:
                logging.warning(f"Error closing WebSocket: {e}")
        
        self.connected = False
        logging.info("🔌 Disconnected from Market Feed")

