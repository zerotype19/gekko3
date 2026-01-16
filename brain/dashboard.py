"""
Gekko3 Mission Control (Pro)
Live visualization of trading system state using Streamlit
Compatible with Phase C Rich State Export + Pilot Stats
"""

import streamlit as st
import pandas as pd
import json
import time
import os
from datetime import datetime, timedelta
import plotly.graph_objects as go
import plotly.express as px

# --- CONFIGURATION ---
st.set_page_config(
    page_title="Gekko3 Command",
    page_icon="🦁",
    layout="wide",
    initial_sidebar_state="collapsed" # Focus on the data
)

# --- STYLING ---
st.markdown("""
    <style>
    /* Global Cleanups */
    .block-container { padding-top: 2rem; padding-bottom: 2rem; }
    
    /* Metrics */
    div[data-testid="stMetric"] {
        background-color: #1e293b;
        border: 1px solid #334155;
        padding: 15px;
        border-radius: 8px;
        color: white;
    }
    div[data-testid="stMetricLabel"] { color: #94a3b8 !important; }
    div[data-testid="stMetricValue"] { color: #f8fafc !important; font-weight: 700; }
    
    /* Regime Badges */
    .regime-box {
        padding: 20px;
        border-radius: 8px;
        text-align: center;
        margin-bottom: 20px;
        border: 1px solid rgba(255,255,255,0.1);
    }
    .regime-title { font-size: 0.9rem; text-transform: uppercase; letter-spacing: 1px; opacity: 0.8; }
    .regime-value { font-size: 2.5rem; font-weight: 900; margin: 10px 0; }
    .regime-desc { font-size: 1.1rem; font-style: italic; opacity: 0.9; }
    
    /* Tables */
    .stDataFrame { border: 1px solid #334155; border-radius: 8px; }
    
    /* Status Indicators */
    .status-ok { color: #4ade80; font-weight: bold; }
    .status-warn { color: #facc15; font-weight: bold; }
    .status-err { color: #ef4444; font-weight: bold; }
    </style>
""", unsafe_allow_html=True)

# --- DATA LOADING ---
@st.cache_data(ttl=1) # Cache for 1 second to prevent file lock contention
def load_data():
    state = {}
    pilot = {}
    
    # Load Brain State
    if os.path.exists('brain_state.json'):
        try:
            with open('brain_state.json', 'r') as f:
                state = json.load(f)
        except: pass
        
    # Load Pilot Stats
    if os.path.exists('pilot_stats.json'):
        try:
            with open('pilot_stats.json', 'r') as f:
                pilot = json.load(f)
        except: pass
        
    return state, pilot

raw_state, raw_pilot = load_data()

# --- SIDEBAR ---
with st.sidebar:
    st.title("⚙️ Controls")
    auto_refresh = st.toggle("Auto-Refresh (2s)", value=True)
    if st.button("Manual Refresh"):
        st.rerun()
    st.divider()
    st.caption(f"Last UI Update: {datetime.now().strftime('%H:%M:%S')}")
    st.caption("Gekko3 Pivot Engine v3.1")

# --- DATA PRE-PROCESSING ---
if not raw_state:
    st.warning("⚠️ Waiting for Brain Connection...")
    time.sleep(2)
    st.rerun()

system = raw_state.get('system', {})
market = raw_state.get('market', {})

# Time freshness check
last_update = datetime.fromisoformat(system.get('timestamp', datetime.now().isoformat()))
latency = (datetime.now() - last_update).total_seconds()
status_color = "#4ade80" if latency < 60 else "#ef4444"
status_text = "ONLINE" if latency < 60 else "STALE/OFFLINE"

# --- HUD (Heads Up Display) ---

# 1. Regime Banner
regime = system.get('regime', 'UNKNOWN')
vix = market.get('SPY', {}).get('vix', 0)
adx = market.get('SPY', {}).get('adx', 0)

# Determine Logic/Color
bg_color = "#1e293b" # Default
emoji = "❓"
desc = "Analyzing..."

if regime == 'COMPRESSED':
    bg_color = "linear-gradient(90deg, #4f46e5 0%, #7c3aed 100%)" # Indigo/Purple
    emoji = "🦁"
    desc = "Volatility Beast (Buying Calendars)"
