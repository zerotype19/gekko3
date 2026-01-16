"""
Gekko3 Mission Control (Pro)
Live visualization of trading system state using Streamlit
Compatible with Phase C Rich State Export
"""

import streamlit as st
import pandas as pd
import json
import time
import os
from datetime import datetime, timedelta
import plotly.graph_objects as go

# Page configuration
st.set_page_config(
    page_title="Gekko3 Pro Terminal",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
    <style>
    .main-header { font-size: 2.5rem; font-weight: bold; color: #1f77b4; text-align: center; margin-bottom: 1rem; }
    .sub-header { font-size: 1.2rem; font-weight: bold; opacity: 0.8; margin-top: 1rem; }
    .metric-container { background-color: #1e293b; padding: 15px; border-radius: 10px; color: white; }
    .regime-tag { padding: 5px 10px; border-radius: 5px; font-weight: bold; color: white; }
    .stale-warning { color: #ff4b4b; font-weight: bold; border: 1px solid #ff4b4b; padding: 10px; border-radius: 5px; text-align: center; margin-bottom: 10px; }
    </style>
""", unsafe_allow_html=True)

# Title
st.markdown('<p class="main-header">🧠 Gekko3 Pro Terminal</p>', unsafe_allow_html=True)

# Sidebar
with st.sidebar:
    st.header("⚙️ Controls")
    auto_refresh = st.checkbox("Auto-refresh", value=True)
    refresh_interval = st.slider("Refresh rate (sec)", 1, 10, 2)
    if st.button("🔄 Refresh Now"):
        st.rerun()
    st.markdown("---")
    st.markdown("**System Status**")
    st.info("Monitoring 'brain_state.json'")

# Load State
state_file = 'brain_state.json'
if not os.path.exists(state_file):
    st.warning("⚠️ Waiting for Brain heartbeat...")
    time.sleep(2)
    st.rerun()

try:
    # Read file with retry logic to handle race conditions
    raw_data = None
    for attempt in range(3):
        try:
            with open(state_file, 'r') as f:
                content = f.read().strip()
                if not content:
                    time.sleep(0.1)
                    continue
                raw_data = json.loads(content)
                break
        except json.JSONDecodeError as e:
            if attempt < 2:
                time.sleep(0.1)
                continue
            else:
                raise
    
    if raw_data is None:
        st.warning("⚠️ State file is empty. Waiting for Brain to write data...")
        time.sleep(2)
        st.rerun()
        
except Exception as e:
    st.error(f"⚠️ Error reading state: {e}")
    time.sleep(2)
    st.rerun()

# Handle New "Rich" Structure
if 'system' in raw_data and 'market' in raw_data:
    system_data = raw_data['system']
    market_data = raw_data['market']
else:
    st.error("❌ Incompatible Data Format. Please restart brain/main.py.")
    st.stop()

# --- FRESHNESS CHECK ---
last_update_str = system_data.get('timestamp')
try:
    last_update = datetime.fromisoformat(last_update_str)
    seconds_ago = (datetime.now() - last_update).total_seconds()
    if seconds_ago > 60:
        st.markdown(f'<div class="stale-warning">⚠️ DATA STALE: Last update {int(seconds_ago)}s ago. Is Brain running?</div>', unsafe_allow_html=True)
except:
    pass

# --- SECTION 1: MISSION CONTROL (System Wide) ---
st.markdown("### 🛡️ Portfolio & Risk")

# 1. System Health Row
sys_col1, sys_col2, sys_col3, sys_col4 = st.columns(4)

with sys_col1:
    regime = system_data.get('regime', 'UNKNOWN')
    regime_color = "gray"
    if regime == 'LOW_VOL_CHOP': regime_color = "#3b82f6" # Blue
    elif regime == 'TRENDING': regime_color = "#22c55e"    # Green
    elif regime == 'HIGH_VOL_EXPANSION': regime_color = "#f97316" # Orange
    elif regime == 'EVENT_RISK': regime_color = "#ef4444"  # Red
    
    st.markdown(f"**Market Regime**")
    st.markdown(f"<div style='background-color:{regime_color}; text-align:center;' class='regime-tag'>{regime}</div>", unsafe_allow_html=True)

with sys_col2:
    risk = system_data.get('portfolio_risk', {})
    delta = risk.get('delta', 0)
    st.metric("Net Delta", f"{delta:+.1f}", delta="Bullish" if delta > 0 else "Bearish")

with sys_col3:
    theta = risk.get('theta', 0)
    st.metric("Net Theta", f"${theta:+.1f}/day", delta="Income")

with sys_col4:
    positions = system_data.get('open_positions', 0)
    total_positions = system_data.get('total_positions', 0)
    st.metric("Open Positions", f"{positions}/{total_positions}")

st.markdown("---")

# --- SECTION: REGIME PIVOT ENGINE (VIX/ADX Metrics) ---
st.markdown("### 🧭 Regime Pivot Engine")

# Get VIX and ADX from market data (use SPY as proxy for market-wide metrics)
spy_data = market_data.get('SPY', {})
vix = spy_data.get('vix', system_data.get('vix', 0))
adx = spy_data.get('adx', 0)  # ADX might be per-symbol, use SPY as market proxy

# Calculate ADX from market data if available
if not adx and 'SPY' in market_data:
    # Try to get ADX from indicators if available
    indicators = market_data['SPY'].get('indicators', {})
    adx = indicators.get('adx', 0)

# Determine Active Strategy Mode based on Regime + VIX + ADX
strategy_mode = "WAITING"
strategy_emoji = "⏳"

if regime == 'LOW_VOL_CHOP':
    if adx and adx < 20:
        strategy_mode = "🚜 FARMER (Iron Condor)"
        strategy_emoji = "🚜"
    else:
        strategy_mode = "⚠️ CHOP (Trend Grinding - No Trade)"
        strategy_emoji = "⚠️"
elif regime == 'TRENDING':
    if vix and vix < 13:
        strategy_mode = "🛡️ SKEW (Ratio Backspread)"
        strategy_emoji = "🛡️"
    else:
        strategy_mode = "📈 TREND (Credit Spread)"
        strategy_emoji = "📈"
elif regime == 'HIGH_VOL_EXPANSION':
    strategy_mode = "🔴 EXPANSION (Stand Down)"
    strategy_emoji = "🔴"
elif regime == 'EVENT_RISK':
    strategy_mode = "🚨 EVENT (Blocked)"
    strategy_emoji = "🚨"

# Check for VOLATILITY BEAST window (low VIX + morning hours)
now_hour = datetime.now().hour
if vix and vix < 15 and 9 <= now_hour <= 10:
    strategy_mode = "🦁 BEAST (Calendar Scan)"
    strategy_emoji = "🦁"

# Display metrics in columns
pivot_col1, pivot_col2, pivot_col3, pivot_col4 = st.columns(4)

with pivot_col1:
    vix_delta = "-Low Vol" if vix and vix < 15 else "Normal" if vix else None
    st.metric("VIX Level", f"{vix:.2f}" if vix else "N/A", delta=vix_delta)

with pivot_col2:
    adx_delta = "Strong" if adx and adx > 25 else "Weak" if adx else None
    st.metric("Trend Strength (ADX)", f"{adx:.1f}" if adx else "N/A", delta=adx_delta)

with pivot_col3:
    st.metric("Market Regime", regime)

with pivot_col4:
    st.metric("Active Strategy Mode", strategy_mode)

st.markdown("---")

# --- POSITIONS TABLE ---
positions_list = system_data.get('positions', [])
if positions_list:
    st.markdown("### 📊 Active Positions")
    
    pos_data = []
    for pos in positions_list:
        pos_data.append({
            'Symbol': pos.get('symbol', 'UNKNOWN'),
            'Strategy': pos.get('strategy', 'UNKNOWN'),
            'Status': pos.get('status', 'UNKNOWN'),
            'Entry Price': f"${pos.get('entry_price', 0):.2f}",
            'Legs': pos.get('legs_count', 0),
            'Bias': pos.get('bias', 'neutral'),
            'Trade ID': pos.get('trade_id', '')[:15] + '...'
        })
    
    if pos_data:
        df = pd.DataFrame(pos_data)
        st.dataframe(df, width=1200, hide_index=True)
else:
    st.info("📭 No active positions tracked by Brain")

st.markdown("---")

# --- SECTION 2: MARKET INTELLIGENCE (Per Symbol) ---
st.markdown("### 📊 Asset Surveillance")

for symbol, metrics in market_data.items():
    # Warm-up Check
    is_warm = metrics.get('is_warm', False)
    candle_count = metrics.get('candle_count', 0)
    
    header_text = f"{symbol} - ${metrics.get('price', 0):.2f} ({metrics.get('trend', 'FLAT')})"
    if not is_warm:
        header_text += f" [❄️ WARMING UP: {candle_count}/200]"
        
    with st.expander(header_text, expanded=True):
        
        # Row 1: Key Signals
        m1, m2, m3, m4 = st.columns(4)
        
        with m1:
            iv_rank = metrics.get('iv_rank', 0)
            st.metric("IV Rank", f"{iv_rank:.0f}%", delta="High" if iv_rank > 50 else "Low")
            
        with m2:
            rsi = metrics.get('rsi', 50)
            st.metric("RSI (14)", f"{rsi:.1f}")
            
        with m3:
            # Volume Profile POC distance
            poc = metrics.get('poc', 0)
            price = metrics.get('price', 0)
            dist_pct = ((price - poc) / poc) * 100 if poc > 0 else 0
            st.metric("Dist to POC", f"{dist_pct:+.2f}%", help=f"POC: ${poc:.2f}")
            
        with m4:
            signal = metrics.get('active_signal')
            if signal:
                st.warning(f"🚨 {signal}")
            else:
                st.info("Scanning...")

        # Row 2: Visuals
        g1, g2 = st.columns(2)
        
        with g1:
            # RSI Gauge
            fig_rsi = go.Figure(go.Indicator(
                mode = "gauge+number",
                value = rsi,
                title = {'text': "RSI Heatmap"},
                gauge = {
                    'axis': {'range': [0, 100]},
                    'bar': {'color': "white"},
                    'steps': [
                        {'range': [0, 30], 'color': "green"},
                        {'range': [30, 70], 'color': "gray"},
                        {'range': [70, 100], 'color': "red"}],
                    'threshold': {'line': {'color': "blue", 'width': 4}, 'thickness': 0.75, 'value': rsi}
                }
            ))
            fig_rsi.update_layout(height=180, margin=dict(l=20,r=20,t=30,b=20))
            st.plotly_chart(fig_rsi, use_container_width=True, key=f"rsi_{symbol}", config={'displayModeBar': False})

        with g2:
            # Market Structure (Price vs POC/Value Area)
            poc = metrics.get('poc', 0)
            vah = metrics.get('vah', 0)
            val = metrics.get('val', 0)
            price = metrics.get('price', 0)
            
            if poc > 0:
                # Normalize range for display
                range_min = min(val, price) * 0.995
                range_max = max(vah, price) * 1.005
                
                fig_struct = go.Figure()
                
                # Value Area Rect
                fig_struct.add_shape(type="rect",
                    x0=val, y0=0, x1=vah, y1=1,
                    fillcolor="rgba(0,0,255,0.1)", line=dict(width=0),
                )
                
                # Lines
                fig_struct.add_trace(go.Scatter(x=[poc, poc], y=[0, 1], mode="lines", name="POC", line=dict(color="blue", width=3, dash="dash")))
                fig_struct.add_trace(go.Scatter(x=[price, price], y=[0, 1], mode="lines", name="Price", line=dict(color="green", width=4)))
                
                fig_struct.update_layout(
                    title="Market Structure (Auction Theory)",
                    height=180,
                    margin=dict(l=20,r=20,t=30,b=20),
                    xaxis=dict(range=[range_min, range_max], title="Price"),
                    yaxis=dict(showticklabels=False, range=[0, 1]),
                    showlegend=True
                )
                st.plotly_chart(fig_struct, use_container_width=True, key=f"struct_{symbol}", config={'displayModeBar': False})
            else:
                st.info("Building Volume Profile...")

# --- SECTION 3: PILOT SCORECARD (Execution Quality) ---
st.markdown("---")
st.markdown("### ✈️ Pilot Scorecard")

# Load Pilot Data
pilot_file = 'pilot_stats.json'
pilot_stats = {
    'total_trades': 0,
    'avg_slippage': 0.0,
    'avg_latency': 0.0,
    'win_rate': 0.0,
    'regime_changes_24h': 0,
    'closed_trades_count': 0
}
pilot_trades = []
pilot_latency_log = []

if os.path.exists(pilot_file):
    try:
        with open(pilot_file, 'r') as f:
            pilot_data = json.load(f)
            # Calculate stats
            trades = pilot_data.get('trades', [])
            regime_changes = pilot_data.get('regime_changes', [])
            
            if trades:
                pilot_stats['total_trades'] = len(trades)
                pilot_stats['avg_slippage'] = sum(t.get('slippage', 0) for t in trades) / len(trades)
                pilot_stats['avg_latency'] = sum(t.get('latency_seconds', 0) for t in trades) / len(trades)
                
                # Win rate (for closed trades with P&L)
                closed_trades_with_pnl = [t for t in trades if t.get('side') == 'CLOSE' and t.get('pnl_pct') is not None]
                pilot_stats['closed_trades_count'] = len(closed_trades_with_pnl)
                if closed_trades_with_pnl:
                    wins = sum(1 for t in closed_trades_with_pnl if t.get('pnl_pct', 0) > 0)
                    pilot_stats['win_rate'] = (wins / len(closed_trades_with_pnl)) * 100
                
                # Get recent trades for charts
                pilot_trades = sorted(trades, key=lambda x: x.get('fill_time', ''), reverse=True)[:100]
                pilot_latency_log = pilot_data.get('latency_log', [])[-100:]
            
            # Count regime changes in last 24 hours
            now = datetime.now()
            pilot_stats['regime_changes_24h'] = sum(
                1 for rc in regime_changes
                if (now - datetime.fromisoformat(rc['timestamp'])).total_seconds() < 86400
            )
    except Exception as e:
        st.warning(f"⚠️ Could not load pilot stats: {e}")

# Key Metrics Row
p1, p2, p3, p4 = st.columns(4)

with p1:
    total_trades = pilot_stats['total_trades']
    st.metric("Trades Executed", f"{total_trades}")

with p2:
    avg_slippage = pilot_stats['avg_slippage']
    slippage_color = "normal" if avg_slippage <= 0.05 else "inverse"
    st.metric("Avg Slippage", f"${avg_slippage:.4f}", help="Price difference between signal and fill", delta=None if avg_slippage <= 0.05 else "⚠️ High")

with p3:
    avg_latency = pilot_stats['avg_latency']
    latency_color = "normal" if avg_latency <= 5.0 else "inverse"
    st.metric("Avg Latency", f"{avg_latency:.3f}s", help="Time from signal to fill", delta=None if avg_latency <= 5.0 else "⚠️ Slow")

with p4:
    regime_changes = pilot_stats['regime_changes_24h']
    st.metric("Regime Changes (24h)", f"{regime_changes}", help="Market regime switches in last 24 hours")

# Charts Row
if pilot_trades:
    c1, c2 = st.columns(2)
    
    with c1:
        # Execution Quality Chart (Slippage Histogram)
        slippage_data = [t.get('slippage_direction', 0) for t in pilot_trades if t.get('slippage_direction') is not None]
        if slippage_data:
            fig_slippage = go.Figure()
            fig_slippage.add_trace(go.Histogram(
                x=slippage_data,
                nbinsx=30,
                name="Slippage",
                marker_color='rgba(59, 130, 246, 0.7)'
            ))
            fig_slippage.add_vline(x=0, line_dash="dash", line_color="green", annotation_text="Perfect Fill")
            fig_slippage.update_layout(
                title="Execution Quality (Slippage Distribution)",
                xaxis_title="Slippage Direction (Positive = Bad, Negative = Good)",
                yaxis_title="Frequency",
                height=300,
                margin=dict(l=20,r=20,t=40,b=20)
            )
            st.plotly_chart(fig_slippage, use_container_width=True, key="slippage_chart", config={'displayModeBar': False})
    
    with c2:
        # Latency Timeline
        if pilot_latency_log:
            latency_data = [{
                'time': datetime.fromisoformat(log['timestamp']),
                'latency': log['latency_seconds']
            } for log in pilot_latency_log if log.get('timestamp') and log.get('latency_seconds') is not None]
            
            if latency_data:
                fig_latency = go.Figure()
                fig_latency.add_trace(go.Scatter(
                    x=[d['time'] for d in latency_data],
                    y=[d['latency'] for d in latency_data],
                    mode='markers+lines',
                    name='Latency',
                    marker=dict(color='rgba(236, 72, 153, 0.7)', size=6),
                    line=dict(color='rgba(236, 72, 153, 0.5)', width=1)
                ))
                fig_latency.add_hline(y=5.0, line_dash="dash", line_color="red", annotation_text="5s Threshold")
                fig_latency.update_layout(
                    title="Latency Timeline",
                    xaxis_title="Time",
                    yaxis_title="Latency (seconds)",
                    height=300,
                    margin=dict(l=20,r=20,t=40,b=20)
                )
                st.plotly_chart(fig_latency, use_container_width=True, key="latency_chart", config={'displayModeBar': False})
        
        # Win Rate (if available)
        if pilot_stats['closed_trades_count'] > 0:
            win_rate = pilot_stats['win_rate']
            win_color = "green" if win_rate >= 50 else "red"
            st.markdown(f"**Win Rate:** <span style='color:{win_color}'>{win_rate:.1f}%</span> ({pilot_stats['closed_trades_count']} closed trades)", unsafe_allow_html=True)
else:
    st.info("📊 No pilot data yet. Trades will populate here automatically.")

# Footer
if auto_refresh:
    time.sleep(refresh_interval)
    st.rerun()
