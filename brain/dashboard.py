"""
Gekko3 Mission Control Dashboard
Live visualization of trading system state using Streamlit
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
    page_title="Gekko3 Mission Control",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for better styling
st.markdown("""
    <style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #1f77b4;
        text-align: center;
        margin-bottom: 2rem;
    }
    .metric-card {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 0.5rem;
        margin: 0.5rem 0;
    }
    .status-online {
        color: #22c55e;
        font-weight: bold;
    }
    .status-offline {
        color: #ef4444;
        font-weight: bold;
    }
    </style>
""", unsafe_allow_html=True)

# Title
st.markdown('<p class="main-header">🧠 Gekko3 Mission Control</p>', unsafe_allow_html=True)

# Sidebar controls
with st.sidebar:
    st.header("⚙️ Controls")
    auto_refresh = st.checkbox("Auto-refresh", value=True)
    refresh_interval = st.slider("Refresh interval (seconds)", 1, 10, 2)
    
    if st.button("🔄 Refresh Now"):
        st.rerun()
    
    st.markdown("---")
    st.header("ℹ️ About")
    st.markdown("""
    **Gekko3 Brain Activity Monitor**
    
    This dashboard shows real-time metrics from the Gekko3 trading system:
    - **RSI (2)**: Hypersensitive RSI for scalping
    - **ADX**: Trend strength indicator
    - **Flow State**: RISK_ON / RISK_OFF / NEUTRAL
    - **VIX**: Volatility index
    - **Trend**: UPTREND / DOWNTREND
    
    Data updates automatically from `brain_state.json`
    """)

# Main content area
state_file = 'brain_state.json'

# Check if state file exists
if not os.path.exists(state_file):
    st.error("⚠️ Waiting for Brain heartbeat...")
    st.info("Make sure `brain/main.py` is running and generating state data.")
    st.stop()

# Load state
try:
    with open(state_file, 'r') as f:
        data = json.load(f)
except json.JSONDecodeError:
    st.error("⚠️ Invalid JSON in state file. Waiting for valid data...")
    st.stop()
except Exception as e:
    st.error(f"⚠️ Error reading state file: {e}")
    st.stop()

# Check if data is empty
if not data:
    st.warning("📭 No symbol data available yet. Waiting for market feed...")
    st.stop()

# Display timestamp
if data and any('timestamp' in data.get(s, {}) for s in data):
    latest_timestamp = max(
        data.get(s, {}).get('timestamp', '') 
        for s in data 
        if 'timestamp' in data.get(s, {})
    )
    if latest_timestamp:
        try:
            ts = datetime.fromisoformat(latest_timestamp)
            time_ago = (datetime.now() - ts).total_seconds()
            if time_ago < 10:
                st.success(f"🟢 Online - Last update: {int(time_ago)}s ago")
            elif time_ago < 60:
                st.warning(f"🟡 Stale - Last update: {int(time_ago)}s ago")
            else:
                st.error(f"🔴 Offline - Last update: {int(time_ago/60):.1f}min ago")
        except:
            pass

# Create metrics for each symbol
for symbol, metrics in data.items():
    st.markdown("---")
    st.header(f"📊 {symbol} - ${metrics.get('price', 0):.2f}")
    
    # Key Metrics Row
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        rsi = metrics.get('rsi', 50)
        rsi_delta = None
        if rsi < 5:
            rsi_delta = "Oversold"
        elif rsi > 95:
            rsi_delta = "Overbought"
        st.metric("RSI (2)", f"{rsi:.1f}", delta=rsi_delta, delta_color="inverse")
    
    with col2:
        adx = metrics.get('adx', 0)
        adx_status = "Weak" if adx < 20 else "Strong" if adx > 25 else "Moderate"
        st.metric("ADX (Trend)", f"{adx:.1f}", delta=adx_status)
    
    with col3:
        flow = metrics.get('flow', 'NEUTRAL')
        flow_color = {
            'RISK_ON': '🟢',
            'RISK_OFF': '🔴',
            'NEUTRAL': '🟡'
        }.get(flow, '⚪')
        st.metric("Flow State", f"{flow_color} {flow}")
    
    with col4:
        vix = metrics.get('vix', 0)
        vix_status = "Low" if vix < 15 else "High" if vix > 25 else "Normal"
        st.metric("VIX", f"{vix:.2f}", delta=vix_status)
    
    with col5:
        trend = metrics.get('trend', 'UNKNOWN')
        trend_emoji = {
            'UPTREND': '📈',
            'DOWNTREND': '📉',
            'UNKNOWN': '➡️'
        }.get(trend, '➡️')
        st.metric("Trend", f"{trend_emoji} {trend}")
    
    # Additional metrics
    col6, col7, col8 = st.columns(3)
    
    with col6:
        velocity = metrics.get('volume_velocity', 1.0)
        st.metric("Volume Velocity", f"{velocity:.2f}x")
    
    with col7:
        candle_count = metrics.get('candle_count', 0)
        is_warm = metrics.get('is_warm', False)
        warm_status = "✅ Ready" if is_warm else f"⏳ {candle_count}/200"
        st.metric("Warmup Status", warm_status)
    
    with col8:
        price = metrics.get('price', 0)
        st.metric("Current Price", f"${price:.2f}")
    
    # Visual Gauge for RSI
    st.subheader("RSI Heatmap")
    rsi_gauge = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=rsi,
        title={'text': f"{symbol} RSI (2)"},
        domain={'x': [0, 1], 'y': [0, 1]},
        gauge={
            'axis': {'range': [0, 100]},
            'bar': {'color': "darkblue"},
            'steps': [
                {'range': [0, 5], 'color': "lightgreen"},      # Oversold (Buy Zone)
                {'range': [5, 30], 'color': "lightblue"},      # Oversold region
                {'range': [30, 70], 'color': "gray"},         # Neutral
                {'range': [70, 95], 'color': "lightcoral"},   # Overbought region
                {'range': [95, 100], 'color': "red"}          # Overbought (Sell Zone)
            ],
            'threshold': {
                'line': {'color': "red", 'width': 4},
                'thickness': 0.75,
                'value': 90
            }
        }
    ))
    rsi_gauge.update_layout(height=300)
    st.plotly_chart(rsi_gauge, use_container_width=True)
    
    # ADX Gauge
    st.subheader("Trend Strength (ADX)")
    adx_gauge = go.Figure(go.Indicator(
        mode="gauge+number",
        value=adx,
        title={'text': f"{symbol} ADX"},
        domain={'x': [0, 1], 'y': [0, 1]},
        gauge={
            'axis': {'range': [0, 50]},
            'bar': {'color': "darkgreen"},
            'steps': [
                {'range': [0, 20], 'color': "lightgray"},   # Weak trend (Iron Condor zone)
                {'range': [20, 25], 'color': "yellow"},    # Moderate
                {'range': [25, 50], 'color': "lightgreen"} # Strong trend
            ],
            'threshold': {
                'line': {'color': "red", 'width': 4},
                'thickness': 0.75,
                'value': 20  # Threshold for Iron Condor strategy
            }
        }
    ))
    adx_gauge.update_layout(height=300)
    st.plotly_chart(adx_gauge, use_container_width=True)

# Footer
st.markdown("---")
st.caption(f"🔄 Auto-updating from Gekko3 Core Engine | Last refresh: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# Auto-refresh logic
if auto_refresh:
    time.sleep(refresh_interval)
    st.rerun()