elif regime == 'LOW_VOL_CHOP':
    bg_color = "linear-gradient(90deg, #0ea5e9 0%, #3b82f6 100%)" # Blue
    emoji = "🚜"
    desc = "Range Farmer (Iron Condors)"
elif regime == 'TRENDING':
    bg_color = "linear-gradient(90deg, #16a34a 0%, #22c55e 100%)" # Green
    if vix < 13:
        emoji = "🛡️"
        desc = "Trend Engine (Ratio Skew)"
    else:
        emoji = "📈"
        desc = "Trend Engine (Credit Spreads)"
elif regime == 'HIGH_VOL_EXPANSION':
    bg_color = "linear-gradient(90deg, #ea580c 0%, #f97316 100%)" # Orange
    emoji = "🏰"
    desc = "Defense Mode (Hedging Only)"
elif regime == 'EVENT_RISK':
    bg_color = "linear-gradient(90deg, #dc2626 0%, #ef4444 100%)" # Red
    emoji = "🚨"
    desc = "Event Risk (No New Entries)"

st.markdown(f"""
    <div class="regime-box" style="background: {bg_color};">
        <div class="regime-title">Active Market Regime</div>
        <div class="regime-value">{emoji} {regime}</div>
        <div class="regime-desc">{desc}</div>
        <div style="font-size: 0.8rem; margin-top: 10px; opacity: 0.7;">
            VIX: {vix:.2f} | ADX: {adx:.1f} | System Status: <span style="color:{'white' if latency < 60 else '#ffcccc'}">{status_text} ({latency:.0f}s ago)</span>
        </div>
    </div>
""", unsafe_allow_html=True)

# 2. Key Metrics Grid
k1, k2, k3, k4 = st.columns(4)

greeks = system.get('portfolio_risk', {})
pos_count = system.get('open_positions', 0)
total_count = system.get('total_positions', 0)

with k1:
    st.metric("Net Delta", f"{greeks.get('delta', 0):.2f}", help="Directional Exposure")
with k2:
    st.metric("Net Theta", f"${greeks.get('theta', 0):.2f}", help="Daily Time Decay Income")
with k3:
    st.metric("Net Vega", f"{greeks.get('vega', 0):.2f}", help="Volatility Exposure")
with k4:
    st.metric("Active Positions", f"{pos_count} / {total_count}", help="Current / Total Tracked")

# --- MAIN CONTENT ---

col_main, col_side = st.columns([2, 1])

with col_main:
    # --- POSITION TRACKER ---
    st.subheader("📋 Active Positions")
    positions = system.get('positions', [])
    
    if positions:
        # Flatten for display
        df_pos = pd.DataFrame(positions)
        
        # Color coding for P&L (if we had live P&L streaming, adding placeholders)
        # Brain doesn't stream live P&L yet, but we have entry prices.
        # We can calculate estimated P&L using current market data if available.
        
        # Define columns we want to display (with defaults for missing columns)
        display_cols_map = {
            'symbol': 'Symbol',
            'strategy': 'Strategy',
            'status': 'Status',
            'entry_price': 'Entry',
            'legs_count': 'Legs',
            'timestamp': 'Time'
        }
        
        # Build display DataFrame with only available columns
        display_data = {}
        for orig_col, display_name in display_cols_map.items():
            if orig_col in df_pos.columns:
                display_data[display_name] = df_pos[orig_col]
            else:
                # Missing column - add with None/NaN
                display_data[display_name] = None
        
        # Create new DataFrame with renamed columns
        df_display = pd.DataFrame(display_data)
        
        # Format Time if it exists
        if 'Time' in df_display.columns and df_display['Time'].notna().any():
            df_display['Time'] = pd.to_datetime(df_display['Time'], errors='coerce').dt.strftime('%H:%M:%S')
        
        # Build column config (only for columns that exist and have data)
        column_config = {}
        if 'Entry' in df_display.columns and df_display['Entry'].notna().any():
            column_config['Entry'] = st.column_config.NumberColumn(format="$%.2f")
        
        st.dataframe(
            df_display, 
            width='stretch',
            hide_index=True,
            column_config=column_config if column_config else None
        )
    else:
        st.info("📭 No active positions. Waiting for signals...")

    st.markdown("---")
    
    # --- MARKET SCANNER ---
    st.subheader("📡 Market Scanner")
    
    m_cols = st.columns(4)
    symbols = ['SPY', 'QQQ', 'IWM', 'DIA']
    
    for i, sym in enumerate(symbols):
        with m_cols[i]:
            data = market.get(sym, {})
            price = data.get('price', 0)
            trend = data.get('trend', 'FLAT')
            flow = data.get('flow', 'NEUTRAL')
            
            # Card Style
            card_bg = "#334155"
            trend_icon = "➡️"
            if trend == 'UPTREND': 
                trend_icon = "↗️"
                card_bg = "rgba(34, 197, 94, 0.1)"
            elif trend == 'DOWNTREND': 
                trend_icon = "↘️"
                card_bg = "rgba(239, 68, 68, 0.1)"
            
            st.markdown(f"""
                <div style="background-color: {card_bg}; padding: 15px; border-radius: 8px; border: 1px solid #475569; text-align: center;">
                    <div style="font-size: 1.2rem; font-weight: bold;">{sym}</div>
                    <div style="font-size: 1.5rem; font-weight: 900;">${price:.2f}</div>
                    <div style="margin-top: 10px; font-size: 0.9rem;">
                        <div>{trend_icon} {trend}</div>
                        <div style="color: #94a3b8;">Flow: {flow}</div>
                        <div style="color: #94a3b8; font-size: 0.8rem; margin-top:5px;">RSI: {data.get('rsi', 0):.1f}</div>
                    </div>
                </div>
            """, unsafe_allow_html=True)

