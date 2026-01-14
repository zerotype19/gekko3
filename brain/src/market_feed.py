"""
Market Feed (Production Grade)
Connects to Tradier WebSocket and feeds data to AlphaEngine
Generates trading signals based on technical indicators
Includes: Real-Time Pricing + Dynamic Expiration + Delta Strike Selection
"""

import asyncio
import json
import websockets
import aiohttp
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Optional, Set, List
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
        
        # Position Management (Smart Manager)
        self.open_positions: Dict[str, Dict] = {}
        self.position_manager_task: Optional[asyncio.Task] = None
        
        # Portfolio Greeks (Phase C: Dashboard)
        self.portfolio_greeks = {'delta': 0.0, 'theta': 0.0, 'vega': 0.0}
        
        # Dashboard state export (write to project root for Streamlit dashboard)
        # The dashboard runs from project root, so we need to write there
        # If running from brain/ directory, go up one level; otherwise use current dir
        import os
        current_dir = os.getcwd()
        if current_dir.endswith('brain'):
            # Running from brain/ directory, write to parent (project root)
            self.state_file = os.path.join(os.path.dirname(current_dir), 'brain_state.json')
        else:
            # Running from project root
            self.state_file = 'brain_state.json'
    
    def export_state(self):
        """Dumps RICH brain state to JSON for the dashboard (Phase C: Step 3)"""
        
        # 1. Global State
        regime = 'UNKNOWN'
        try: 
            regime = self.regime_engine.get_regime('SPY').value
        except: 
            pass

        system_state = {
            'timestamp': datetime.now().isoformat(),
            'regime': regime,
            'portfolio_risk': self.portfolio_greeks,
            'open_positions': len(self.open_positions),
            'status': 'CONNECTED' if self.is_connected else 'DISCONNECTED'
        }

        # 2. Symbol State
        symbols_data = {}
        for symbol in self.symbols:
            inds = self.alpha_engine.get_indicators(symbol)
            iv_rank = self.alpha_engine.get_iv_rank(symbol)
            
            symbols_data[symbol] = {
                'price': inds.get('price', 0),
                'rsi': inds.get('rsi', 50),
                'adx': self.alpha_engine.get_adx(symbol),
                'iv_rank': iv_rank,  # NEW
                'trend': inds.get('trend', 'UNKNOWN'),
                'flow': inds.get('flow_state', 'NEUTRAL'),
                'vix': inds.get('vix', 0),
                'volume_velocity': inds.get('volume_velocity', 1.0),
                'active_signal': self.last_signals.get(symbol, {}).get('signal', None)
            }
        
        final_export = {
            'system': system_state,
            'market': symbols_data
        }
        
        # Save to file
        try:
            with open(self.state_file, 'w') as f:
                json.dump(final_export, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to export state: {e}")

    # --- POSITION MANAGEMENT (AUTOPILOT) ---
    
    async def _manage_positions_loop(self):
        """Background task to monitor and manage open positions"""
        logging.info("🛡️ Position Manager: ONLINE")
        while not self.stop_signal:
            try:
                if self.open_positions:
                    await self._manage_positions()
            except Exception as e:
                logging.error(f"⚠️ Manager Error: {e}")
            await asyncio.sleep(5)  # Check every 5 seconds

    async def _get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """
        Fetch real-time quotes AND Greeks for option symbols.
        Returns Dict: {symbol: {'price': float, 'delta': float, 'theta': float, 'vega': float}}
        """
        if not symbols:
            return {}
        
        headers = {'Authorization': f'Bearer {self.access_token}', 'Accept': 'application/json'}
        url = f'{TRADIER_API_BASE}/markets/quotes'
        # Request Greeks explicitly
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
                            
                            # Price Logic (Mid or Last)
                            bid = float(q.get('bid', 0) or 0)
                            ask = float(q.get('ask', 0) or 0)
                            if bid > 0 and ask > 0:
                                price = (bid + ask) / 2
                            else:
                                price = float(q.get('last', 0) or 0)
                                
                            # Greeks Logic (Safely parse)
                            greeks = q.get('greeks', {})
                            if greeks is None:
                                greeks = {}
                            
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
        Smart Manager 2.0: Advanced Exit Logic
        Includes: Trailing Stops, Technical Guards (SMA/ADX), and Hard Stops.
        """
        # 1. Collect all option symbols we need to check
        all_legs = []
        for pos in self.open_positions.values():
            for leg in pos['legs']:
                all_legs.append(leg['symbol'])
        
        if not all_legs:
            return
        
        # 2. Batch fetch prices
        quotes = await self._get_quotes(all_legs)
        if not quotes:
            return

        # 3. Check Rules
        now = datetime.now()
        for trade_id, pos in list(self.open_positions.items()):
            symbol = pos['symbol']
            
            # --- P&L CALCULATION (Updated for Greeks) ---
            cost_to_close = 0.0
            missing_quote = False
            
            # Track Trade-Level Greeks
            trade_delta = 0.0
            trade_theta = 0.0
            trade_vega = 0.0
            
            for leg in pos['legs']:
                quote_data = quotes.get(leg['symbol'])  # Now a dict
                if not quote_data:
                    missing_quote = True
                    break
                
                price = quote_data['price']
                qty = float(leg['quantity'])
                
                # To Close: Buy Short (+), Sell Long (-)
                if leg['side'] == 'SELL':
                    cost_to_close += price * qty
                    # Short Option Greeks: Invert sign
                    trade_delta -= quote_data['delta'] * 100 * qty  # 1 contract = 100 shares
                    trade_theta -= quote_data['theta'] * 100 * qty
                    trade_vega -= quote_data['vega'] * 100 * qty
                else:
                    cost_to_close -= price * qty
                    # Long Option Greeks: Keep sign
                    trade_delta += quote_data['delta'] * 100 * qty
                    trade_theta += quote_data['theta'] * 100 * qty
                    trade_vega += quote_data['vega'] * 100 * qty
            
            # Store live greeks in position for debugging
            pos['live_greeks'] = {'delta': trade_delta, 'theta': trade_theta, 'vega': trade_vega}
            
            if missing_quote or cost_to_close <= 0:
                continue
            
            entry_credit = pos['entry_price']
            # ROI: (Entry - Current) / Entry. Example: Sold @ 1.00, Buy @ 0.50 = 50% Profit
            pnl_pct = ((entry_credit - cost_to_close) / entry_credit) * 100
            
            # Update High Water Mark (Trailing Stop Base)
            if pnl_pct > pos.get('highest_pnl', -100):
                pos['highest_pnl'] = pnl_pct

            # --- EXIT REASONING ---
            should_close = False
            reason = ""
            
            # Get latest technicals for context
            indicators = self.alpha_engine.get_indicators(symbol)
            current_price = indicators['price']
            sma_200 = indicators.get('sma_200')
            adx = self.alpha_engine.get_adx(symbol)

            # Detect Strategy Type
            is_scalper = False
            if pos['legs']:
                try:
                    exp_str = pos['legs'][0].get('expiration', '')
                    exp = datetime.strptime(exp_str, '%Y-%m-%d').date()
                    if (exp - now.date()).days == 0:
                        is_scalper = True
                except:
                    pass

            # ----------------------------------------
            # STRATEGY 1: SCALPER (0DTE)
            # ----------------------------------------
            if is_scalper:
                rsi = indicators['rsi']
                # Rule A: Mean Reversion (Classic)
                if rsi is not None:
                    if pos.get('bias') == 'bullish' and rsi > 60:  # Let it run a bit more than 50
                        should_close = True
                        reason = f"Scalp Win (RSI {rsi:.1f})"
                    elif pos.get('bias') == 'bearish' and rsi < 40:
                        should_close = True
                        reason = f"Scalp Win (RSI {rsi:.1f})"
                
                # Rule B: Hard Stop
                if pnl_pct < -20:
                    should_close = True
                    reason = "Scalp Hard Stop (-20%)"

            # ----------------------------------------
            # STRATEGY 2: TREND & ORB (Directional)
            # ----------------------------------------
            elif pos['strategy'] == 'CREDIT_SPREAD' and pos.get('bias') in ['bullish', 'bearish']:
                # Rule A: Trailing Stop (The "Let Winners Run" Rule)
                # If we hit 30% profit, trigger trail. Close if we drop 10% from peak.
                if pos['highest_pnl'] >= 30 and (pos['highest_pnl'] - pnl_pct) >= 10:
                    should_close = True
                    reason = f"Trailing Stop (Peak {pos['highest_pnl']:.1f}%)"
                
                # Rule B: Technical Fail (Trend Reversal)
                # Bullish trade but Price crashes below SMA 200? Get out.
                if pos.get('bias') == 'bullish' and sma_200 and current_price < sma_200:
                    should_close = True
                    reason = "Trend Broken (Price < SMA200)"
                # Bearish trade but Price rallies above SMA 200? Get out.
                if pos.get('bias') == 'bearish' and sma_200 and current_price > sma_200:
                    should_close = True
                    reason = "Trend Broken (Price > SMA200)"

                # Rule C: Hard Target/Stop (Safety Net)
                if pnl_pct >= 80:
                    should_close = True
                    reason = "Max Profit (+80%)"
                if pnl_pct <= -100:
                    should_close = True
                    reason = "Stop Loss (-100%)"

            # ----------------------------------------
            # STRATEGY 3: RANGE FARMER (Neutral)
            # ----------------------------------------
            elif pos.get('bias') == 'neutral':
                # Rule A: Volatility Spike (ADX)
                # If ADX goes above 30, the market is trending. Condors will die. Exit.
                if adx is not None and adx > 30:
                    should_close = True
                    reason = f"Volatility Spike (ADX {adx:.1f})"
                
                # Rule B: Standard Profit/Loss
                if pnl_pct >= 50:
                    should_close = True
                    reason = "Take Profit (+50%)"
                if pnl_pct <= -100:
                    should_close = True
                    reason = "Stop Loss (-100%)"

            # ----------------------------------------
            # GLOBAL RULES
            # ----------------------------------------
            # EOD Force Close (3:55 PM)
            if now.hour == 15 and now.minute >= 55:
                should_close = True
                reason = "EOD Auto-Close"

            # EXECUTE
            if should_close:
                logging.info(f"🛑 CLOSING {trade_id} | P&L: {pnl_pct:.1f}% | Reason: {reason}")
                await self._execute_close(trade_id, pos, cost_to_close)
        
        # Log Portfolio Risk (after checking all positions)
        self._log_portfolio_risk()

    def _log_portfolio_risk(self):
        """Log AND STORE total portfolio exposure (The 'Risk Dashboard')"""
        total_delta = 0.0
        total_theta = 0.0
        total_vega = 0.0
        
        count = 0
        for pos in self.open_positions.values():
            greeks = pos.get('live_greeks', {})
            total_delta += greeks.get('delta', 0)
            total_theta += greeks.get('theta', 0)
            total_vega += greeks.get('vega', 0)
            count += 1
        
        # STORE IT (Phase C: Dashboard)
        self.portfolio_greeks = {
            'delta': total_delta,
            'theta': total_theta,
            'vega': total_vega
        }
            
        if count > 0:
            logging.info(f"📊 PORTFOLIO RISK: Delta {total_delta:+.1f} | Theta {total_theta:+.1f} | Vega {total_vega:+.1f} | Positions: {count}")

    async def _execute_close(self, trade_id: str, pos: Dict, limit_price: float):
        """Send CLOSE order to Gatekeeper"""
        # Construct Close Proposal (Gatekeeper handles leg inversion based on side='CLOSE')
        # Add buffer to limit price to ensure fill (pay 5 cents more for debit)
        execution_price = limit_price + 0.05
        
        proposal = {
            'symbol': pos['symbol'],
            'strategy': pos['strategy'],
            'side': 'CLOSE',
            'quantity': 1,
            'price': round(execution_price, 2),
            'legs': pos['legs'],  # Send same legs, Gatekeeper inverts logic based on 'CLOSE' side
            'context': {
                'reason': 'Manage Position',
                'closing_trade_id': trade_id
            }
        }
        
        resp = await self.gatekeeper_client.send_proposal(proposal)
        if resp and resp.get('status') == 'APPROVED':
            del self.open_positions[trade_id]
            logging.info(f"✅ Closed {trade_id}")

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
                            if isinstance(quote, list): quote = quote[0]
                            if quote and quote.get('last') is not None:
                                self.alpha_engine.set_vix(float(quote['last']), datetime.now())
            except Exception as e:
                logging.error(f"❌ VIX poller error: {e}")
            
            for _ in range(6): 
                if self.stop_signal: break
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

    async def connect(self):
        self.stop_signal = False
        while not self.stop_signal:
            logging.info("🔌 Creating Session...")
            session_id = await self._create_session()
            if not session_id:
                await asyncio.sleep(10)
                continue
                
            try:
                if not self.vix_poller_running:
                    self.vix_poller_task = asyncio.create_task(self._poll_vix_loop())
                
                if not self.position_manager_task:
                    self.position_manager_task = asyncio.create_task(self._manage_positions_loop())
                
                if not self.iv_poller_task:
                    self.iv_poller_task = asyncio.create_task(self._poll_iv_loop())
                
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
                if self.stop_signal: break
                data = json.loads(message)
                await self._handle_message(data)
        except Exception as e:
            logging.error(f"Run loop error: {e}")
            self.connected = False

    async def disconnect(self):
        self.stop_signal = True
        self.is_connected = False
        if self.ws: await self.ws.close()

    async def _handle_message(self, data: dict):
        if data.get('type') == 'trade':
            await self._handle_trade(data)
            if data.get('symbol'): await self._check_signals(data.get('symbol'))
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
        if not symbol or symbol not in self.symbols: return
        
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
            if adx is not None and adx < 20: # Low Trend
                logging.info(f"🚜 FARMING: {symbol} ADX {adx:.1f}. Opening Iron Condor.")
                # FIX: Use 'CREDIT_SPREAD' so Gatekeeper accepts the order
                # Leg 1: Bear Call Spread
                await self._send_proposal(symbol, 'CREDIT_SPREAD', 'OPEN', 'CALL', indicators, 'neutral')
                # Leg 2: Bull Put Spread
                await self._send_proposal(symbol, 'CREDIT_SPREAD', 'OPEN', 'PUT', indicators, 'neutral')
                self.last_proposal_time[symbol] = now
                return

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
            
            if trend == 'UPTREND' and rsi < 30 and flow != 'NEUTRAL':
                signal = 'BULL_PUT_SPREAD'
                strategy = 'CREDIT_SPREAD'
                side = 'OPEN'
                option_type = 'PUT'
                bias = 'bullish'
            elif trend == 'DOWNTREND' and rsi > 70 and flow != 'NEUTRAL':
                signal = 'BEAR_CALL_SPREAD'
                strategy = 'CREDIT_SPREAD'
                side = 'OPEN'
                option_type = 'CALL'
                bias = 'bearish'

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
                    logging.info(f"🦋 BUTTERFLY: {symbol} High IV ({iv_rank:.0f}) in Chop. Targeting Pin.")
                    
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
        except: return []

    async def _get_best_expiration(self, symbol: str) -> Optional[str]:
        # Target: 30 DTE (Sweet Spot)
        exps = await self._get_expirations(symbol)
        if not exps: return None
        
        today = datetime.now().date()
        valid = []
        for e in exps:
            try:
                dte = (datetime.strptime(e, '%Y-%m-%d').date() - today).days
                if 14 <= dte <= 45: valid.append((dte, e))
            except: continue
            
        if not valid: return None
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
        except: return []

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
            
        if not exp_str: return

        # 2. Chain
        chain = await self._get_option_chain(symbol, exp_str)
        if not chain: return

        # Helper: Safely get delta
        def get_delta(o):
            try: return float(o.get('greeks', {}).get('delta', 0))
            except: return 0.0

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
                if candidates: short_leg = candidates[-1]
                
            if short_leg:
                # Long leg: $5 lower
                s_strike = float(short_leg['strike'])
                longs = [o for o in options if float(o['strike']) <= s_strike - 5]
                if longs: long_leg = longs[-1]

        else: # CALL
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
                if candidates: short_leg = candidates[0]
                
            if short_leg:
                # Long leg: $5 higher
                s_strike = float(short_leg['strike'])
                longs = [o for o in options if float(o['strike']) >= s_strike + 5]
                if longs: long_leg = longs[0]

        if not short_leg or not long_leg: return

        # 4. Real Pricing
        short_bid = float(short_leg.get('bid', 0))
        long_ask = float(long_leg.get('ask', 0))
        
        if short_bid == 0 or long_ask == 0: return # No liquidity

        fair_credit = short_bid - long_ask
        limit_price = max(0.05, fair_credit - 0.05) # 5 cent buffer

        # 5. Real Metrics (No Stubs)
        vix = indicators.get('vix') or 0
        if vix < 15: vol_state = 'low'
        elif vix < 25: vol_state = 'normal'
        else: vol_state = 'high'
        
        velocity = indicators.get('volume_velocity', 1.0)
        imbalance_score = min(10, max(0, (velocity - 1.0) * 5))

        # 6. Proposal
        proposal = {
            'symbol': symbol,
            'strategy': strategy,
            'side': side,
            'quantity': 1,
            'price': round(limit_price, 2),
            'legs': [
                {
                    'symbol': short_leg['symbol'],
                    'expiration': exp_str,
                    'strike': float(short_leg['strike']),
                    'type': option_type,
                    'quantity': 1,
                    'side': 'SELL'
                },
                {
                    'symbol': long_leg['symbol'],
                    'expiration': exp_str,
                    'strike': float(long_leg['strike']),
                    'type': option_type,
                    'quantity': 1,
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
                'imbalance_score': round(imbalance_score, 1), # REAL DATA
            }
        }
        
        response = await self.gatekeeper_client.send_proposal(proposal)
        
        # Track approved trades for position management
        if response and response.get('status') == 'APPROVED':
            trade_id = f"{symbol}_{strategy}_{int(datetime.now().timestamp())}"
            self.open_positions[trade_id] = {
                'symbol': symbol,
                'strategy': strategy,
                'legs': proposal['legs'],  # Contains the specific option symbols
                'entry_price': proposal['price'],
                'bias': bias,
                'timestamp': datetime.now(),
                'highest_pnl': -100.0  # NEW: Initialize for Trailing Stop tracking
            }
            logging.info(f"📝 Tracking Trade: {trade_id}")

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
        
        # Construct Context
        context = {
            'vix': indicators.get('vix', 0),
            'flow_state': indicators.get('flow_state', 'UNKNOWN'),
            'iv_rank': self.alpha_engine.get_iv_rank(symbol),
            'strategy_logic': 'Complex Structure'
        }

        proposal = {
            'symbol': symbol,
            'strategy': strategy,
            'side': side,
            'quantity': 1,
            'price': round(limit_price, 2),
            'legs': legs,
            'context': context
        }
        
        response = await self.gatekeeper_client.send_proposal(proposal)
        
        if response and response.get('status') == 'APPROVED':
            trade_id = f"{symbol}_{strategy}_{int(datetime.now().timestamp())}"
            self.open_positions[trade_id] = {
                'symbol': symbol,
                'strategy': strategy,
                'legs': legs,
                'entry_price': round(limit_price, 2),
                'bias': bias,
                'timestamp': datetime.now(),
                'highest_pnl': -100.0
            }
            logging.info(f"📝 Tracking Complex Trade: {trade_id}")
