"""
Pilot Recorder
Structured data capture for "Skin in the Game" pilot tracking
Captures execution quality, latency, slippage, and system health metrics
"""

import json
import os
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from threading import Lock

class PilotRecorder:
    """Records structured pilot data for performance analysis"""
    
    def __init__(self, stats_file: str = 'pilot_stats.json'):
        """
        Initialize the Pilot Recorder
        
        Args:
            stats_file: Path to JSON file for storing pilot statistics
        """
        # Determine file path (relative to brain/ or project root)
        current_dir = os.getcwd()
        if current_dir.endswith('brain'):
            self.stats_file = os.path.join(os.path.dirname(current_dir), stats_file)
        else:
            self.stats_file = stats_file
        
        # Thread lock for atomic file writes
        self.lock = Lock()
        
        # Load existing data or initialize
        self._load_data()
        
        logging.info("✅ Pilot Recorder initialized")
    
    def _load_data(self) -> None:
        """Load existing pilot statistics from disk"""
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, 'r') as f:
                    self.data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logging.warning(f"⚠️ Failed to load pilot stats: {e}. Initializing new file.")
                self.data = self._init_data_structure()
        else:
            self.data = self._init_data_structure()
            self._save_data()
    
    def _init_data_structure(self) -> Dict[str, List]:
        """Initialize the data structure"""
        return {
            'trades': [],
            'regime_changes': [],
            'latency_log': [],
            'errors': []
        }
    
    def _save_data(self) -> None:
        """Atomically save data to disk"""
        with self.lock:
            try:
                # Write to temp file first, then rename (atomic on most filesystems)
                temp_file = self.stats_file + '.tmp'
                with open(temp_file, 'w') as f:
                    json.dump(self.data, f, indent=2, default=str)
                os.replace(temp_file, self.stats_file)
            except Exception as e:
                logging.error(f"❌ Failed to save pilot stats: {e}")
    
    def record_trade(
        self,
        symbol: str,
        strategy: str,
        side: str,
        signal_price: float,
        fill_price: float,
        signal_time: datetime,
        fill_time: datetime,
        entry_price: Optional[float] = None,
        exit_price: Optional[float] = None,
        pnl_pct: Optional[float] = None,
        pnl_dollars: Optional[float] = None,
        trade_id: Optional[str] = None
    ) -> None:
        """
        Record a completed trade with execution metrics
        
        Args:
            symbol: Trading symbol (e.g., 'SPY')
            strategy: Strategy name (e.g., 'CREDIT_SPREAD')
            side: 'OPEN' or 'CLOSE'
            signal_price: Price at signal generation (limit price)
            fill_price: Actual fill price from broker
            signal_time: Timestamp when signal was generated
            fill_time: Timestamp when order was filled
            entry_price: Entry price for the position (for CLOSE trades)
            exit_price: Exit price for the position (for CLOSE trades)
            pnl_pct: Realized P&L percentage (for CLOSE trades)
            pnl_dollars: Realized P&L in dollars (for CLOSE trades)
            trade_id: Unique trade identifier
        """
        try:
            # Calculate metrics
            latency_seconds = (fill_time - signal_time).total_seconds()
            slippage = abs(fill_price - signal_price)
            
            # Determine slippage direction (positive = bad, negative = good)
            if side == 'OPEN':
                # For opening: If fill_price > signal_price, we paid more (bad slippage)
                slippage_direction = fill_price - signal_price
            else:
                # For closing: If fill_price < signal_price, we sold for less (bad slippage)
                slippage_direction = signal_price - fill_price
            
            trade_record = {
                'trade_id': trade_id or f"{symbol}_{strategy}_{int(signal_time.timestamp())}",
                'symbol': symbol,
                'strategy': strategy,
                'side': side,
                'signal_price': round(signal_price, 4),
                'fill_price': round(fill_price, 4),
                'slippage': round(slippage, 4),
                'slippage_direction': round(slippage_direction, 4),  # Positive = bad, negative = good
                'latency_seconds': round(latency_seconds, 3),
                'signal_time': signal_time.isoformat(),
                'fill_time': fill_time.isoformat(),
                'entry_price': round(entry_price, 4) if entry_price is not None else None,
                'exit_price': round(exit_price, 4) if exit_price is not None else None,
                'pnl_pct': round(pnl_pct, 2) if pnl_pct is not None else None,
                'pnl_dollars': round(pnl_dollars, 2) if pnl_dollars is not None else None,
                'recorded_at': datetime.now().isoformat()
            }
            
            self.data['trades'].append(trade_record)
            
            # Also log to latency_log for timeline analysis
            self.data['latency_log'].append({
                'timestamp': fill_time.isoformat(),
                'latency_seconds': round(latency_seconds, 3),
                'side': side,
                'symbol': symbol
            })
            
            self._save_data()
            
            logging.info(f"📊 Pilot: Recorded {side} trade for {symbol} {strategy} | "
                        f"Slippage: ${slippage:.4f} | Latency: {latency_seconds:.3f}s")
            
        except Exception as e:
            logging.error(f"❌ Failed to record trade: {e}")
    
    def record_regime_change(self, old_regime: str, new_regime: str) -> None:
        """
        Record a market regime change
        
        Args:
            old_regime: Previous regime (e.g., 'TRENDING')
            new_regime: New regime (e.g., 'LOW_VOL_CHOP')
        """
        try:
            change_record = {
                'timestamp': datetime.now().isoformat(),
                'old_regime': old_regime,
                'new_regime': new_regime
            }
            
            self.data['regime_changes'].append(change_record)
            self._save_data()
            
            logging.info(f"📊 Pilot: Regime change {old_regime} -> {new_regime}")
            
        except Exception as e:
            logging.error(f"❌ Failed to record regime change: {e}")
    
    def record_error(self, source: str, message: str, error_type: Optional[str] = None) -> None:
        """
        Record a system error
        
        Args:
            source: Source of the error (e.g., 'MarketFeed', 'Gatekeeper')
            message: Error message
            error_type: Optional error type classification
        """
        try:
            error_record = {
                'timestamp': datetime.now().isoformat(),
                'source': source,
                'message': message,
                'error_type': error_type
            }
            
            self.data['errors'].append(error_record)
            self._save_data()
            
            logging.warning(f"📊 Pilot: Error recorded from {source}: {message}")
            
        except Exception as e:
            logging.error(f"❌ Failed to record error: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Calculate and return aggregated statistics
        
        Returns:
            Dictionary with calculated metrics
        """
        trades = self.data.get('trades', [])
        regime_changes = self.data.get('regime_changes', [])
        
        if not trades:
            return {
                'total_trades': 0,
                'avg_slippage': 0.0,
                'avg_latency': 0.0,
                'win_rate': 0.0,
                'total_pnl_dollars': 0.0,
                'regime_changes_24h': 0,
                'trades_by_strategy': {},
                'trades_by_side': {}
            }
        
        # Calculate averages
        total_trades = len(trades)
        avg_slippage = sum(t.get('slippage', 0) for t in trades) / total_trades
        avg_latency = sum(t.get('latency_seconds', 0) for t in trades) / total_trades
        
        # Calculate win rate (for closed trades with P&L data)
        closed_trades_with_pnl = [t for t in trades if t.get('side') == 'CLOSE' and t.get('pnl_pct') is not None]
        if closed_trades_with_pnl:
            wins = sum(1 for t in closed_trades_with_pnl if t.get('pnl_pct', 0) > 0)
            win_rate = (wins / len(closed_trades_with_pnl)) * 100
            total_pnl_dollars = sum(t.get('pnl_dollars', 0) for t in closed_trades_with_pnl)
        else:
            win_rate = 0.0
            total_pnl_dollars = 0.0
        
        # Count regime changes in last 24 hours
        now = datetime.now()
        regime_changes_24h = sum(
            1 for rc in regime_changes
            if (now - datetime.fromisoformat(rc['timestamp'])).total_seconds() < 86400
        )
        
        # Trades by strategy
        trades_by_strategy = {}
        for trade in trades:
            strategy = trade.get('strategy', 'UNKNOWN')
            trades_by_strategy[strategy] = trades_by_strategy.get(strategy, 0) + 1
        
        # Trades by side
        trades_by_side = {}
        for trade in trades:
            side = trade.get('side', 'UNKNOWN')
            trades_by_side[side] = trades_by_side.get(side, 0) + 1
        
        return {
            'total_trades': total_trades,
            'avg_slippage': round(avg_slippage, 4),
            'avg_latency': round(avg_latency, 3),
            'win_rate': round(win_rate, 2),
            'total_pnl_dollars': round(total_pnl_dollars, 2),
            'regime_changes_24h': regime_changes_24h,
            'trades_by_strategy': trades_by_strategy,
            'trades_by_side': trades_by_side,
            'closed_trades_count': len(closed_trades_with_pnl)
        }
    
    def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get most recent trades
        
        Args:
            limit: Maximum number of trades to return
            
        Returns:
            List of trade records, most recent first
        """
        trades = self.data.get('trades', [])
        # Sort by fill_time (most recent first)
        sorted_trades = sorted(trades, key=lambda x: x.get('fill_time', ''), reverse=True)
        return sorted_trades[:limit]