with col_side:
    # --- PERFORMANCE ---
    st.subheader("📈 Pilot Performance")
    
    # Calculate cumulative P&L from closed trades
    trades = raw_pilot.get('trades', [])
    closed_trades = [t for t in trades if t.get('side') == 'CLOSE']
    
    if closed_trades:
        df_perf = pd.DataFrame(closed_trades)
        df_perf['pnl_dollars'] = df_perf['pnl_dollars'].fillna(0)
        df_perf['fill_time'] = pd.to_datetime(df_perf['fill_time'])
        df_perf = df_perf.sort_values('fill_time')
        df_perf['cumulative_pnl'] = df_perf['pnl_dollars'].cumsum()
        
        # KPI Cards
        total_pnl = df_perf['pnl_dollars'].sum()
        win_rate = (len(df_perf[df_perf['pnl_dollars'] > 0]) / len(df_perf) * 100) if len(df_perf) > 0 else 0
        
        c1, c2 = st.columns(2)
        c1.metric("Total P&L", f"${total_pnl:.2f}", delta=f"{len(df_perf)} Trades")
        c2.metric("Win Rate", f"{win_rate:.1f}%")
        
        # Mini Chart
        fig = px.area(df_perf, x='fill_time', y='cumulative_pnl', title=None)
        fig.update_layout(
            height=200, 
            margin=dict(l=0,r=0,t=10,b=0),
            xaxis_title=None,
            yaxis_title=None,
            showlegend=False,
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color='white')
        )
        # Line color based on P&L
        line_color = '#4ade80' if total_pnl >= 0 else '#ef4444'
        fig.update_traces(line_color=line_color, fillcolor=f"rgba{tuple(int(line_color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)) + (0.1,)}")
        st.plotly_chart(fig, config={'displayModeBar': False})
        
    else:
        st.info("No closed trades yet.")
        st.metric("Total P&L", "$0.00")

    # --- EXECUTION QUALITY ---
    st.subheader("⚡ Execution Stats")
    
    if trades:
        avg_slip = sum(abs(t.get('slippage', 0)) for t in trades) / len(trades)
        avg_lat = sum(t.get('latency_seconds', 0) for t in trades) / len(trades)
        
        e1, e2 = st.columns(2)
        e1.metric("Avg Slippage", f"{avg_slip:.3f}%")
        e2.metric("Avg Latency", f"{avg_lat:.2f}s")
    else:
        st.caption("Waiting for trade data...")

# --- AUTO REFRESH LOGIC ---
if auto_refresh:
    time.sleep(2)
    st.rerun()
