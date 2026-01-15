"""
Market Feed (Production Grade)
Connects to Tradier WebSocket and feeds data to AlphaEngine
Generates trading signals based on technical indicators
Includes: Real-Time Pricing + Dynamic Expiration + Delta Strike Selection
Includes: Order Verification & Retry Logic
"""

import asyncio
import json
import websockets
import aiohttp
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Dict, Optional, Set, List
from dotenv import load_dotenv

from src.alpha_engine import AlphaEngine
from src.gatekeeper_client import GatekeeperClient
from src.notifier import get_notifier
from src.position_sizer import PositionSizer

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Tradier WebSocket URLs
TRADIER_WS_URL = "wss://ws.tradier.com/v1/markets/events"
TRADIER_SESSION_URL = "https://api.tradier.com/v1/markets/events/session"
TRADIER_API_BASE = "https://api.tradier.com/v1"


class MarketFeed:
    """Connects to Tradier WebSocket and processes market data"""

    def __init__(
        self,
        alpha_engine: AlphaEngine,
        gatekeeper_client: GatekeeperClient,
        regime_engine,
        symbols: list = None
    ):
        self.alpha_engine = alpha_engine
        self.gatekeeper_client = gatekeeper_client
        self.regime_engine = regime_engine
        self.symbols = symbols or ['SPY', 'QQQ', 'IWM', 'DIA']
        
        self.access_token = os.getenv('TRADIER_ACCESS_TOKEN', '')
        if not self.access_token:
            raise ValueError('TRADIER_ACCESS_TOKEN must be set in .env')
        
        # For order status checks, we need SANDBOX token (where orders are executed)
        # Brain uses PRODUCTION token for WebSocket, but Gatekeeper uses SANDBOX for execution
        self.sandbox_token = os.getenv('TRADIER_SANDBOX_TOKEN', '')
        if not self.sandbox_token:
            # Fallback to known sandbox token
            self.sandbox_token = 'XFE6d2z7hJnleNbpQ789otJmvW3z'
            logging.warning("⚠️ Using hardcoded sandbox token for order checks. Set TRADIER_SANDBOX_TOKEN in .env")
        
        self.account_id = None  # Fetched on connect (SANDBOX account)
        
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.connected = False
        self.is_connected = False
        self.stop_signal = False
        
        self.last_proposal_time: Dict[str, datetime] = {}
        self.min_proposal_interval = timedelta(minutes=1)
        self.last_signals: Dict[str, Dict] = {}
        self.last_trend: Dict[str, str] = {}
        
        self.vix_poller_task: Optional[asyncio.Task] = None
        self.vix_poller_running = False
        
        # IV Poller (for IV Rank calculation)
        self.iv_poller_task: Optional[asyncio.Task] = None
        
        # Connection Watchdog (Dead Man's Switch)
        self.last_msg_time = datetime.now()
        self.watchdog_task: Optional[asyncio.Task] = None
        
        # Position Management (Smart Manager)
        self.open_positions: Dict[str, Dict] = {}
        self.position_manager_task: Optional[asyncio.Task] = None
        self._needs_entry_price_recalc = False  # Flag to force recalculation on first sync
        
        # Portfolio Greeks
        self.portfolio_greeks = {'delta': 0.0, 'theta': 0.0, 'vega': 0.0}
        
        # Position Sizing (Professional Grade)
        self.position_sizer = PositionSizer()
        
        # Dashboard state export
        current_dir = os.getcwd()
        if current_dir.endswith('brain'):
            self.state_file = os.path.join(os.path.dirname(current_dir), 'brain_state.json')
            self.positions_file = os.path.join(os.path.dirname(current_dir), 'brain_positions.json')
        else:
            self.state_file = 'brain_state.json'
            self.positions_file = 'brain_positions.json'
        
        # Load positions from disk on startup (survive restarts)
        self._load_positions_from_disk()
        
        # Startup Reconciliation (Adopt Orphans from Tradier)
        # Run this asynchronously on first connect to avoid blocking init
        self._needs_reconciliation = True

    # --- PERSISTENCE ---
    def _save_positions_to_disk(self):
        """Persist open positions to disk to survive restarts"""
        try:
            with open(self.positions_file, 'w') as f:
                serializable = {}
                for k, v in self.open_positions.items():
                    serializable[k] = v.copy()
                    if isinstance(v.get('timestamp'), datetime):
                        serializable[k]['timestamp'] = v['timestamp'].isoformat()
                    # Also save closing metadata
                    if isinstance(v.get('closing_timestamp'), datetime):
                        serializable[k]['closing_timestamp'] = v['closing_timestamp'].isoformat()
                    if isinstance(v.get('opening_timestamp'), datetime):
                        serializable[k]['opening_timestamp'] = v['opening_timestamp'].isoformat()
                json.dump(serializable, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save positions: {e}")

    def _load_positions_from_disk(self):
        """Load positions from disk on startup"""
        if not os.path.exists(self.positions_file):
            return
        
        try:
            with open(self.positions_file, 'r') as f:
                data = json.load(f)
                for k, v in data.items():
                    # Restore datetime objects
                    if 'timestamp' in v and isinstance(v['timestamp'], str):
                        v['timestamp'] = datetime.fromisoformat(v['timestamp'])
                    if 'closing_timestamp' in v and isinstance(v['closing_timestamp'], str):
                        v['closing_timestamp'] = datetime.fromisoformat(v['closing_timestamp'])
                    if 'opening_timestamp' in v and isinstance(v['opening_timestamp'], str):
                        v['opening_timestamp'] = datetime.fromisoformat(v['opening_timestamp'])
                    
                    # Ensure status is set (recovered positions might not have it)
                    if 'status' not in v or v.get('status') is None:
                        v['status'] = 'OPEN'  # Default to OPEN for recovered positions
                    
                    # Initialize live_greeks if missing (will be calculated on next _manage_positions cycle)
                    if 'live_greeks' not in v:
                        v['live_greeks'] = {'delta': 0.0, 'theta': 0.0, 'vega': 0.0}
                    
                    self.open_positions[k] = v
                if self.open_positions:
                    logging.info(f"♻️ Restored {len(self.open_positions)} positions from disk.")
        except Exception as e:
            logging.error(f"Failed to load positions: {e}")

    def export_state(self):
        """Dumps RICH brain state to JSON for the dashboard"""
        # 1. Global State
        regime = 'UNKNOWN'
        try: 
            regime = self.regime_engine.get_regime('SPY').value
        except: 
            pass

        # Serialize positions for dashboard (exclude datetime objects)
        # CRITICAL: For MANUAL_RECOVERY positions, always read entry_price from disk
        # to ensure we have the latest corrected value
        disk_positions = {}
        if os.path.exists(self.positions_file):
            try:
                with open(self.positions_file, 'r') as f:
                    disk_positions = json.load(f)
            except:
                pass
        
        serialized_positions = []
        for trade_id, pos in self.open_positions.items():
            # Include all positions (OPEN, OPENING, CLOSING, or no status for recovered)
            # For MANUAL_RECOVERY, use disk value if available (more up-to-date)
            entry_price = pos.get('entry_price', 0)
            if pos.get('strategy') == 'MANUAL_RECOVERY' and trade_id in disk_positions:
                disk_entry = disk_positions[trade_id].get('entry_price', 0)
                if disk_entry > 0 and abs(disk_entry - entry_price) > 0.01:
                    # Disk has different value, use it and update in-memory
                    entry_price = disk_entry
                    pos['entry_price'] = entry_price
                    logging.debug(f"📝 Using disk entry_price for {trade_id}: ${entry_price:.2f}")
            
            serialized = {
                'trade_id': trade_id,
                'symbol': pos.get('symbol', 'UNKNOWN'),
                'strategy': pos.get('strategy', 'UNKNOWN'),
                'status': pos.get('status', 'OPEN'),  # Default to OPEN if missing (recovered positions)
                'entry_price': entry_price,
                'bias': pos.get('bias', 'neutral'),
                'legs_count': len(pos.get('legs', [])),
                'timestamp': pos.get('timestamp', '').isoformat() if isinstance(pos.get('timestamp'), datetime) else pos.get('timestamp', ''),
            }
            # Add order IDs if present
            if 'open_order_id' in pos:
                serialized['open_order_id'] = pos['open_order_id']
            if 'close_order_id' in pos:
                serialized['close_order_id'] = pos['close_order_id']
            serialized_positions.append(serialized)
        
        system_state = {
            'timestamp': datetime.now().isoformat(),
            'regime': regime,
            'portfolio_risk': self.portfolio_greeks,
            # Count truly active positions (OPEN or CLOSING, exclude OPENING positions waiting for fill)
            # Include positions with no status (recovered positions default to OPEN)
            'open_positions': sum(1 for p in self.open_positions.values() if p.get('status') in ['OPEN', 'CLOSING'] or p.get('status') is None),
            'total_positions': len(self.open_positions),  # Total including OPENING
            'positions': serialized_positions,  # Full position details for dashboard
            'status': 'CONNECTED' if self.is_connected else 'DISCONNECTED'
        }

        # 2. Symbol State
        symbols_data = {}
        for symbol in self.symbols:
            inds = self.alpha_engine.get_indicators(symbol)
            iv_rank = self.alpha_engine.get_iv_rank(symbol)
            
            # Get warm status and candle count for dashboard
            is_warm = inds.get('is_warm', False)
            candle_count = inds.get('candle_count', 0)
            sma_200 = inds.get('sma_200')
            
            symbols_data[symbol] = {
                'price': inds.get('price', 0),
                'rsi': inds.get('rsi', 50),
                'adx': self.alpha_engine.get_adx(symbol),
                'iv_rank': iv_rank,
                'trend': inds.get('trend', 'UNKNOWN'),
                'flow': inds.get('flow_state', 'NEUTRAL'),
                'vix': inds.get('vix', 0),
                'volume_velocity': inds.get('volume_velocity', 1.0),
                'active_signal': self.last_signals.get(symbol, {}).get('signal', None),
                # Volume Profile (Market Structure)
                'poc': inds.get('poc', 0),  # Point of Control
                'vah': inds.get('vah', 0),  # Value Area High
                'val': inds.get('val', 0),  # Value Area Low
                # Warm-up status for dashboard
                'is_warm': is_warm,
                'candle_count': candle_count,
                'sma_200': sma_200  # Include SMA value so dashboard can show it
            }
        
        final_export = {
            'system': system_state,
            'market': symbols_data
        }
        
        try:
            with open(self.state_file, 'w') as f:
                json.dump(final_export, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to export state: {e}")
        
        return final_export

    # --- HELPERS FOR ORDER MANAGEMENT ---
    async def _fetch_account_id(self):
        """Fetches the SANDBOX account ID if not already known"""
        if self.account_id: 
            return self.account_id
        
        # Use SANDBOX token and API for account lookup (where orders are executed)
        sandbox_api_base = "https://sandbox.tradier.com/v1"
        headers = {'Authorization': f'Bearer {self.sandbox_token}', 'Accept': 'application/json'}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{sandbox_api_base}/user/profile", headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        acct = data['profile']['account']
                        accounts = acct if isinstance(acct, list) else [acct]
                        # Find VA account (paper trading)
                        for acc in accounts:
                            acc_num = acc['account_number'] if isinstance(acc, dict) else acc
                            if str(acc_num).startswith('VA'):
                                self.account_id = str(acc_num)
                                logging.info(f"✅ SANDBOX Account ID identified: {self.account_id}")
                                return self.account_id
                        # Fallback to first account
                        if accounts:
                            self.account_id = accounts[0]['account_number'] if isinstance(accounts[0], dict) else str(accounts[0])
                            logging.info(f"✅ Account ID identified: {self.account_id}")
                            return self.account_id
        except Exception as e:
            logging.error(f"Failed to fetch account ID: {e}")
        return None

    async def _get_account_equity(self) -> float:
        """
        Fetch account equity from Tradier (SANDBOX account where orders execute).
        Returns total_equity or fallback to $100,000 if API fails.
        """
        if not self.account_id:
            await self._fetch_account_id()
        if not self.account_id:
            logging.warning("⚠️ Account ID not available. Using fallback equity: $100,000")
            return 100000.0  # Safe fallback for sizing calculations
        
        sandbox_api_base = "https://sandbox.tradier.com/v1"
        headers = {'Authorization': f'Bearer {self.sandbox_token}', 'Accept': 'application/json'}
        url = f"{sandbox_api_base}/accounts/{self.account_id}/balances"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        balances = data.get('balances', {})
                        total_equity = balances.get('total_equity', 0)
                        if total_equity and total_equity > 0:
                            return float(total_equity)
                        else:
                            logging.warning(f"⚠️ Equity data unavailable. Using fallback: $100,000")
                            return 100000.0
        except Exception as e:
            logging.error(f"Failed to fetch equity: {e}. Using fallback: $100,000")
        
        return 100000.0  # Safe fallback so we don't crash

    async def _get_order_status(self, order_id: str) -> Optional[str]:
        """
        Get order status from Tradier (uses SANDBOX API where orders are executed)
        Returns: 'filled', 'canceled', 'pending', 'rejected', 'expired', or None on error
        Also logs rejection reasons for debugging
        """
        if not self.account_id: 
            await self._fetch_account_id()
        if not self.account_id: 
            return None

        # Use SANDBOX API for order status (Gatekeeper executes orders in sandbox)
        sandbox_api_base = "https://sandbox.tradier.com/v1"
        headers = {'Authorization': f'Bearer {self.sandbox_token}', 'Accept': 'application/json'}
        url = f"{sandbox_api_base}/accounts/{self.account_id}/orders/{order_id}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        order = data.get('order', {})
                        status = order.get('status')
                        
                        # Log rejection reasons for debugging
                        if status == 'rejected':
                            error_msg = order.get('error', order.get('message', 'Unknown rejection reason'))
                            logging.warning(f"🚫 Order {order_id} REJECTED: {error_msg}")
                        
                        return status  # 'filled', 'canceled', 'pending', 'rejected', 'expired'
                    elif resp.status == 404:
                        # Order not found - might be filled and removed, or invalid ID
                        logging.warning(f"⚠️ Order {order_id} not found (404). May be filled or invalid.")
                        return None
                    else:
                        error_text = await resp.text()
                        logging.error(f"⚠️ Order status check failed for {order_id}: HTTP {resp.status} - {error_text[:200]}")
                        return None
        except Exception as e:
            logging.error(f"❌ Check order status failed for {order_id}: {e}")
            import traceback
            traceback.print_exc()
        return None

    async def _cancel_order(self, order_id: str) -> bool:
        """
        Cancel a pending order (uses SANDBOX API where orders are executed)
        Returns True if cancellation succeeded or order already filled/cancelled, False on error
        """
        if not self.account_id: 
            await self._fetch_account_id()
        if not self.account_id: 
            return False

        # First, check order status - don't cancel if already filled/cancelled
        status = await self._get_order_status(order_id)
        if status in ['filled', 'canceled', 'rejected', 'expired']:
            logging.info(f"ℹ️ Order {order_id} already {status}, skipping cancellation")
            return True  # Not an error - order is already in terminal state

        # Use SANDBOX API for order cancellation (Gatekeeper executes orders in sandbox)
        sandbox_api_base = "https://sandbox.tradier.com/v1"
        headers = {'Authorization': f'Bearer {self.sandbox_token}', 'Accept': 'application/json'}
        url = f"{sandbox_api_base}/accounts/{self.account_id}/orders/{order_id}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        order_status = data.get('order', {}).get('status', 'unknown')
                        logging.info(f"🗑️ Cancelled order {order_id} (status: {order_status})")
                        return True
                    else:
                        # Parse error response for better error details
                        error_text = await resp.text()
                        try:
                            error_json = await resp.json()
                            error_msg = error_json.get('error', error_json.get('fault', {}).get('faultstring', error_text))
                            if isinstance(error_msg, dict):
                                error_msg = error_msg.get('message', str(error_msg))
                        except:
                            error_msg = error_text[:200] if error_text else f"HTTP {resp.status}"
                        
                        logging.warning(f"⚠️ Failed to cancel order {order_id}: {resp.status} - {error_msg}")
                        return False
        except Exception as e:
            logging.error(f"❌ Cancel order error for {order_id}: {e}")
            return False

    # --- POSITION MANAGEMENT (AUTOPILOT) ---
    
    async def _manage_positions_loop(self):
        """Background task to monitor and manage open positions"""
        logging.info("🛡️ Position Manager: ONLINE")
        last_status_log = datetime.now()
        last_sync = datetime.now()
        
        # Ensure account ID is ready
        await self._fetch_account_id()

        while not self.stop_signal:
            try:
                if self.open_positions:
                    await self._manage_positions()
                    if (datetime.now() - last_status_log).seconds >= 30:
                        logging.info(f"📊 MONITORING {len(self.open_positions)} open positions")
                        last_status_log = datetime.now()
                else:
                    await asyncio.sleep(30)
                    continue
                
                # Periodic Full Sync: Every 10 minutes, sync with Tradier
                # This ensures Brain's state matches broker reality
                if (datetime.now() - last_sync).total_seconds() >= 600:  # 10 minutes
                    await self.sync_positions_with_tradier()
                    last_sync = datetime.now()
                    
            except Exception as e:
                logging.error(f"⚠️ Manager Error: {e}")
                import traceback
                traceback.print_exc()
            await asyncio.sleep(5)

    async def _get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        if not symbols: 
            return {}
        headers = {'Authorization': f'Bearer {self.access_token}', 'Accept': 'application/json'}
        url = f'{TRADIER_API_BASE}/markets/quotes'
        params = {'symbols': ','.join(symbols), 'greeks': 'true'}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        quotes = data.get('quotes', {}).get('quote', [])
                        if isinstance(quotes, dict): 
                            quotes = [quotes]
                        result = {}
                        for q in quotes:
                            sym = q.get('symbol')
                            if not sym: 
                                continue
                            bid = float(q.get('bid', 0) or 0)
                            ask = float(q.get('ask', 0) or 0)
                            price = (bid + ask) / 2 if bid > 0 and ask > 0 else float(q.get('last', 0) or 0)
                            greeks = q.get('greeks', {}) or {}
                            result[sym] = {
                                'price': price,
                                'delta': float(greeks.get('delta', 0) or 0),
                                'theta': float(greeks.get('theta', 0) or 0),
                                'vega': float(greeks.get('vega', 0) or 0)
                            }
                        return result
        except Exception as e:
            logging.error(f"⚠️ Quote/Greek fetch failed: {e}")
        return {}

    async def _manage_positions(self):
        """
        Smart Manager 2.0: Advanced Exit Logic + Order Verification
        """
        # Collect symbols for quotes (only for OPEN positions - CLOSING positions don't need quotes)
        # Also include positions with status=None (recovered positions default to OPEN)
        all_legs = []
        for pos in self.open_positions.values():
            status = pos.get('status')
            if status == 'OPEN' or status is None:
                for leg in pos['legs']:
                    all_legs.append(leg['symbol'])
        
        if not all_legs: 
            return
        quotes = await self._get_quotes(all_legs)
        if not quotes:
            logging.warning(f"⚠️ Failed to fetch quotes for {len(all_legs)} option symbols.")
            return

        now = datetime.now()
        
        # Iterate over a COPY of items because we might modify dictionary
        for trade_id, pos in list(self.open_positions.items()):
            status = pos.get('status', 'OPEN')

            # --- 1. VERIFY ENTRY (The "Waiting Room") ---
            if status == 'OPENING':
                order_id = pos.get('open_order_id')
                if not order_id:
                    # Logic error, shouldn't happen unless manual intervention
                    logging.error(f"❌ OPENING state with no Order ID for {trade_id}. Deleting.")
                    del self.open_positions[trade_id]
                    self._save_positions_to_disk()
                    continue
                
                order_status = await self._get_order_status(order_id)
                
                # FALLBACK: If order status check fails, verify by checking actual positions
                # This catches cases where order status API fails but position exists
                if order_status is None:
                    logging.warning(f"⚠️ Order status check failed for {order_id}. Checking actual positions as fallback...")
                    actual_positions = await self._get_actual_positions()
                    if actual_positions:
                        # Check if any of our legs exist in Tradier
                        leg_symbols = [leg.get('symbol') for leg in pos.get('legs', [])]
                        found_legs = [sym for sym in leg_symbols if sym in actual_positions]
                        if found_legs:
                            logging.info(f"✅ FALLBACK: Found {len(found_legs)}/{len(leg_symbols)} legs in Tradier for {trade_id}. Assuming filled.")
                            order_status = 'filled'
                        else:
                            # No legs found, might be canceled or never filled
                            logging.warning(f"⚠️ No legs found in Tradier for {trade_id}. Order may be canceled.")
                            # Don't delete yet, wait for next check
                            continue
                    else:
                        # Can't verify, skip this check
                        logging.warning(f"⚠️ Cannot verify order {order_id} status. Will retry next cycle.")
                        continue
                
                if order_status == 'filled':
                    logging.info(f"✅ ENTRY FILLED for {trade_id}. Tracking active position.")
                    pos['status'] = 'OPEN'
                    pos['timestamp'] = now  # Reset timer to fill time
                    # Verify actual quantities from Tradier (may differ if partially filled)
                    actual_positions = await self._get_actual_positions()
                    if actual_positions:
                        for leg in pos.get('legs', []):
                            leg_symbol = leg.get('symbol')
                            actual_pos = actual_positions.get(leg_symbol)
                            if actual_pos:
                                actual_qty = abs(float(actual_pos.get('quantity', 0)))
                                if actual_qty > 0:
                                    leg['quantity'] = int(actual_qty)
                                    logging.info(f"   Updated {leg_symbol} quantity to {actual_qty} (from Tradier)")
                    self._save_positions_to_disk()
                
                elif order_status in ['canceled', 'rejected', 'expired']:
                    logging.warning(f"🚫 Entry Order {order_status} for {trade_id}. Removing from tracker.")
                    del self.open_positions[trade_id]
                    self._save_positions_to_disk()
                
                elif order_status in ['pending', 'open', 'partially_filled']:
                    # Check timeout (5 mins)
                    sent_time = pos.get('opening_timestamp')
                    if sent_time and (now - sent_time).total_seconds() > 300:
                        logging.info(f"⏳ Entry Order {order_id} pending > 5m. Cancelling.")
                        await self._cancel_order(order_id)
                        del self.open_positions[trade_id]
                        self._save_positions_to_disk()
                
                continue  # Skip remaining logic for OPENING positions

            # --- 2. VERIFY EXIT (Close & Verify) ---
            if status == 'CLOSING':
                order_id = pos.get('close_order_id')
                if not order_id:
                    # Weird state, reset to OPEN
                    pos['status'] = 'OPEN'
                    continue
                
                # Check if we're waiting for cancellation to complete
                if pos.get('cancelling'):
                    # Wait for cancellation to complete (check status)
                    status = await self._get_order_status(order_id)
                    if status == 'filled':
                        # Order was filled before cancellation completed - SUCCESS!
                        logging.info(f"✅ Order {order_id} FILLED (during cancellation attempt). Position {trade_id} closed successfully.")
                        del self.open_positions[trade_id]
                        self._save_positions_to_disk()
                        continue
                    elif status in ['canceled', 'rejected', 'expired']:
                        # Cancellation complete (or order rejected/expired), reset to OPEN for retry
                        logging.info(f"✅ Order {order_id} {status}. Will retry after delay.")
                        pos['status'] = 'OPEN'
                        del pos['close_order_id']
                        del pos['cancelling']
                        if 'closing_timestamp' in pos:
                            del pos['closing_timestamp']
                        # Add retry delay timestamp to prevent immediate retry (wait 5 seconds)
                        pos['last_close_attempt'] = now
                        self._save_positions_to_disk()
                    elif status == 'pending' or status == 'open':
                        # Still pending, wait another cycle
                        continue
                    else:
                        # Unknown status, assume cancelled and retry
                        logging.warning(f"⚠️ Order {order_id} status unknown: {status}. Assuming cancelled.")
                        pos['status'] = 'OPEN'
                        del pos['close_order_id']
                        del pos['cancelling']
                        if 'closing_timestamp' in pos:
                            del pos['closing_timestamp']
                        pos['last_close_attempt'] = now
                        self._save_positions_to_disk()
                    continue
                
                order_status = await self._get_order_status(order_id)
                
                # Handle API failure - check if order still exists in Tradier
                if order_status is None:
                    logging.warning(f"⚠️ Could not get order status for {order_id}. Checking Tradier positions as fallback...")
                    # Fallback: Check if position still exists in Tradier
                    actual_positions = await self._get_actual_positions()
                    if actual_positions:
                        leg_symbols = [leg.get('symbol') for leg in pos.get('legs', [])]
                        found_legs = [sym for sym in leg_symbols if sym in actual_positions]
                        if not found_legs or len(found_legs) < len(leg_symbols) * 0.5:  # Less than 50% of legs exist
                            # Position likely closed (order filled)
                            logging.info(f"✅ FALLBACK: Position {trade_id} no longer in Tradier. Assuming filled.")
                            del self.open_positions[trade_id]
                            self._save_positions_to_disk()
                            continue
                        else:
                            # Position still exists, order might be pending or rejected
                            # Try to get order details from Tradier directly
                            logging.info(f"⚠️ Position still exists, order {order_id} status unknown. Will retry next cycle.")
                            continue
                    else:
                        # Can't verify, wait for next cycle
                        logging.warning(f"⚠️ Cannot verify order {order_id} status. Will retry next cycle.")
                        continue
                
                if order_status == 'filled':
                    logging.info(f"✅ ORDER FILLED for {trade_id}. Position Closed.")
                    del self.open_positions[trade_id]
                    self._save_positions_to_disk()
                    continue
                
                elif order_status in ['canceled', 'rejected', 'expired']:
                    # CRITICAL: Log rejection reason if available
                    logging.warning(f"⚠️ Closing Order {order_status} for {trade_id} (Order ID: {order_id}). Will retry after delay...")
                    # For rejected orders, check if it's a buying power issue (shouldn't happen for closing)
                    # Reset to OPEN so exit conditions can be re-evaluated
                    pos['status'] = 'OPEN'  # Reset to try again
                    del pos['close_order_id']
                    if 'closing_timestamp' in pos:
                        del pos['closing_timestamp']
                    # Add retry delay timestamp to prevent immediate retry (wait 10 seconds for rejected orders)
                    pos['last_close_attempt'] = now
                    self._save_positions_to_disk()
                    continue
                
                elif status == 'pending' or status == 'open' or status == 'partially_filled':
                    # Smart Order Chasing: Check if price moved away
                    # If price moved > 10 cents from order limit price, cancel and retry immediately
                    order_limit_price = pos.get('close_limit_price')
                    if order_limit_price:
                        # Get current market price for the legs
                        symbol = pos.get('symbol', '')
                        leg_symbols = [leg['symbol'] for leg in pos.get('legs', [])]
                        if leg_symbols:
                            current_quotes = await self._get_quotes(leg_symbols)
                            if current_quotes:
                                # Calculate current cost to close (same logic as in _manage_positions)
                                current_cost = 0.0
                                for leg in pos['legs']:
                                    quote_data = current_quotes.get(leg['symbol'])
                                    if quote_data:
                                        price = quote_data['price']
                                        qty = float(leg['quantity'])
                                        if leg['side'] == 'SELL':
                                            current_cost += price * qty
                                        else:
                                            current_cost -= price * qty
                                
                                if current_cost > 0:
                                    drift = abs(current_cost - order_limit_price)
                                    if drift > 0.10:  # 10 cents away
                                        logging.info(f"🏃 SMART CHASE: Price moved {drift:.2f} away for {trade_id}. "
                                                   f"Order: ${order_limit_price:.2f}, Market: ${current_cost:.2f}. "
                                                   f"Cancelling to re-price.")
                                        if not pos.get('cancelling'):  # Only cancel once
                                            cancel_success = await self._cancel_order(order_id)
                                            if cancel_success:
                                                pos['cancelling'] = True
                                                pos['cancel_attempt_time'] = now.isoformat()
                                                self._save_positions_to_disk()
                                                await asyncio.sleep(5)  # Wait for cancellation
                                            continue  # Skip timeout check for this cycle
                    
                    # Check timeout (fallback if price didn't move much)
                    sent_time = pos.get('closing_timestamp')
                    if sent_time:
                        # If pending for > 2 minutes, cancel and retry (likely price moved)
                        if (now - sent_time).total_seconds() > 120:
                            if not pos.get('cancelling'):  # Only cancel once
                                logging.info(f"⏳ Order {order_id} pending too long. Cancelling to repost.")
                                cancel_success = await self._cancel_order(order_id)
                                if cancel_success:
                                    # Mark as cancelling and wait for cancellation to complete
                                    pos['cancelling'] = True
                                    pos['cancel_attempt_time'] = now.isoformat()
                                    self._save_positions_to_disk()
                                    # Wait longer for cancellation to process (give Tradier time)
                                    await asyncio.sleep(5)
                                else:
                                    # Cancellation failed - might be API error or order already filled
                                    # Check status one more time before giving up
                                    await asyncio.sleep(3)
                                    final_status = await self._get_order_status(order_id)
                                    if final_status in ['filled', 'canceled']:
                                        logging.info(f"✅ Order {order_id} is now {final_status} after failed cancel attempt")
                                        if final_status == 'filled':
                                            del self.open_positions[trade_id]
                                            self._save_positions_to_disk()
                                        else:
                                            pos['status'] = 'OPEN'
                                            del pos['close_order_id']
                                            pos['last_close_attempt'] = now
                                            self._save_positions_to_disk()
                                    else:
                                        # Still pending - wait before retrying cancellation
                                        logging.info(f"⏳ Order {order_id} still {final_status}, will retry cancellation later")
                                        pos['cancel_attempt_time'] = now.isoformat()
                                        self._save_positions_to_disk()
                                        await asyncio.sleep(10)  # Extended delay before next attempt
                    continue
                
                else:
                    # Unknown status or API fail, wait for next loop
                    continue

            # --- 3. EVALUATE OPEN POSITIONS (Risk Management) ---
            # If we are here, status is 'OPEN' or None (recovered positions)
            # Ensure status is set to OPEN if it's None (recovered positions)
            if pos.get('status') is None:
                pos['status'] = 'OPEN'
            
            symbol = pos['symbol']
            cost_to_close = 0.0
            missing_quote = False
            trade_delta = 0.0
            trade_theta = 0.0
            trade_vega = 0.0
            
            for leg in pos['legs']:
                quote_data = quotes.get(leg['symbol'])
                if not quote_data:
                    missing_quote = True
                    break
                price = quote_data['price']
                qty = float(leg['quantity'])
                if leg['side'] == 'SELL':
                    cost_to_close += price * qty
                    trade_delta -= quote_data['delta'] * 100 * qty
                    trade_theta -= quote_data['theta'] * 100 * qty
                    trade_vega -= quote_data['vega'] * 100 * qty
                else:
                    cost_to_close -= price * qty
                    trade_delta += quote_data['delta'] * 100 * qty
                    trade_theta += quote_data['theta'] * 100 * qty
                    trade_vega += quote_data['vega'] * 100 * qty
            
            # Always update live_greeks, even if missing_quote (will be 0, but at least it's set)
            pos['live_greeks'] = {'delta': trade_delta, 'theta': trade_theta, 'vega': trade_vega}
            
            if missing_quote: 
                logging.debug(f"⚠️ Missing quotes for {trade_id}, skipping P&L calculation but Greeks updated")
                continue
            if cost_to_close <= 0: 
                logging.debug(f"⚠️ Invalid cost_to_close for {trade_id} (${cost_to_close:.2f}), skipping P&L calculation")
                continue
            
            entry_credit = pos['entry_price']
            pnl_pct = ((entry_credit - cost_to_close) / entry_credit) * 100
            
            if pnl_pct > pos.get('highest_pnl', -100):
                pos['highest_pnl'] = pnl_pct

            # --- EXIT RULES ---
            should_close = False
            reason = ""
            
            indicators = self.alpha_engine.get_indicators(symbol)
            current_price = indicators['price']
            sma_200 = indicators.get('sma_200')
            adx = self.alpha_engine.get_adx(symbol)

            is_scalper = False
            if pos['legs']:
                try:
                    exp_str = pos['legs'][0].get('expiration', '')
                    exp = datetime.strptime(exp_str, '%Y-%m-%d').date()
                    if (exp - now.date()).days == 0: 
                        is_scalper = True
                except: 
                    pass

            if is_scalper:
                rsi = indicators['rsi']
                if rsi is not None:
                    if pos.get('bias') == 'bullish' and rsi > 60:
                        should_close = True
                        reason = f"Scalp Win (RSI {rsi:.1f})"
                    elif pos.get('bias') == 'bearish' and rsi < 40:
                        should_close = True
                        reason = f"Scalp Win (RSI {rsi:.1f})"
                if pnl_pct < -20: 
                    should_close = True
                    reason = "Scalp Hard Stop (-20%)"

            elif pos['strategy'] == 'CREDIT_SPREAD' and pos.get('bias') in ['bullish', 'bearish']:
                if pos['highest_pnl'] >= 30 and (pos['highest_pnl'] - pnl_pct) >= 10:
                    should_close = True
                    reason = f"Trailing Stop (Peak {pos['highest_pnl']:.1f}%)"
                if pos.get('bias') == 'bullish' and sma_200 and current_price < sma_200:
                    should_close = True
                    reason = "Trend Broken (Price < SMA200)"
                if pos.get('bias') == 'bearish' and sma_200 and current_price > sma_200:
                    should_close = True
                    reason = "Trend Broken (Price > SMA200)"
                if pnl_pct >= 80: 
                    should_close = True
                    reason = "Max Profit (+80%)"
                if pnl_pct <= -100: 
                    should_close = True
                    reason = "Stop Loss (-100%)"

            elif pos.get('bias') == 'neutral':
                if adx is not None and adx > 30: 
                    should_close = True
                    reason = f"Volatility Spike (ADX {adx:.1f})"
                if pnl_pct >= 50: 
                    should_close = True
                    reason = "Take Profit (+50%)"
                if pnl_pct <= -100: 
                    should_close = True
                    reason = "Stop Loss (-100%)"

            if now.hour == 15 and now.minute >= 55:
                should_close = True
                reason = "EOD Auto-Close"

            if should_close:
                # Check if we need to wait before retrying (after cancellation/rejection)
                last_attempt = pos.get('last_close_attempt')
                if last_attempt:
                    seconds_since_attempt = (now - last_attempt).total_seconds()
                    # Wait 10 seconds after rejection (longer delay for rejected orders)
                    # Wait 5 seconds after cancellation
                    wait_time = 10 if pos.get('close_order_id') else 5
                    if seconds_since_attempt < wait_time:
                        logging.debug(f"⏳ Waiting to retry close for {trade_id} ({int(wait_time - seconds_since_attempt)}s remaining)")
                        continue  # Skip this cycle, try again next time
                    # Enough time has passed, clear the delay flag
                    del pos['last_close_attempt']
                
                # Don't attempt close if already CLOSING (wait for current order to resolve)
                if pos.get('status') == 'CLOSING':
                    logging.debug(f"⏳ {trade_id} already has close order pending, waiting for resolution...")
                    continue
                
                logging.info(f"🛑 ATTEMPTING CLOSE {trade_id} | P&L: {pnl_pct:.1f}% | Reason: {reason}")
                await self._execute_close(trade_id, pos, cost_to_close)
        
        self._log_portfolio_risk()

    def _log_portfolio_risk(self):
        total_delta = 0.0
        total_theta = 0.0
        total_vega = 0.0
        count = 0
        positions_without_greeks = []
        for pos in self.open_positions.values():
            # Only count truly open positions (exclude OPENING/CLOSING)
            # Also include positions with no status (recovered positions default to OPEN)
            status = pos.get('status')
            if status == 'OPEN' or status is None:
                greeks = pos.get('live_greeks', {})
                delta = greeks.get('delta', 0)
                theta = greeks.get('theta', 0)
                vega = greeks.get('vega', 0)
                
                # Check if Greeks are still zero (might not have been calculated yet)
                if delta == 0 and theta == 0 and vega == 0:
                    positions_without_greeks.append(pos.get('symbol', 'UNKNOWN'))
                
                total_delta += delta
                total_theta += theta
                total_vega += vega
                count += 1
        
        self.portfolio_greeks = {'delta': total_delta, 'theta': total_theta, 'vega': total_vega}
        if count > 0:
            if positions_without_greeks:
                logging.warning(f"⚠️ Portfolio Greeks: {len(positions_without_greeks)} position(s) have zero Greeks "
                              f"(may not have quotes yet): {', '.join(set(positions_without_greeks))}")
            logging.info(f"📊 PORTFOLIO RISK: Delta {total_delta:+.1f} | Theta {total_theta:+.1f} | Vega {total_vega:+.1f} | Positions: {count}")

    async def _get_actual_positions(self) -> Dict[str, Dict]:
        """Fetch actual current positions from Tradier to verify quantities/sides"""
        if not self.account_id:
            await self._fetch_account_id()
        if not self.account_id:
            return {}
        
        sandbox_api_base = "https://sandbox.tradier.com/v1"
        headers = {'Authorization': f'Bearer {self.sandbox_token}', 'Accept': 'application/json'}
        url = f"{sandbox_api_base}/accounts/{self.account_id}/positions"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        positions = data.get('positions', {}).get('position', [])
                        if positions == 'null' or not positions:
                            return {}
                        
                        # Convert to dict keyed by symbol for easy lookup
                        result = {}
                        pos_list = positions if isinstance(positions, list) else [positions]
                        for p in pos_list:
                            symbol = p.get('symbol')
                            if symbol:
                                result[symbol] = {
                                    'quantity': float(p.get('quantity', 0)),  # Can be negative (short)
                                    'cost_basis': float(p.get('cost_basis', 0))
                                }
                        return result
        except Exception as e:
            logging.error(f"Failed to fetch actual positions: {e}")
        return {}

    async def sync_positions_with_tradier(self):
        """
        Full Position Sync: Compare Brain's tracked positions with Tradier's actual positions
        Runs every 10 minutes to ensure Brain's state matches broker reality.
        - Updates OPENING positions that have filled
        - Removes positions that no longer exist in Tradier (ghosts)
        - Updates quantities to match actual Tradier positions
        """
        logging.info("🔄 SYNC: Starting full position sync with Tradier...")
        
        if not self.account_id:
            await self._fetch_account_id()
        if not self.account_id:
            logging.warning("⚠️ Cannot sync: Account ID not available")
            return
        
        try:
            # Fetch all positions from Tradier
            actual_positions = await self._get_actual_positions()
            if not actual_positions:
                logging.warning("⚠️ Sync: No positions found in Tradier (or API failed)")
                return
            
            logging.info(f"📊 Tradier has {len(actual_positions)} position(s)")
            
            # Build set of Tradier position symbols
            tradier_symbols = set(actual_positions.keys())
            
            # Build set of Brain position symbols (from all legs)
            brain_symbols = set()
            for trade_id, pos in self.open_positions.items():
                for leg in pos.get('legs', []):
                    leg_symbol = leg.get('symbol')
                    if leg_symbol:
                        brain_symbols.add(leg_symbol)
            
            now = datetime.now()
            updated_count = 0
            removed_count = 0
            
            # 1. Check OPENING positions - see if they've filled
            for trade_id, pos in list(self.open_positions.items()):
                if pos.get('status') == 'OPENING':
                    leg_symbols = [leg.get('symbol') for leg in pos.get('legs', [])]
                    found_legs = [sym for sym in leg_symbols if sym in tradier_symbols]
                    
                    if found_legs:
                        # Position has filled!
                        logging.info(f"✅ SYNC: {trade_id} has filled ({len(found_legs)}/{len(leg_symbols)} legs in Tradier)")
                        pos['status'] = 'OPEN'
                        pos['timestamp'] = now
                        
                        # Update quantities from actual positions
                        for leg in pos.get('legs', []):
                            leg_symbol = leg.get('symbol')
                            actual_pos = actual_positions.get(leg_symbol)
                            if actual_pos:
                                actual_qty = abs(float(actual_pos.get('quantity', 0)))
                                if actual_qty > 0:
                                    leg['quantity'] = int(actual_qty)
                        
                        updated_count += 1
            
            # 2. Remove ghosts (in Brain but not in Tradier)
            ghosts = brain_symbols - tradier_symbols
            if ghosts:
                logging.info(f"👻 SYNC: Found {len(ghosts)} ghost position(s) (closed in Tradier)")
                to_remove = []
                for trade_id, pos in list(self.open_positions.items()):
                    pos_symbols = {leg.get('symbol') for leg in pos.get('legs', [])}
                    # If ALL legs are ghosts, remove the position
                    if pos_symbols and pos_symbols.issubset(ghosts):
                        logging.info(f"🗑️ SYNC: Removing ghost position: {trade_id}")
                        to_remove.append(trade_id)
                
                for trade_id in to_remove:
                    del self.open_positions[trade_id]
                    removed_count += len(to_remove)
            
            # 3. Update quantities and recalculate entry_price for existing OPEN positions
            for trade_id, pos in self.open_positions.items():
                if pos.get('status') == 'OPEN':
                    # Update quantities
                    for leg in pos.get('legs', []):
                        leg_symbol = leg.get('symbol')
                        actual_pos = actual_positions.get(leg_symbol)
                        if actual_pos:
                            actual_qty = abs(float(actual_pos.get('quantity', 0)))
                            if actual_qty > 0 and actual_qty != leg.get('quantity', 0):
                                old_qty = leg.get('quantity', 0)
                                leg['quantity'] = int(actual_qty)
                                logging.info(f"📝 SYNC: Updated {leg_symbol} quantity: {old_qty} -> {actual_qty}")
                    
                    # Recalculate entry_price for MANUAL_RECOVERY positions (fix incorrect calculations)
                    # Always recalculate if entry_price seems suspiciously high (> $50 suggests wrong calculation)
                    should_recalc = (pos.get('strategy') == 'MANUAL_RECOVERY' and 
                                   (pos.get('entry_price', 0) > 50 or pos.get('entry_price', 0) < 0.01))
                    
                    if should_recalc:
                        old_entry = pos.get('entry_price', 0)
                        new_entry = await self._recalculate_entry_price_from_tradier(pos, actual_positions)
                        if new_entry and new_entry > 0 and abs(new_entry - old_entry) > 0.01:
                            logging.info(f"🔧 SYNC: Recalculated entry_price for {trade_id}: ${old_entry:.2f} -> ${new_entry:.2f}")
                            pos['entry_price'] = round(new_entry, 2)
                            updated_count += 1
            
            # Save changes
            if updated_count > 0 or removed_count > 0:
                self._save_positions_to_disk()
                logging.info(f"💾 SYNC: Saved {updated_count} updated, {removed_count} removed position(s)")
            
            logging.info(f"✅ SYNC COMPLETE: {updated_count} updated, {removed_count} removed, {len(self.open_positions)} total tracked")
            
        except Exception as e:
            logging.error(f"❌ Sync failed: {e}")
            import traceback
            traceback.print_exc()

    async def _recalculate_entry_price_from_tradier(self, pos: Dict, actual_positions: Dict) -> Optional[float]:
        """
        Recalculate entry_price for a position using Tradier's cost_basis data.
        This fixes incorrect entry_price calculations for MANUAL_RECOVERY positions.
        """
        try:
            net_credit = 0.0
            legs_found = 0
            
            for leg in pos.get('legs', []):
                leg_symbol = leg.get('symbol')
                actual_pos = actual_positions.get(leg_symbol)
                
                if not actual_pos:
                    continue
                
                legs_found += 1
                qty = float(actual_pos.get('quantity', 0))  # Can be negative (short)
                cost_basis = float(actual_pos.get('cost_basis', 0))
                
                # Tradier's cost_basis is already the TOTAL cost basis (not per contract)
                # For SELL (qty < 0): cost_basis is negative (we received money)
                # For BUY (qty > 0): cost_basis is positive (we paid money)
                # So we can use cost_basis directly without dividing by quantity
                
                if qty < 0:  # SELL leg (credit received, cost_basis is negative)
                    net_credit += abs(cost_basis)  # Add the credit received
                else:  # BUY leg (debit paid, cost_basis is positive)
                    net_credit -= abs(cost_basis)  # Subtract the debit paid
            
            # Only recalculate if we found at least one leg
            if legs_found == 0:
                return None
            
            # entry_price should be the net credit received (positive for credit spreads)
            if net_credit > 0:
                return net_credit  # Credit received
            elif net_credit < 0:
                return abs(net_credit)  # Debit paid (convert to positive)
            else:
                return None  # Can't determine
                
        except Exception as e:
            logging.error(f"Failed to recalculate entry_price: {e}")
            return None

    async def _reconcile_fills(self):
        """
        Lightweight reconciliation: Check if OPENING positions have actually filled
        by comparing Brain's tracked positions with Tradier's actual positions.
        This catches fills that were missed by order status checks.
        """
        if not self.open_positions:
            return
        
        # Find all OPENING positions
        opening_positions = {tid: pos for tid, pos in self.open_positions.items() 
                            if pos.get('status') == 'OPENING'}
        
        if not opening_positions:
            return
        
        logging.info(f"🔍 Checking {len(opening_positions)} OPENING position(s) for fills...")
        
        # Fetch actual positions from Tradier
        actual_positions = await self._get_actual_positions()
        if not actual_positions:
            logging.warning("⚠️ Cannot reconcile fills: Failed to fetch Tradier positions")
            return
        
        now = datetime.now()
        updated = False
        
        for trade_id, pos in opening_positions.items():
            # Check if any legs exist in Tradier
            leg_symbols = [leg.get('symbol') for leg in pos.get('legs', [])]
            found_legs = [sym for sym in leg_symbols if sym in actual_positions]
            
            if found_legs:
                # At least some legs exist - position likely filled
                logging.info(f"✅ RECONCILIATION: Found {len(found_legs)}/{len(leg_symbols)} legs in Tradier for {trade_id}. Marking as OPEN.")
                pos['status'] = 'OPEN'
                pos['timestamp'] = now
                
                # Update quantities from actual positions
                for leg in pos.get('legs', []):
                    leg_symbol = leg.get('symbol')
                    actual_pos = actual_positions.get(leg_symbol)
                    if actual_pos:
                        actual_qty = abs(float(actual_pos.get('quantity', 0)))
                        if actual_qty > 0:
                            leg['quantity'] = int(actual_qty)
                            logging.info(f"   Updated {leg_symbol} quantity to {actual_qty}")
                
                updated = True
        
        if updated:
            self._save_positions_to_disk()
            logging.info("💾 Updated positions saved to disk")

    async def reconcile_state(self):
        """Startup Reconciliation (Adopt Orphans from Tradier)
        Fetches all open positions from Tradier and reconciles with Brain's state:
        - Adopts positions that exist in Tradier but not in Brain (orphans)
        - Removes positions that exist in Brain but not in Tradier (ghosts)
        """
        logging.info("🕵️ STARTUP RECONCILIATION: Fetching positions from Tradier...")
        
        if not self.account_id:
            await self._fetch_account_id()
        if not self.account_id:
            logging.warning("⚠️ Cannot reconcile: Account ID not available")
            return
        
        try:
            # Fetch all positions from Tradier
            sandbox_api_base = "https://sandbox.tradier.com/v1"
            headers = {'Authorization': f'Bearer {self.sandbox_token}', 'Accept': 'application/json'}
            url = f"{sandbox_api_base}/accounts/{self.account_id}/positions"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        logging.warning(f"⚠️ Reconciliation failed: {resp.status}")
                        return
                    
                    data = await resp.json()
                    positions = data.get('positions', {}).get('position', [])
                    if positions == 'null' or not positions:
                        positions = []
                    
                    pos_list = positions if isinstance(positions, list) else [positions]
                    
                    # Filter to only option positions
                    option_positions = []
                    for p in pos_list:
                        symbol = p.get('symbol', '')
                        if symbol and re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', symbol):
                            option_positions.append(p)
                    
                    logging.info(f"📊 Tradier has {len(option_positions)} option position(s)")
                    
                    # Group by underlying + expiration (same trade)
                    def parse_option_symbol(opt_symbol):
                        match = re.match(r'^([A-Z]+)(\d{6})([CP])(\d{8})$', opt_symbol)
                        if match:
                            root = match.group(1)
                            date_str = match.group(2)
                            opt_type = 'CALL' if match.group(3) == 'C' else 'PUT'
                            strike_str = match.group(4)
                            
                            year = 2000 + int(date_str[0:2])
                            month = int(date_str[2:4])
                            day = int(date_str[4:6])
                            expiration = f"{year:04d}-{month:02d}-{day:02d}"
                            strike = float(strike_str) / 1000.0
                            
                            return root, expiration, opt_type, strike
                        return None, None, None, None
                    
                    # Group positions by trade
                    grouped_by_trade = {}
                    for p in option_positions:
                        symbol = p.get('symbol')
                        root, exp, opt_type, strike = parse_option_symbol(symbol)
                        if root:
                            key = f"{root}_{exp}"
                            if key not in grouped_by_trade:
                                grouped_by_trade[key] = []
                            grouped_by_trade[key].append({
                                'raw': p,
                                'symbol': symbol,
                                'root': root,
                                'expiration': exp,
                                'type': opt_type,
                                'strike': strike
                            })
                    
                    # Build set of Tradier position keys (by leg symbol)
                    tradier_symbols = {p.get('symbol') for p in option_positions if p.get('symbol')}
                    
                    # Build Tradier position map for quantity comparison
                    tradier_positions_map = {}
                    for p in option_positions:
                        symbol = p.get('symbol')
                        if symbol:
                            tradier_positions_map[symbol] = {
                                'quantity': float(p.get('quantity', 0)),
                                'cost_basis': float(p.get('cost_basis', 0))
                            }
                    
                    # Check for orphans (in Tradier but not in Brain)
                    brain_symbols = set()
                    for pos in self.open_positions.values():
                        for leg in pos.get('legs', []):
                            brain_symbols.add(leg.get('symbol'))
                    
                    orphans = tradier_symbols - brain_symbols
                    if orphans:
                        logging.info(f"🕵️ ORPHAN DETECTED: Found {len(orphans)} position(s) in Tradier not tracked by Brain")
                        # Group orphans by trade
                        orphan_trades = {}
                        for symbol in orphans:
                            root, exp, opt_type, strike = parse_option_symbol(symbol)
                            if root:
                                key = f"{root}_{exp}"
                                if key not in orphan_trades:
                                    orphan_trades[key] = []
                                # Find the position in grouped_by_trade
                                for trade_key, legs in grouped_by_trade.items():
                                    if trade_key == key:
                                        for leg in legs:
                                            if leg['symbol'] == symbol:
                                                orphan_trades[key].append(leg)
                        
                        # Adopt orphans
                        for trade_key, legs in orphan_trades.items():
                            if not legs:
                                continue
                            
                            root = legs[0]['root']
                            expiration = legs[0]['expiration']
                            
                            # Determine strategy
                            strategy = 'CREDIT_SPREAD' if len(legs) == 2 else \
                                      'IRON_CONDOR' if len(legs) == 4 and \
                                      len([l for l in legs if l['type'] == 'CALL']) == 2 else \
                                      'IRON_BUTTERFLY' if len(legs) == 4 else \
                                      'MANUAL_RECOVERY'
                            
                            # Build Brain leg format
                            brain_legs = []
                            net_credit = 0.0
                            for leg in legs:
                                qty = float(leg['raw'].get('quantity', 0))
                                cost_basis = float(leg['raw'].get('cost_basis', 0))
                                side = "SELL" if qty < 0 else "BUY"
                                
                                # Tradier's cost_basis is already the TOTAL cost basis (not per contract)
                                # For SELL (qty < 0): cost_basis is negative (we received money)
                                # For BUY (qty > 0): cost_basis is positive (we paid money)
                                # So we can use cost_basis directly without dividing by quantity
                                
                                if qty < 0:  # SELL leg (credit received, cost_basis is negative)
                                    net_credit += abs(cost_basis)  # Add the credit received
                                else:  # BUY leg (debit paid, cost_basis is positive)
                                    net_credit -= abs(cost_basis)  # Subtract the debit paid
                                
                                brain_legs.append({
                                    'symbol': leg['symbol'],
                                    'expiration': expiration,
                                    'strike': leg['strike'],
                                    'type': leg['type'],
                                    'quantity': abs(int(qty)),
                                    'side': side
                                })
                            
                            # Determine bias
                            bias = "neutral"
                            if strategy == 'CREDIT_SPREAD' and len(legs) == 2:
                                bias = 'bullish' if legs[0]['type'] == 'PUT' else 'bearish'
                            
                            # entry_price should be the net credit received (positive for credit spreads)
                            # If net_credit is negative, it means we paid a debit (unusual for credit spreads)
                            # Use absolute value and ensure minimum of $0.01
                            entry_price = max(abs(net_credit), 0.01) if net_credit != 0 else 1.0
                            trade_id = f"{root}_{strategy}_RECOVERED_{int(datetime.now().timestamp())}"
                            
                            self.open_positions[trade_id] = {
                                "symbol": root,
                                "strategy": strategy,
                                "status": "OPEN",  # Assume OPEN since it exists in Tradier
                                "legs": brain_legs,
                                "entry_price": round(entry_price, 2),
                                "bias": bias,
                                "timestamp": datetime.now(),
                                "highest_pnl": -100.0,
                                "live_greeks": {'delta': 0.0, 'theta': 0.0, 'vega': 0.0}  # Initialize, will be calculated on next _manage_positions cycle
                            }
                            
                            logging.info(f"✅ ADOPTED: {trade_id} ({strategy}, {len(legs)} legs, Entry: ${entry_price:.2f}, Net Credit: ${net_credit:.2f})")
                        
                        self._save_positions_to_disk()
                    
                    # Check for ghosts (in Brain but not in Tradier)
                    ghosts = brain_symbols - tradier_symbols
                    if ghosts:
                        logging.info(f"👻 GHOST DETECTED: Found {len(ghosts)} position(s) in Brain but closed in Tradier")
                        # Find positions with these symbols and remove them
                        to_remove = []
                        for trade_id, pos in self.open_positions.items():
                            pos_symbols = {leg.get('symbol') for leg in pos.get('legs', [])}
                            if pos_symbols.intersection(ghosts):
                                # All legs of this position are closed in Tradier
                                if pos_symbols.issubset(ghosts):
                                    to_remove.append(trade_id)
                        
                        for trade_id in to_remove:
                            logging.info(f"🗑️ Removing ghost position: {trade_id}")
                            del self.open_positions[trade_id]
                        
                        if to_remove:
                            self._save_positions_to_disk()
                    
                    # QUANTITY AUDIT: Check for quantity mismatches (partial fills/closures)
                    quantity_updates = 0
                    unbalanced_positions = []
                    
                    for trade_id, pos in list(self.open_positions.items()):
                        legs_updated = False
                        leg_quantities_zero = []
                        
                        for leg in pos.get('legs', []):
                            leg_symbol = leg.get('symbol')
                            brain_qty = abs(int(leg.get('quantity', 0)))
                            
                            if leg_symbol in tradier_positions_map:
                                tradier_qty = abs(int(tradier_positions_map[leg_symbol]['quantity']))
                                
                                if brain_qty != tradier_qty:
                                    # Quantity mismatch detected
                                    logging.warning(f"⚠️ Quantity mismatch for {trade_id} leg {leg_symbol}: "
                                                  f"Brain={brain_qty}, Tradier={tradier_qty}. Syncing to Tradier.")
                                    
                                    # Update leg quantity to match Tradier
                                    leg['quantity'] = tradier_qty
                                    legs_updated = True
                                    quantity_updates += 1
                                    
                                    # Check if this leg is now zero (unbalanced closure)
                                    if tradier_qty == 0:
                                        leg_quantities_zero.append(leg_symbol)
                        
                        # Handle unbalanced leg closures (some legs closed, others remain)
                        if leg_quantities_zero:
                            all_leg_symbols = {leg.get('symbol') for leg in pos.get('legs', [])}
                            closed_legs = set(leg_quantities_zero)
                            remaining_legs = all_leg_symbols - closed_legs
                            
                            if remaining_legs:
                                # Partial closure: Some legs closed but others remain
                                # This is dangerous - unbalanced position
                                logging.error(f"🚨 UNBALANCED POSITION: {trade_id} has {len(closed_legs)} leg(s) closed "
                                            f"but {len(remaining_legs)} leg(s) still open. This is a risk!")
                                unbalanced_positions.append(trade_id)
                                
                                # Safety: Close the entire position to prevent "Legging Out" risk
                                logging.warning(f"🛑 Closing unbalanced position {trade_id} to prevent risk")
                                del self.open_positions[trade_id]
                                quantity_updates += 1
                            else:
                                # All legs closed - this should have been caught by ghost detection
                                # But handle it here as well
                                logging.info(f"✅ All legs closed for {trade_id}. Removing.")
                                del self.open_positions[trade_id]
                        
                        # Save updates if quantities changed
                        if legs_updated and trade_id in self.open_positions:
                            self._save_positions_to_disk()
                            logging.info(f"💾 Updated quantities for {trade_id}")
                    
                    # Summary logging
                    if not orphans and not ghosts and quantity_updates == 0:
                        logging.info("✅ RECONCILIATION: Brain state matches Tradier (quantities verified)")
                    else:
                        summary_parts = []
                        if orphans:
                            summary_parts.append(f"Adopted {len(orphans)} orphan(s)")
                        if ghosts:
                            summary_parts.append(f"removed {len(ghosts)} ghost(s)")
                        if quantity_updates > 0:
                            summary_parts.append(f"updated {quantity_updates} quantity mismatch(es)")
                        if unbalanced_positions:
                            summary_parts.append(f"closed {len(unbalanced_positions)} unbalanced position(s)")
                        
                        logging.info(f"✅ RECONCILIATION COMPLETE: {', '.join(summary_parts)}")
        
        except Exception as e:
            logging.error(f"❌ Reconciliation error: {e}")
            import traceback
            traceback.print_exc()

    async def _execute_close(self, trade_id: str, pos: Dict, limit_price: float):
        """Send CLOSE order and track it - uses ACTUAL Tradier positions for quantities"""
        # CRITICAL: Fetch actual positions from Tradier to get correct quantities
        # Recovered positions may have wrong quantities if partially filled or adjusted
        actual_positions = await self._get_actual_positions()
        if not actual_positions:
            logging.warning(f"⚠️ Could not fetch actual positions for {trade_id}, using stored legs")
            actual_positions = {}  # Will use fallback
        
        # Add Aggressive Buffer to Limit Price (Pay more to close)
        execution_price = limit_price + 0.05
        
        # Build legs using ACTUAL quantities from Tradier
        legs = []
        for leg in pos['legs']:
            leg_symbol = leg['symbol']
            actual_pos = actual_positions.get(leg_symbol)
            
            if actual_pos:
                # Use actual quantity from Tradier (can be negative for shorts)
                actual_qty_raw = float(actual_pos['quantity'])
                actual_qty = abs(actual_qty_raw)
                # Determine side based on actual Tradier quantity
                # Negative quantity = short position = was SELL to open = need BUY to close
                # Positive quantity = long position = was BUY to open = need SELL to close
                if actual_qty_raw < 0:
                    # Short position: need to BUY to close
                    side = 'SELL'  # Gatekeeper maps SELL->buy_to_close
                    position_type = 'SHORT'
                else:
                    # Long position: need to SELL to close
                    side = 'BUY'  # Gatekeeper maps BUY->sell_to_close
                    position_type = 'LONG'
                
                qty = int(actual_qty)
                if qty > 0:
                    logging.info(f"🔍 {leg_symbol}: Tradier qty={actual_qty_raw} ({position_type}) -> Send side='{side}' qty={qty}")
                    legs.append({
                        'symbol': leg_symbol,
                        'expiration': leg['expiration'],
                        'strike': leg['strike'],
                        'type': leg['type'],
                        'quantity': qty,
                        'side': side  # Use side based on actual Tradier position
                    })
                else:
                    logging.warning(f"⚠️ Position {leg_symbol} has zero quantity, skipping leg")
            else:
                # Position not found in Tradier - use stored leg (fallback)
                logging.warning(f"⚠️ Position {leg_symbol} not found in Tradier, using stored leg")
                qty = abs(int(leg.get('quantity', 1)))
                legs.append({
                    'symbol': leg_symbol,
                    'expiration': leg['expiration'],
                    'strike': leg['strike'],
                    'type': leg['type'],
                    'quantity': qty,
                    'side': leg['side']  # Use stored side
                })
        
        if not legs:
            logging.error(f"❌ No valid legs for closing {trade_id} - all positions may be closed")
            return
        
        proposal = {
            'symbol': pos['symbol'],
            'strategy': pos['strategy'],
            'side': 'CLOSE',
            'quantity': 1,  # Top-level quantity (usually 1 for spreads)
            'price': round(execution_price, 2),
            'legs': legs,  # Use legs with actual Tradier quantities
            'context': {
                'reason': 'Manage Position',
                'closing_trade_id': trade_id
            }
        }
        
        resp = await self.gatekeeper_client.send_proposal(proposal)
        
        if resp and resp.get('status') == 'APPROVED':
            # NEW: DO NOT DELETE. Mark as CLOSING.
            # Extract order_id from response (may be in 'data' or top-level)
            order_id = resp.get('order_id') or (resp.get('data', {}).get('order_id') if isinstance(resp.get('data'), dict) else None)
            if order_id:
                pos['status'] = 'CLOSING'
                pos['close_order_id'] = str(order_id)
                pos['close_limit_price'] = execution_price  # Store for smart chasing
                pos['closing_timestamp'] = datetime.now()
                self._save_positions_to_disk()
                logging.info(f"📤 Close Order Sent: {order_id}. Waiting for fill...")
            else:
                logging.error(f"❌ Approved but no Order ID for {trade_id}. Response: {resp}")
        elif resp and resp.get('status') == 'REJECTED':
            logging.error(f"❌ Close REJECTED for {trade_id}: {resp.get('reason')}")
        else:
            logging.error(f"❌ Close FAILED for {trade_id}: {resp}")

    # --- VIX Polling ---
    async def _poll_vix_loop(self):
        self.vix_poller_running = True
        headers = {'Authorization': f'Bearer {self.access_token}', 'Accept': 'application/json'}
        logging.info("📊 VIX poller started")
        
        while self.vix_poller_running and not self.stop_signal:
            try:
                async with aiohttp.ClientSession() as session:
                    url = f'{TRADIER_API_BASE}/markets/quotes'
                    params = {'symbols': 'VIX'}
                    async with session.get(url, headers=headers, params=params) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            quotes = data.get('quotes', {})
                            quote = quotes.get('quote', None)
                            if isinstance(quote, list): 
                                quote = quote[0]
                            if quote and quote.get('last') is not None:
                                self.alpha_engine.set_vix(float(quote['last']), datetime.now())
            except Exception as e:
                logging.error(f"❌ VIX poller error: {e}")
            
            for _ in range(6): 
                if self.stop_signal: 
                    break
                await asyncio.sleep(10)

    # --- Connection Logic ---
    async def _create_session(self) -> Optional[str]:
        headers = {'Authorization': f'Bearer {self.access_token}', 'Accept': 'application/json'}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(TRADIER_SESSION_URL, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('stream', {}).get('sessionid')
                    logging.error(f"Session failed: {resp.status}")
                    return None
        except Exception as e:
            logging.error(f"Session error: {e}")
            return None

    async def warm_up_history(self):
        """
        Fast Start: Fetch historical candles from Tradier to populate indicators instantly.
        This eliminates the 3+ hour warm-up period by loading the last 5 days of 1-minute data.
        
        Uses Tradier's /markets/timesales endpoint for 1-minute data (history endpoint only supports daily/weekly/monthly).
        """
        logging.info("🔥 WARM-UP: Fetching historical candles for instant indicator readiness...")
        
        # Calculate date range (last 5 days, but fetch each day separately due to API limits)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=5)
        
        headers = {'Authorization': f'Bearer {self.access_token}', 'Accept': 'application/json'}
        
        for symbol in self.symbols:
            try:
                # Use timesales endpoint for 1-minute data (history endpoint doesn't support 1min)
                url = f'{TRADIER_API_BASE}/markets/timesales'
                
                # Fetch data for each day (API may limit date range)
                all_candle_rows = []
                
                for day_offset in range(5):
                    day_date = end_date - timedelta(days=day_offset)
                    # Market hours: 9:30 AM - 4:00 PM ET
                    day_start = day_date.replace(hour=9, minute=30, second=0, microsecond=0)
                    day_end = day_date.replace(hour=16, minute=0, second=0, microsecond=0)
                    
                    # Skip if future date
                    if day_start > end_date:
                        continue
                    
                    params = {
                        'symbol': symbol,
                        'interval': '1min',
                        'start': day_start.strftime('%Y-%m-%dT%H:%M:%S'),
                        'end': min(day_end, end_date).strftime('%Y-%m-%dT%H:%M:%S')
                    }
                    
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, headers=headers, params=params) as resp:
                            if resp.status == 200:
                                data = None  # Initialize to avoid scope issues
                                # Read response text first (can only read once)
                                try:
                                    text = await resp.text()
                                    if not text or text.strip() == '':
                                        logging.debug(f"⚠️ Empty response body for {symbol} on {day_date.date()}")
                                        continue
                                    
                                    # Try to parse as JSON
                                    try:
                                        data = json.loads(text)
                                    except json.JSONDecodeError as json_err:
                                        logging.debug(f"⚠️ JSON parse error for {symbol} on {day_date.date()}: {json_err}, body: {text[:200]}")
                                        continue
                                    
                                    if data is None:
                                        logging.debug(f"⚠️ Parsed JSON is None for {symbol} on {day_date.date()}, body: {text[:100]}")
                                        continue
                                    
                                except Exception as read_err:
                                    logging.debug(f"⚠️ Error reading response for {symbol} on {day_date.date()}: {read_err}")
                                    continue
                                
                                # Double-check data is valid before accessing (defensive programming)
                                if data is None or not isinstance(data, dict):
                                    logging.debug(f"⚠️ Invalid data for {symbol} on {day_date.date()}: type={type(data)}, is_none={data is None}")
                                    continue
                                
                                # Timesales endpoint returns: series.data (array of data points)
                                # Tradier API quirk: Returns {"series": null} instead of empty list when no data
                                # Safely navigate the response structure
                                series_root = data.get('series')
                                if series_root is None:
                                    # Tradier returned {"series": null} - no data for this symbol/date
                                    logging.debug(f"⚠️ No series data for {symbol} on {day_date.date()} (API returned null)")
                                    continue
                                
                                if not isinstance(series_root, dict):
                                    logging.debug(f"⚠️ Invalid series format for {symbol} on {day_date.date()}: {type(series_root)}")
                                    continue
                                
                                series_data = series_root.get('data', [])
                                
                                # If no data, check if there's an error message
                                if not series_data and 'fault' in data:
                                    logging.debug(f"⚠️ API fault for {symbol} on {day_date.date()}: {data.get('fault', {})}")
                                    continue
                                
                                if not series_data:
                                    continue
                                
                                if isinstance(series_data, dict):
                                    series_data = [series_data]
                                
                                # Parse CANDLES from timesales format
                                # Timesales with interval=1min returns PRE-AGGREGATED 1-minute candles
                                # Keys: time, timestamp, price, open, high, low, close, volume, vwap
                                for data_point in series_data:
                                    try:
                                        # Parse timestamp
                                        timestamp_str = data_point.get('time') or data_point.get('timestamp')
                                        
                                        if timestamp_str:
                                            try:
                                                if isinstance(timestamp_str, (int, float)):
                                                    timestamp = datetime.fromtimestamp(timestamp_str)
                                                elif 'T' in str(timestamp_str):
                                                    # ISO format: "2026-01-15T09:30:00"
                                                    timestamp = datetime.fromisoformat(str(timestamp_str).replace('Z', '+00:00'))
                                                    # Remove timezone if present
                                                    if timestamp.tzinfo:
                                                        timestamp = timestamp.replace(tzinfo=None)
                                                else:
                                                    timestamp = datetime.strptime(str(timestamp_str), '%Y-%m-%d %H:%M:%S')
                                            except Exception as parse_err:
                                                logging.debug(f"Timestamp parse error for {symbol}: {parse_err}")
                                                continue
                                        else:
                                            continue
                                        
                                        # Timesales with interval=1min returns OHLC candles directly
                                        open_price = float(data_point.get('open', 0))
                                        high_price = float(data_point.get('high', 0))
                                        low_price = float(data_point.get('low', 0))
                                        close_price = float(data_point.get('close', 0))
                                        volume = int(data_point.get('volume', 0))
                                        
                                        # Validate candle data
                                        if open_price > 0 and high_price > 0 and low_price > 0 and close_price > 0 and volume > 0:
                                            all_candle_rows.append({
                                                'timestamp': timestamp,
                                                'open': open_price,
                                                'high': high_price,
                                                'low': low_price,
                                                'close': close_price,
                                                'volume': volume
                                            })
                                    except Exception as e:
                                        logging.debug(f"⚠️ Failed to parse candle for {symbol}: {e}")
                                        continue
                            elif resp.status == 400:
                                # API might reject requests for future dates or weekends
                                logging.debug(f"⚠️ Timesales request rejected for {symbol} on {day_date.date()}: {resp.status}")
                            else:
                                logging.debug(f"⚠️ Timesales request failed for {symbol} on {day_date.date()}: {resp.status}")
                
                if all_candle_rows:
                    # Sort by timestamp (oldest first)
                    all_candle_rows.sort(key=lambda x: x['timestamp'])
                    
                    import pandas as pd
                    candles_df = pd.DataFrame(all_candle_rows)
                    self.alpha_engine.load_history(symbol, candles_df)
                    logging.info(f"🔥 Warmed up {symbol} with {len(all_candle_rows)} candles")
                else:
                    logging.warning(f"⚠️ No valid candles fetched for {symbol} (may be weekend/non-trading day)")
            except Exception as e:
                logging.error(f"❌ Warm-up error for {symbol}: {e}")
                import traceback
                traceback.print_exc()
            except Exception as e:
                logging.error(f"❌ Warm-up error for {symbol}: {e}")
                import traceback
                traceback.print_exc()
        
        logging.info("✅ WARM-UP COMPLETE: Indicators ready for trading")

    async def connect(self):
        self.stop_signal = False
        while not self.stop_signal:
            logging.info("🔌 Creating Session...")
            session_id = await self._create_session()
            if not session_id:
                await asyncio.sleep(10)
                continue
                
            try:
                # Fast Start: Warm up indicators with historical data
                await self.warm_up_history()
                
                if not self.vix_poller_running:
                    self.vix_poller_task = asyncio.create_task(self._poll_vix_loop())
                
                if not self.position_manager_task:
                    self.position_manager_task = asyncio.create_task(self._manage_positions_loop())
                
                if not self.iv_poller_task:
                    self.iv_poller_task = asyncio.create_task(self._poll_iv_loop())
                
                # Start Connection Watchdog (Dead Man's Switch)
                if not self.watchdog_task:
                    self.last_msg_time = datetime.now()  # Reset on connect
                    self.watchdog_task = asyncio.create_task(self._monitor_watchdog())
                
                # Startup Reconciliation (Adopt Orphans from Tradier)
                if self._needs_reconciliation:
                    self._needs_reconciliation = False
                    asyncio.create_task(self.reconcile_state())
                
                async with websockets.connect(TRADIER_WS_URL) as websocket:
                    self.ws = websocket
                    self.connected = True
                    self.is_connected = True
                    await self._subscribe(session_id)
                    await self.run(websocket)
            except Exception as e:
                logging.error(f"WS Error: {e}")
                await asyncio.sleep(5)

    async def _subscribe(self, session_id: str):
        if self.ws:
            payload = {"symbols": self.symbols, "filter": ["trade", "quote"], "sessionid": session_id}
            await self.ws.send(json.dumps(payload))

    async def run(self, websocket):
        logging.info(f"🚀 Monitoring: {', '.join(self.symbols)}")
        try:
            async for message in websocket:
                if self.stop_signal: 
                    break
                data = json.loads(message)
                await self._handle_message(data)
        except Exception as e:
            logging.error(f"Run loop error: {e}")
            self.connected = False

    async def disconnect(self):
        self.stop_signal = True
        self.is_connected = False
        if self.ws: 
            await self.ws.close()
        # Stop watchdog
        if self.watchdog_task:
            self.watchdog_task.cancel()

    async def _monitor_watchdog(self):
        """Connection Watchdog (Dead Man's Switch)
        Monitors WebSocket activity and forces reconnect if silence > 60s"""
        while not self.stop_signal:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds
                
                if self.stop_signal:
                    break
                
                now = datetime.now()
                silence_seconds = (now - self.last_msg_time).total_seconds()
                
                if silence_seconds > 60:
                    logging.warning(f"⚠️ WATCHDOG: No data for {int(silence_seconds)}s. Resetting connection...")
                    # Force reconnect by stopping the current connection loop
                    self.stop_signal = True
                    self.is_connected = False
                    if self.ws:
                        try:
                            await self.ws.close()
                        except:
                            pass
                    # Reset watchdog timestamp
                    self.last_msg_time = datetime.now()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Watchdog error: {e}")

    async def _handle_message(self, data: dict):
        # Update watchdog timestamp on any message
        self.last_msg_time = datetime.now()
        
        if data.get('type') == 'trade':
            await self._handle_trade(data)
            if data.get('symbol'): 
                await self._check_signals(data.get('symbol'))
        elif data.get('type') == 'quote':
            await self._handle_quote(data)

    async def _handle_trade(self, data: dict):
        symbol = data.get('symbol')
        price = float(data.get('price', 0))
        size = int(data.get('size', 0))
        if symbol and price > 0:
            self.alpha_engine.update(symbol, price, size)

    async def _handle_quote(self, data: dict):
        symbol = data.get('symbol')
        bid = float(data.get('bid', 0))
        ask = float(data.get('ask', 0))
        if symbol and bid > 0:
            mid = (bid + ask) / 2
            self.alpha_engine.update(symbol, mid, 0)

    # --- SIGNAL LOGIC ---
    async def _check_signals(self, symbol: str):
        if not symbol or symbol not in self.symbols: 
            return
        
        now = datetime.now()
        if symbol in self.last_proposal_time:
            if now - self.last_proposal_time[symbol] < self.min_proposal_interval:
                return

        # 1. GET REGIME (The Governance Check)
        # We use SPY as the global proxy for the market state
        current_regime = self.regime_engine.get_regime('SPY')
        
        indicators = self.alpha_engine.get_indicators(symbol)
        
        # Signal Setup
        signal = None
        strategy = None
        side = None
        option_type = None
        bias = None
        
        current_hour = now.hour
        current_minute = now.minute
        
        # -----------------------------------------------
        # STRATEGY 1: ORB (Opening Range Breakout)
        # PERMISSION: All Regimes EXCEPT Event Risk
        # -----------------------------------------------
        is_orb_window = (current_hour == 10) or (current_hour == 11 and current_minute < 30)
        
        if current_regime.value != 'EVENT_RISK' and is_orb_window and indicators.get('candle_count', 0) >= 30:
            orb = self.alpha_engine.get_opening_range(symbol)
            if orb['complete']:
                price = indicators['price']
                velocity = indicators['volume_velocity']
                
                if price > orb['high'] and velocity > 1.5:
                    signal = 'ORB_BREAKOUT_BULL'
                    strategy = 'CREDIT_SPREAD'
                    side = 'OPEN'
                    option_type = 'PUT'
                    bias = 'bullish'
                elif price < orb['low'] and velocity > 1.5:
                    signal = 'ORB_BREAKOUT_BEAR'
                    strategy = 'CREDIT_SPREAD'
                    side = 'OPEN'
                    option_type = 'CALL'
                    bias = 'bearish'

        # -----------------------------------------------
        # STRATEGY 2: RANGE FARMER (Iron Condor)
        # PERMISSION: ONLY in LOW_VOL_CHOP
        # -----------------------------------------------
        if not signal and current_regime.value == 'LOW_VOL_CHOP' and current_hour == 13 and 0 <= current_minute < 5:
            adx = self.alpha_engine.get_adx(symbol)
            if adx is not None and adx < 20:  # Low Trend
                # Volume Profile Filter: Only enter if price is near POC (within $2 for SPY/QQQ)
                # In chop, price acts like a rubber band around POC. If too far, avoid.
                poc = indicators.get('poc', 0)
                current_price = indicators['price']
                
                if poc > 0 and abs(current_price - poc) < 2.00:
                    logging.info(f"🚜 FARMING: {symbol} ADX {adx:.1f}. Price ${current_price:.2f} near POC ${poc:.2f}. Opening Iron Condor.")
                    # FIX: Use 'CREDIT_SPREAD' so Gatekeeper accepts the order
                    # Leg 1: Bear Call Spread
                    await self._send_proposal(symbol, 'CREDIT_SPREAD', 'OPEN', 'CALL', indicators, 'neutral')
                    # Leg 2: Bull Put Spread
                    await self._send_proposal(symbol, 'CREDIT_SPREAD', 'OPEN', 'PUT', indicators, 'neutral')
                    self.last_proposal_time[symbol] = now
                    return
                elif poc > 0:
                    logging.debug(f"🔍 Vol Profile: Price ${current_price:.2f} vs POC ${poc:.2f} (Distance: ${abs(current_price - poc):.2f}) - Too far from value node, skipping Iron Condor")

        # -----------------------------------------------
        # STRATEGY 3: SCALPER (0DTE)
        # PERMISSION: TRENDING or HIGH_VOL_EXPANSION
        # -----------------------------------------------
        if not signal and current_regime.value in ['TRENDING', 'HIGH_VOL_EXPANSION']:
            rsi_2 = self.alpha_engine.get_rsi(symbol, period=2)
            if rsi_2 is not None and (rsi_2 < 5 or rsi_2 > 95):
                zero_dte = await self._get_0dte_expiration(symbol)
                if zero_dte:
                    if rsi_2 < 5:
                        signal = 'SCALP_BULL_PUT'
                        strategy = 'CREDIT_SPREAD'
                        side = 'OPEN'
                        option_type = 'PUT'
                        bias = 'bullish'
                    else:
                        signal = 'SCALP_BEAR_CALL'
                        strategy = 'CREDIT_SPREAD'
                        side = 'OPEN'
                        option_type = 'CALL'
                        bias = 'bearish'
                        
                    if signal:
                        # ADDITIONAL FILTER (From Feedback Audit):
                        # Don't short a strong uptrend
                        trend_strength = self.alpha_engine.get_adx(symbol)
                        if signal == 'SCALP_BEAR_CALL' and trend_strength is not None and trend_strength > 40:
                            logging.info(f"🚫 SKIPPING SCALP: Trend too strong (ADX {trend_strength:.1f})")
                            signal = None
                            return
                        
                        logging.info(f"⚡ SCALP: {symbol} RSI(2) {rsi_2:.1f}. 0DTE {option_type}.")
                        await self._send_proposal(symbol, strategy, side, option_type, indicators, bias, force_expiration=zero_dte)
                        self.last_signals[symbol] = {'signal': signal, 'timestamp': now}
                        return

        # --- UTILITY 1: EARNINGS ASSASSIN ---
        # Trigger: 3:55 PM on Earnings Day
        # Logic: Sell Iron Condor to capture IV Crush
        # TODO: Connect to a real earnings calendar API
        # For now, hardcode today's earnings symbols here manually or via env var
        EARNINGS_TODAY = []  # Example: ['NFLX', 'TSLA'] - manually set for earnings days
        
        if not signal and symbol in EARNINGS_TODAY and current_hour == 15 and current_minute >= 55:
            # Check if we already fired (deduplication handled by last_proposal_time)
            logging.info(f"🥷 ASSASSIN: Executing Earnings Play on {symbol}")
            await self._send_proposal(symbol, 'CREDIT_SPREAD', 'OPEN', 'CALL', indicators, 'neutral')
            await self._send_proposal(symbol, 'CREDIT_SPREAD', 'OPEN', 'PUT', indicators, 'neutral')
            self.last_proposal_time[symbol] = now
            return

        # --- UTILITY 3: WEEKEND WARRIOR ---
        # Trigger: Friday @ 3:55 PM
        # Logic: Sell premium to collect 2 days of weekend Theta decay
        is_friday = now.weekday() == 4
        if not signal and is_friday and current_hour == 15 and current_minute >= 55:
            # Only trade if market isn't crashing (VIX check)
            vix_value = indicators.get('vix') or 0
            if vix_value < 25:
                logging.info(f"🏖️ WEEKEND WARRIOR: Selling Friday Premium on {symbol}")
                # Sell a Put Spread (betting market won't crash over weekend)
                await self._send_proposal(symbol, 'CREDIT_SPREAD', 'OPEN', 'PUT', indicators, 'bullish')
                self.last_proposal_time[symbol] = now
                return

        # -----------------------------------------------
        # STRATEGY 4: TREND ENGINE
        # PERMISSION: ONLY in TRENDING
        # -----------------------------------------------
        if not signal and current_regime.value == 'TRENDING':
            if not indicators.get('is_warm', False):
                # Log progress occasionally
                if indicators.get('candle_count', 0) % 60 == 0:
                    logging.info(f"⏳ Warmup {symbol}: {indicators.get('candle_count')}/200")
                return

            trend = indicators['trend']
            rsi = indicators['rsi']
            flow = indicators['flow_state']
            
            # Volume Profile Filter (Auction Market Theory)
            # For trend strategies, confirm price is above/below POC relative to trend direction
            poc = indicators.get('poc', 0)
            current_price = indicators['price']
            
            if trend == 'UPTREND' and rsi < 30 and flow != 'NEUTRAL':
                # Bullish: Only take if price is above POC (buyers in control, value migrating up)
                if poc > 0 and current_price > poc:
                    signal = 'BULL_PUT_SPREAD'
                    strategy = 'CREDIT_SPREAD'
                    side = 'OPEN'
                    option_type = 'PUT'
                    bias = 'bullish'
                    logging.info(f"🔍 Vol Profile: Price ${current_price:.2f} vs POC ${poc:.2f} (Above) - Trend confirmed")
                elif poc > 0:
                    logging.debug(f"🔍 Vol Profile: Price ${current_price:.2f} vs POC ${poc:.2f} (Below) - Rejecting bullish signal (not above value)")
            elif trend == 'DOWNTREND' and rsi > 70 and flow != 'NEUTRAL':
                # Bearish: Only take if price is below POC (sellers in control, value migrating down)
                if poc > 0 and current_price < poc:
                    signal = 'BEAR_CALL_SPREAD'
                    strategy = 'CREDIT_SPREAD'
                    side = 'OPEN'
                    option_type = 'CALL'
                    bias = 'bearish'
                    logging.info(f"🔍 Vol Profile: Price ${current_price:.2f} vs POC ${poc:.2f} (Below) - Trend confirmed")
                elif poc > 0:
                    logging.debug(f"🔍 Vol Profile: Price ${current_price:.2f} vs POC ${poc:.2f} (Above) - Rejecting bearish signal (not below value)")

        # Get IV Rank for complex strategies
        iv_rank = self.alpha_engine.get_iv_rank(symbol)

        # -----------------------------------------------
        # STRATEGY 5: IRON BUTTERFLY ("The Pin")
        # PERMISSION: CHOP Regime + High IV
        # -----------------------------------------------
        if not signal and current_regime.value == 'LOW_VOL_CHOP':
            # Only enter at lunchtime (12:00 - 13:00) when things settle
            if current_hour == 12:
                if iv_rank > 50:  # Premium is expensive -> Sell it
                    # Volume Profile Filter: Only enter if price is near POC (within $2 for SPY/QQQ)
                    # Iron Butterfly should be centered on the value node (POC)
                    poc = indicators.get('poc', 0)
                    current_price = indicators['price']
                    
                    if poc > 0 and abs(current_price - poc) < 2.00:
                        logging.info(f"🦋 BUTTERFLY: {symbol} High IV ({iv_rank:.0f}) in Chop. Price ${current_price:.2f} near POC ${poc:.2f}. Targeting Pin.")
                        
                        # Manual Proposal Construction for Complex Strategy
                        exp = await self._get_best_expiration(symbol)
                        if exp:
                            chain = await self._get_option_chain(symbol, exp)
                            if chain:
                                legs = await self._find_iron_butterfly_legs(chain, indicators['price'], exp)
                                if legs:
                                    await self._send_complex_proposal(symbol, 'IRON_BUTTERFLY', 'OPEN', legs, indicators, 'neutral')
                                    self.last_proposal_time[symbol] = now
                                    return
                    elif poc > 0:
                        logging.debug(f"🔍 Vol Profile: Price ${current_price:.2f} vs POC ${poc:.2f} (Distance: ${abs(current_price - poc):.2f}) - Too far from value node, skipping Iron Butterfly")

        # -----------------------------------------------
        # STRATEGY 6: RATIO SPREAD ("The Hedge")
        # PERMISSION: ANY Regime (Defense) + Low IV
        # -----------------------------------------------
        if not signal and iv_rank < 20:  # Vol is dirt cheap
            # Check if we already have downside protection? (TODO)
            # Only fire occasionally to avoid over-hedging
            if current_minute == 30:  # Check once an hour
                logging.info(f"🛡️ HEDGE: {symbol} IV Low ({iv_rank:.0f}). Looking for Ratio Spread.")
                
                exp = await self._get_best_expiration(symbol)
                if exp:
                    chain = await self._get_option_chain(symbol, exp)
                    if chain:
                        legs = await self._find_ratio_spread_legs(chain, indicators['price'], exp)
                        if legs:
                            # Only trade if we can do it for a credit or zero cost
                            # (Pricing check logic would go here, trusting Gatekeeper Limit for now)
                            await self._send_complex_proposal(symbol, 'RATIO_SPREAD', 'OPEN', legs, indicators, 'bearish')
                            self.last_proposal_time[symbol] = now
                            return

        if signal:
            last = self.last_signals.get(symbol, {})
            if last.get('signal') == signal and (now - last.get('timestamp')).seconds < 300:
                return

            logging.info(f"🎯 SIGNAL: {signal} on {symbol}")
            await self._send_proposal(symbol, strategy, side, option_type, indicators, bias)
            
            self.last_proposal_time[symbol] = now
            self.last_signals[symbol] = {'signal': signal, 'timestamp': now}
        
        # Export state for dashboard (after signal check)
        self.export_state()

    # --- PRODUCTION GRADE HELPERS ---

    async def _get_expirations(self, symbol: str) -> List[str]:
        headers = {'Authorization': f'Bearer {self.access_token}', 'Accept': 'application/json'}
        url = f'{TRADIER_API_BASE}/markets/options/expirations'
        params = {'symbol': symbol, 'includeAllRoots': 'true'}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        exps = data.get('expirations', {}).get('date', [])
                        return exps if isinstance(exps, list) else [exps]
                    return []
        except: 
            return []

    async def _get_best_expiration(self, symbol: str) -> Optional[str]:
        # Target: 30 DTE (Sweet Spot)
        exps = await self._get_expirations(symbol)
        if not exps: 
            return None
        
        today = datetime.now().date()
        valid = []
        for e in exps:
            try:
                dte = (datetime.strptime(e, '%Y-%m-%d').date() - today).days
                if 14 <= dte <= 45: 
                    valid.append((dte, e))
            except: 
                continue
            
        if not valid: 
            return None
        valid.sort(key=lambda x: abs(x[0] - 30))
        return valid[0][1]

    async def _get_0dte_expiration(self, symbol: str) -> Optional[str]:
        # Target: TODAY
        exps = await self._get_expirations(symbol)
        today_str = datetime.now().strftime('%Y-%m-%d')
        return today_str if today_str in exps else None

    async def _get_option_chain(self, symbol: str, expiration: str) -> List[Dict]:
        headers = {'Authorization': f'Bearer {self.access_token}', 'Accept': 'application/json'}
        url = f'{TRADIER_API_BASE}/markets/options/chains'
        params = {'symbol': symbol, 'expiration': expiration, 'greeks': 'true'}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        opts = data.get('options', {}).get('option', [])
                        return opts if isinstance(opts, list) else [opts]
                    return []
        except: 
            return []

    async def _get_atm_iv(self, symbol: str) -> float:
        """Fetch At-The-Money Implied Volatility"""
        # 1. Get Price
        inds = self.alpha_engine.get_indicators(symbol)
        price = inds.get('price')
        if not price:
            return 0.0

        # 2. Get Expiration (~30 days out)
        exp = await self._get_best_expiration(symbol)
        if not exp:
            return 0.0

        # 3. Get Chain
        chain = await self._get_option_chain(symbol, exp)
        if not chain:
            return 0.0

        # 4. Find ATM Option
        # Sort by distance to price
        chain.sort(key=lambda x: abs(float(x.get('strike', 0)) - price))
        
        # Take the closest Call and Put
        atm_call = next((x for x in chain if x.get('option_type', '').lower() == 'call'), None)
        atm_put = next((x for x in chain if x.get('option_type', '').lower() == 'put'), None)
        
        ivs = []
        if atm_call:
            greeks = atm_call.get('greeks', {})
            iv = float(greeks.get('mid_iv', 0) or greeks.get('iv', 0) or 0)
            if iv > 0:
                ivs.append(iv)
        if atm_put:
            greeks = atm_put.get('greeks', {})
            iv = float(greeks.get('mid_iv', 0) or greeks.get('iv', 0) or 0)
            if iv > 0:
                ivs.append(iv)
            
        if not ivs:
            return 0.0
        
        return sum(ivs) / len(ivs) * 100  # Convert to percentage (e.g. 0.15 -> 15.0)

    async def _poll_iv_loop(self):
        """Background task: Poll ATM IV every 15 minutes"""
        logging.info("📊 IV Tracker: STARTED")
        while not self.stop_signal:
            for symbol in self.symbols:
                try:
                    iv = await self._get_atm_iv(symbol)
                    if iv > 0:
                        self.alpha_engine.update_iv(symbol, iv)
                        rank = self.alpha_engine.get_iv_rank(symbol)
                        logging.info(f"📊 IV UPDATE: {symbol} IV: {iv:.1f}% | Rank: {rank:.1f}")
                except Exception as e:
                    logging.error(f"⚠️ IV Poll Error ({symbol}): {e}")
                
                await asyncio.sleep(2)  # Stagger requests
            
            # Sleep 15 minutes
            for _ in range(15 * 60):
                if self.stop_signal:
                    break
                await asyncio.sleep(1)

    def _make_leg(self, chain, expiration, strike, o_type, side, qty):
        """Helper to build a leg object"""
        # Find exact option in chain
        candidates = [x for x in chain if 
                      x.get('option_type') == o_type.lower() and 
                      abs(float(x.get('strike', 0)) - strike) < 0.01]
        if not candidates:
            return None
        opt = candidates[0]
        return {
            'symbol': opt['symbol'],
            'expiration': expiration,
            'strike': float(opt['strike']),
            'type': o_type,
            'quantity': qty,
            'side': side
        }

    async def _find_iron_butterfly_legs(self, chain: List[Dict], price: float, expiration: str) -> List[Dict]:
        """
        Construct Iron Butterfly: Sell ATM Call/Put, Buy OTM Wings
        Target: Sell closest strike to Price. Buy wings $5-10 away.
        """
        # 1. Find ATM Strike (Body)
        strikes = sorted(list(set(float(x.get('strike', 0)) for x in chain)))
        if not strikes:
            return []
        atm_strike = min(strikes, key=lambda x: abs(x - price))
        
        # 2. Find Wings (Protection)
        # Dynamic width based on price (approx 1-2%)
        width = 5.0 if price < 200 else 10.0
        upper_wing = atm_strike + width
        lower_wing = atm_strike - width
        
        # 3. Select Legs
        legs = []
        # Short ATM Call
        call_leg = self._make_leg(chain, expiration, atm_strike, 'CALL', 'SELL', 1)
        if call_leg:
            legs.append(call_leg)
        # Short ATM Put
        put_leg = self._make_leg(chain, expiration, atm_strike, 'PUT', 'SELL', 1)
        if put_leg:
            legs.append(put_leg)
        # Long OTM Call (Upper Wing)
        upper_leg = self._make_leg(chain, expiration, upper_wing, 'CALL', 'BUY', 1)
        if upper_leg:
            legs.append(upper_leg)
        # Long OTM Put (Lower Wing)
        lower_leg = self._make_leg(chain, expiration, lower_wing, 'PUT', 'BUY', 1)
        if lower_leg:
            legs.append(lower_leg)
        
        # Verify we found all 4
        if len(legs) != 4:
            return []
        return legs

    async def _find_ratio_spread_legs(self, chain: List[Dict], price: float, expiration: str) -> List[Dict]:
        """
        Construct Put Ratio Backspread: Sell 1 ATM Put, Buy 2 OTM Puts
        Target: Sell 30 Delta, Buy 15 Delta (approx).
        """
        # Helper to find option by delta
        def find_by_delta(c_chain, target_delta, o_type):
            # Sort by distance to target delta
            candidates = [x for x in c_chain if x.get('option_type', '').lower() == o_type.lower()]
            if not candidates:
                return None
            # Filter out options without delta data
            with_delta = [x for x in candidates if x.get('greeks', {}).get('delta') is not None]
            if not with_delta:
                return None
            return min(with_delta, key=lambda x: abs(float(x.get('greeks', {}).get('delta', 0)) - target_delta))

        # 1. Sell Leg (Short 1) - Near the money
        short_opt = find_by_delta(chain, -0.30, 'PUT')
        if not short_opt:
            return []
        
        # 2. Buy Leg (Long 2) - Further OTM
        long_opt = find_by_delta(chain, -0.15, 'PUT')
        if not long_opt:
            return []
        
        # Ensure distinct strikes (long must be lower strike for puts)
        if float(long_opt.get('strike', 0)) >= float(short_opt.get('strike', 0)):
            return []

        legs = []
        # Sell 1
        legs.append({
            'symbol': short_opt['symbol'],
            'expiration': expiration,
            'strike': float(short_opt['strike']),
            'type': 'PUT',
            'quantity': 1,
            'side': 'SELL'
        })
        # Buy 2
        legs.append({
            'symbol': long_opt['symbol'],
            'expiration': expiration,
            'strike': float(long_opt['strike']),
            'type': 'PUT',
            'quantity': 2,  # RATIO!
            'side': 'BUY'
        })
        return legs

    async def _send_proposal(self, symbol, strategy, side, option_type, indicators, bias, force_expiration=None):
        """Constructs proposal using REAL Delta Selection and REAL Pricing"""
        
        # 1. Expiration
        if force_expiration:
            exp_str = force_expiration
        else:
            exp_str = await self._get_best_expiration(symbol)
            
        if not exp_str: 
            return

        # 2. Chain
        chain = await self._get_option_chain(symbol, exp_str)
        if not chain: 
            return

        # Helper: Safely get delta
        def get_delta(o):
            try: 
                return float(o.get('greeks', {}).get('delta', 0))
            except: 
                return 0.0

        current_price = indicators['price']
        
        # 3. Strike Selection (DELTA ADAPTIVE)
        # Target: Sell the 20 Delta (0.20 probability ITM)
        # If Delta unavailable, fallback to 2% OTM
        
        target_delta = 0.20
        options = [o for o in chain if o.get('option_type') == option_type.lower()]
        options.sort(key=lambda x: float(x.get('strike', 0)))
        
        short_leg = None
        long_leg = None
        
        if option_type == 'PUT':
            # Puts have negative delta (e.g. -0.20)
            # Find strikes below price
            candidates = [o for o in options if float(o['strike']) < current_price]
            
            # Try Delta First
            if candidates and abs(get_delta(candidates[0])) > 0.01: 
                # Find option with delta closest to -0.20
                short_leg = min(candidates, key=lambda x: abs(get_delta(x) - (-target_delta)))
            else:
                # Fallback to 2% OTM
                target_strike = current_price * 0.98
                candidates = [o for o in candidates if float(o['strike']) <= target_strike]
                if candidates: 
                    short_leg = candidates[-1]
                
            if short_leg:
                # Long leg: $5 lower
                s_strike = float(short_leg['strike'])
                longs = [o for o in options if float(o['strike']) <= s_strike - 5]
                if longs: 
                    long_leg = longs[-1]

        else:  # CALL
            # Calls have positive delta
            # Find strikes above price
            candidates = [o for o in options if float(o['strike']) > current_price]
            
            # Try Delta First
            if candidates and abs(get_delta(candidates[0])) > 0.01:
                short_leg = min(candidates, key=lambda x: abs(get_delta(x) - target_delta))
            else:
                # Fallback to 2% OTM
                target_strike = current_price * 1.02
                candidates = [o for o in candidates if float(o['strike']) >= target_strike]
                if candidates: 
                    short_leg = candidates[0]
                
            if short_leg:
                # Long leg: $5 higher
                s_strike = float(short_leg['strike'])
                longs = [o for o in options if float(o['strike']) >= s_strike + 5]
                if longs: 
                    long_leg = longs[0]

        if not short_leg or not long_leg: 
            return

        # 4. Real Pricing
        short_bid = float(short_leg.get('bid', 0))
        long_ask = float(long_leg.get('ask', 0))
        
        if short_bid == 0 or long_ask == 0: 
            return  # No liquidity

        fair_credit = short_bid - long_ask
        limit_price = max(0.05, fair_credit - 0.05)  # 5 cent buffer

        # 5. Real Metrics (No Stubs)
        vix = indicators.get('vix') or 0
        if vix < 15: 
            vol_state = 'low'
        elif vix < 25: 
            vol_state = 'normal'
        else: 
            vol_state = 'high'
        
        velocity = indicators.get('volume_velocity', 1.0)
        imbalance_score = min(10, max(0, (velocity - 1.0) * 5))

        # 6. Position Sizing (Professional Grade)
        # Calculate spread width (max loss per contract)
        short_strike = float(short_leg['strike'])
        long_strike = float(long_leg['strike'])
        spread_width = abs(short_strike - long_strike)
        
        # Fetch equity and calculate quantity
        equity = await self._get_account_equity()
        qty = self.position_sizer.calculate_size(equity, spread_width)
        
        # Log sizing decision
        risk_amount = equity * 0.02  # 2% risk
        max_loss_per_contract = spread_width * 100
        logging.info(f"⚖️ SIZING: Equity ${equity:,.0f} | Risk 2% (${risk_amount:,.0f}) | "
                    f"Width ${spread_width:.2f} (Max Loss ${max_loss_per_contract:.0f}/contract) -> Qty {qty}")

        # 7. Proposal
        proposal = {
            'symbol': symbol,
            'strategy': strategy,
            'side': side,
            'quantity': qty,  # Dynamic quantity based on risk
            'price': round(limit_price, 2),
            'legs': [
                {
                    'symbol': short_leg['symbol'],
                    'expiration': exp_str,
                    'strike': float(short_leg['strike']),
                    'type': option_type,
                    'quantity': qty,  # Dynamic quantity
                    'side': 'SELL'
                },
                {
                    'symbol': long_leg['symbol'],
                    'expiration': exp_str,
                    'strike': float(long_leg['strike']),
                    'type': option_type,
                    'quantity': qty,  # Dynamic quantity
                    'side': 'BUY'
                }
            ],
            'context': {
                'vix': vix,
                'flow_state': indicators['flow_state'],
                'trend_state': bias,
                'vol_state': vol_state,        # REAL DATA
                'rsi': indicators['rsi'],
                'vwap': indicators['vwap'],
                'volume_velocity': velocity,
                'imbalance_score': round(imbalance_score, 1),  # REAL DATA
                'poc': indicators.get('poc', 0),  # Point of Control (Market Structure)
                'price': indicators['price']  # Current price for structure comparison
            }
        }
        
        response = await self.gatekeeper_client.send_proposal(proposal)
        
        # Track approved trades for position management
        if response and response.get('status') == 'APPROVED':
            # Extract order_id from response (may be in 'data' or top-level)
            order_id = response.get('order_id') or (response.get('data', {}).get('order_id') if isinstance(response.get('data'), dict) else None)
            trade_id = f"{symbol}_{strategy}_{int(datetime.now().timestamp())}"
            
            if order_id:
                self.open_positions[trade_id] = {
                    'symbol': symbol,
                    'strategy': strategy,
                    'status': 'OPENING',  # WAIT FOR FILL!
                    'open_order_id': str(order_id),
                    'opening_timestamp': datetime.now(),
                    'legs': proposal['legs'],  # Contains the specific option symbols
                    'entry_price': proposal['price'],
                    'bias': bias,
                    'timestamp': datetime.now(),
                    'highest_pnl': -100.0  # Initialize for Trailing Stop tracking
                }
                logging.info(f"📝 Proposal Approved: {trade_id}. Waiting for Entry Fill (Order {order_id})...")
                self._save_positions_to_disk()
            else:
                logging.error(f"❌ Approved but missing Order ID for {trade_id}. Response: {response}")

    async def _send_complex_proposal(self, symbol, strategy, side, legs, indicators, bias):
        """Send a pre-constructed multi-leg proposal"""
        
        # Calculate Price (Net Credit/Debit from all legs)
        # 1. Get Symbols
        leg_symbols = [l['symbol'] for l in legs]
        quotes = await self._get_quotes(leg_symbols)
        
        net_price = 0.0
        for leg in legs:
            quote_data = quotes.get(leg['symbol'])
            if not quote_data:
                # Missing quote, skip this trade
                logging.warning(f"⚠️ Missing quote for {leg['symbol']}, skipping complex proposal")
                return
            price = quote_data['price']
            if leg['side'] == 'SELL':
                net_price += price * leg['quantity']
            else:
                net_price -= price * leg['quantity']
        
        # If Opening: We prefer Credit (>0). If Debit (<0), ensure it's small.
        # Ratio Spread might be small debit.
        limit_price = abs(net_price)  # Gatekeeper expects positive limit price
        
        # Position Sizing (Professional Grade) - Complex Trades
        # Calculate spread width based on strategy
        spread_width = 0.0
        call_width = 0.0
        put_width = 0.0
        
        if strategy == 'IRON_CONDOR':
            # Iron Condor: Use the larger wing width (call wing or put wing)
            # Group legs by type
            call_legs = [l for l in legs if l['type'] == 'CALL']
            put_legs = [l for l in legs if l['type'] == 'PUT']
            
            if call_legs and len(call_legs) >= 2:
                # Calculate call wing width
                call_strikes = sorted([l['strike'] for l in call_legs])
                call_width = abs(call_strikes[-1] - call_strikes[0])
            
            if put_legs and len(put_legs) >= 2:
                # Calculate put wing width
                put_strikes = sorted([l['strike'] for l in put_legs])
                put_width = abs(put_strikes[-1] - put_strikes[0])
            
            # Use the larger width (worst case max loss)
            spread_width = max(call_width, put_width) if (call_width > 0 or put_width > 0) else 5.0
        
        elif strategy == 'IRON_BUTTERFLY':
            # Iron Butterfly: Wing width (distance from center to wing)
            strikes = sorted(set([l['strike'] for l in legs]))
            if len(strikes) >= 3:
                # Center strike is typically the middle one
                center = strikes[len(strikes) // 2]
                # Wing width is distance from center to outer wing
                spread_width = abs(strikes[-1] - strikes[0])
            else:
                # Fallback: use max spread between any two strikes
                spread_width = abs(max(strikes) - min(strikes))
        
        elif strategy == 'RATIO_SPREAD':
            # Ratio Spread: Width is the distance between the short and long strikes
            strikes = sorted(set([l['strike'] for l in legs]))
            if len(strikes) >= 2:
                spread_width = abs(strikes[-1] - strikes[0])
            else:
                spread_width = 5.0  # Default fallback
        
        else:
            # Unknown strategy, use default
            spread_width = 5.0
            logging.warning(f"⚠️ Unknown strategy {strategy} for sizing, using default width: $5")
        
        # Fetch equity and calculate quantity
        equity = await self._get_account_equity()
        qty = self.position_sizer.calculate_size(equity, spread_width)
        
        # Log sizing decision
        risk_amount = equity * 0.02  # 2% risk
        max_loss_per_contract = spread_width * 100
        logging.info(f"⚖️ SIZING ({strategy}): Equity ${equity:,.0f} | Risk 2% (${risk_amount:,.0f}) | "
                    f"Width ${spread_width:.2f} (Max Loss ${max_loss_per_contract:.0f}/contract) -> Qty {qty}")
        
        # Update leg quantities to match calculated quantity
        # For ratio spreads, preserve the ratio (e.g., 1:2), so multiply base quantity
        updated_legs = []
        for leg in legs:
            updated_leg = leg.copy()
            # For ratio spreads, preserve the ratio
            if strategy == 'RATIO_SPREAD':
                # Keep the ratio intact (e.g., 1:2 becomes qty:qty*2)
                # The original leg quantity already has the ratio (1 or 2)
                updated_leg['quantity'] = leg['quantity'] * qty
            else:
                # Standard multi-leg: all legs get same quantity
                updated_leg['quantity'] = qty
            updated_legs.append(updated_leg)
        
        # CRITICAL FIX: Recalculate net_price with updated quantities
        # The original net_price was calculated with base quantities (qty=1)
        # Now that we've scaled to actual qty, we need to recalculate the total
        net_price_updated = 0.0
        for leg in updated_legs:
            quote_data = quotes.get(leg['symbol'])
            if quote_data:
                price = quote_data['price']
                if leg['side'] == 'SELL':
                    net_price_updated += price * leg['quantity']
                else:
                    net_price_updated -= price * leg['quantity']
        
        # Use the updated net_price (scaled to actual quantity)
        limit_price = abs(net_price_updated)  # Gatekeeper expects positive limit price
        
        # Construct Context
        context = {
            'vix': indicators.get('vix', 0),
            'flow_state': indicators.get('flow_state', 'UNKNOWN'),
            'iv_rank': self.alpha_engine.get_iv_rank(symbol),
            'strategy_logic': 'Complex Structure',
            'poc': indicators.get('poc', 0),  # Point of Control (Market Structure)
            'price': indicators.get('price', 0)  # Current price for structure comparison
        }

        proposal = {
            'symbol': symbol,
            'strategy': strategy,
            'side': side,
            'quantity': qty,  # Dynamic quantity based on risk
            'price': round(limit_price, 2),  # Now correctly scaled to actual quantity
            'legs': updated_legs,  # Updated with dynamic quantities
            'context': context
        }
        
        response = await self.gatekeeper_client.send_proposal(proposal)
        
        if response and response.get('status') == 'APPROVED':
            # Extract order_id from response (may be in 'data' or top-level)
            order_id = response.get('order_id') or (response.get('data', {}).get('order_id') if isinstance(response.get('data'), dict) else None)
            trade_id = f"{symbol}_{strategy}_{int(datetime.now().timestamp())}"
            
            if order_id:
                self.open_positions[trade_id] = {
                    'symbol': symbol,
                    'strategy': strategy,
                    'status': 'OPENING',  # WAIT FOR FILL!
                    'open_order_id': str(order_id),
                    'opening_timestamp': datetime.now(),
                    'legs': updated_legs,  # Use updated legs with dynamic quantities
                    'entry_price': round(limit_price, 2),
                    'bias': bias,
                    'timestamp': datetime.now(),
                    'highest_pnl': -100.0
                }
                logging.info(f"📝 Complex Proposal Approved: {trade_id}. Waiting for Entry Fill (Order {order_id})...")
                self._save_positions_to_disk()
            else:
                logging.error(f"❌ Approved but missing Order ID for {trade_id}. Response: {response}")
