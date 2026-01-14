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
from datetime import datetime
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
    with open(state_file, 'r') as f:
        raw_data = json.load(f)
except Exception as e:
    st.error(f"Error reading state: {e}")
    st.stop()

# Handle New "Rich" Structure vs Old "Flat" Structure
if 'system' in raw_data and 'market' in raw_data:
    system_data = raw_data['system']
    market_data = raw_data['market']
else:
    st.error("❌ Incompatible Data Format. Please restart brain/main.py to generate new state.")
    st.stop()

# --- SECTION 1: MISSION CONTROL (System Wide) ---
st.markdown("### 🛡️ Portfolio & Risk")

# 1. System Health Row
sys_col1, sys_col2, sys_col3, sys_col4 = st.columns(4)

with sys_col1:
    regime = system_data.get('regime', 'UNKNOWN')
    regime_color = "gray"
    if regime == 'LOW_VOL_CHOP': regime_color = "blue"
    elif regime == 'TRENDING': regime_color = "green"
    elif regime == 'HIGH_VOL_EXPANSION': regime_color = "orange"
    elif regime == 'EVENT_RISK': regime_color = "red"
    
    st.markdown(f"**Market Regime**")
    st.markdown(f"<div style='background-color:{regime_color};' class='regime-tag'>{regime}</div>", unsafe_allow_html=True)

with sys_col2:
    risk = system_data.get('portfolio_risk', {})
    delta = risk.get('delta', 0)
    st.metric("Net Delta", f"{delta:+.1f}", delta="Bullish" if delta > 0 else "Bearish")

with sys_col3:
    theta = risk.get('theta', 0)
    st.metric("Net Theta", f"${theta:+.1f}/day", delta="Income")

with sys_col4:
    positions = system_data.get('open_positions', 0)
    st.metric("Open Positions", str(positions))

st.markdown("---")

# --- SECTION 2: MARKET INTELLIGENCE (Per Symbol) ---
st.markdown("### 📊 Asset Surveillance")

for symbol, metrics in market_data.items():
    with st.expander(f"{symbol} - ${metrics.get('price', 0):.2f} ({metrics.get('trend', 'FLAT')})", expanded=True):
        
        # Row 1: Key Signals
        m1, m2, m3, m4 = st.columns(4)
        
        with m1:
            iv_rank = metrics.get('iv_rank', 0)
            st.metric("IV Rank", f"{iv_rank:.0f}%", delta="Expensive" if iv_rank > 50 else "Cheap", delta_color="inverse")
            
        with m2:
            rsi = metrics.get('rsi', 50)
            st.metric("RSI (2)", f"{rsi:.1f}")
            
        with m3:
            adx = metrics.get('adx', 0)
            st.metric("ADX (Trend)", f"{adx:.1f}")
            
        with m4:
            signal = metrics.get('active_signal')
            if signal:
                st.warning(f"🚨 SIGNAL: {signal}")
            else:
                st.info("Scanning...")

        # Row 2: Visuals
        g1, g2 = st.columns(2)
        
        with g1:
            # RSI Gauge
            fig_rsi = go.Figure(go.Indicator(
                mode = "gauge+number",
                value = rsi,
                title = {'text': f"{symbol} RSI Heatmap"},
                gauge = {
                    'axis': {'range': [0, 100]},
                    'bar': {'color': "white"},
                    'steps': [
                        {'range': [0, 10], 'color': "green"},
                        {'range': [10, 90], 'color': "gray"},
                        {'range': [90, 100], 'color': "red"}],
                }
            ))
            fig_rsi.update_layout(height=200, margin=dict(l=20,r=20,t=30,b=20))
            st.plotly_chart(fig_rsi, use_container_width=True, key=f"rsi_gauge_{symbol}")

        with g2:
            # IV Rank Bar
            st.markdown(f"**Volatility Context (IV Rank: {iv_rank:.0f})**")
            st.progress(int(min(iv_rank, 100)))
            if iv_rank < 20:
                st.caption("Environment: BUY PREMIUM (Long Spreads / Calendars)")
            elif iv_rank > 50:
                st.caption("Environment: SELL PREMIUM (Iron Condors / Credit Spreads)")
            else:
                st.caption("Environment: NEUTRAL")

# Footer
if auto_refresh:
    time.sleep(refresh_interval)
    st.rerun()
