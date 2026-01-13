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
        symbols: list = None
    ):
        self.alpha_engine = alpha_engine
        self.gatekeeper_client = gatekeeper_client
        self.symbols = symbols or ['SPY', 'QQQ']
        
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
        
        self.notifier = get_notifier()

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

        indicators = self.alpha_engine.get_indicators(symbol)
        
        # Signal Setup
        signal = None
        strategy = None
        side = None
        option_type = None
        bias = None
        
        current_hour = now.hour
        current_minute = now.minute
        
        # 1. ORB (Opening Range Breakout) - 10:00-11:30
        is_orb_window = (current_hour == 10) or (current_hour == 11 and current_minute < 30)
        
        if is_orb_window and indicators.get('candle_count', 0) >= 30:
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

        # 2. Range Farmer (Iron Condor) - 1:00 PM
        if not signal and current_hour == 13 and 0 <= current_minute < 5:
            adx = self.alpha_engine.get_adx(symbol)
            if adx < 20: # Low Trend
                logging.info(f"🚜 FARMING: {symbol} ADX {adx:.1f}. Opening Iron Condor.")
                await self._send_proposal(symbol, 'IRON_CONDOR_LEG', 'OPEN', 'CALL', indicators, 'neutral')
                await self._send_proposal(symbol, 'IRON_CONDOR_LEG', 'OPEN', 'PUT', indicators, 'neutral')
                self.last_proposal_time[symbol] = now
                return

        # 3. Scalper (0DTE) - All Day
        if not signal:
            rsi_2 = self.alpha_engine.get_rsi(symbol, period=2)
            if rsi_2 < 5 or rsi_2 > 95:
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
                        logging.info(f"⚡ SCALP: {symbol} RSI(2) {rsi_2:.1f}. 0DTE {option_type}.")
                        await self._send_proposal(symbol, strategy, side, option_type, indicators, bias, force_expiration=zero_dte)
                        self.last_signals[symbol] = {'signal': signal, 'timestamp': now}
                        return

        # 4. Trend Strategy (The Core) - After Warmup
        if not signal:
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

        if signal:
            last = self.last_signals.get(symbol, {})
            if last.get('signal') == signal and (now - last.get('timestamp')).seconds < 300:
                return

            logging.info(f"🎯 SIGNAL: {signal} on {symbol}")
            await self._send_proposal(symbol, strategy, side, option_type, indicators, bias)
            
            self.last_proposal_time[symbol] = now
            self.last_signals[symbol] = {'signal': signal, 'timestamp': now}

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
        
        await self.gatekeeper_client.send_proposal(proposal)
