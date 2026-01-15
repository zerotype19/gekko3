#!/usr/bin/env python3
"""
System Verification Script
Checks all components of the Gekko3 trading system
"""

import json
import os
import sys
import requests
from datetime import datetime
from pathlib import Path

def check_brain_state():
    """Check Brain state file"""
    print("=" * 60)
    print("1. BRAIN STATE FILE CHECK")
    print("=" * 60)
    
    state_file = Path('brain_state.json')
    if not state_file.exists():
        print("❌ brain_state.json NOT FOUND")
        return False
    
    try:
        with open(state_file, 'r') as f:
            state = json.load(f)
        
        sys_data = state.get('system', {})
        market_data = state.get('market', {})
        
        print(f"✅ State file exists")
        print(f"   Status: {sys_data.get('status', 'UNKNOWN')}")
        print(f"   Regime: {sys_data.get('regime', 'UNKNOWN')}")
        print(f"   Open Positions: {sys_data.get('open_positions', 0)}")
        print(f"   Total Positions: {sys_data.get('total_positions', 0)}")
        print(f"   Timestamp: {sys_data.get('timestamp', 'UNKNOWN')}")
        print(f"   Market Data: {len(market_data)} symbols")
        
        # Check timestamp freshness
        timestamp_str = sys_data.get('timestamp', '')
        if timestamp_str:
            try:
                ts = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                age = (datetime.now() - ts.replace(tzinfo=None)).total_seconds()
                if age < 120:
                    print(f"   ✅ State is fresh ({int(age)}s old)")
                else:
                    print(f"   ⚠️ State is stale ({int(age)}s old)")
            except:
                pass
        
        # Check positions
        positions = sys_data.get('positions', [])
        if positions:
            print(f"\n   Tracked Positions ({len(positions)}):")
            for p in positions:
                print(f"     - {p.get('symbol')} {p.get('strategy')} ({p.get('status', 'UNKNOWN')})")
        
        return True
    except Exception as e:
        print(f"❌ Error reading state file: {e}")
        return False

def check_positions_file():
    """Check positions file"""
    print("\n" + "=" * 60)
    print("2. POSITIONS FILE CHECK")
    print("=" * 60)
    
    pos_file = Path('brain_positions.json')
    if not pos_file.exists():
        print("❌ brain_positions.json NOT FOUND")
        return False
    
    try:
        with open(pos_file, 'r') as f:
            positions = json.load(f)
        
        print(f"✅ Positions file exists")
        print(f"   Total positions on disk: {len(positions)}")
        
        for tid, pos in positions.items():
            status = pos.get('status', 'OPEN')
            symbol = pos.get('symbol', 'UNKNOWN')
            strategy = pos.get('strategy', 'UNKNOWN')
            print(f"     - {tid[:30]}...: {symbol} {strategy} ({status})")
        
        return True
    except Exception as e:
        print(f"❌ Error reading positions file: {e}")
        return False

def check_cloudflare_gatekeeper():
    """Check Cloudflare Gatekeeper"""
    print("\n" + "=" * 60)
    print("3. CLOUDFLARE GATEKEEPER CHECK")
    print("=" * 60)
    
    try:
        url = "https://gekko3-core.kevin-mcgovern.workers.dev/v1/status"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Gatekeeper is reachable")
            print(f"   Status: {data.get('status', 'UNKNOWN')}")
            print(f"   Positions Count: {data.get('positionsCount', 0)}")
            print(f"   Equity: ${data.get('equity', 0):,.2f}")
            print(f"   Daily P&L: {data.get('dailyPnL', 0) * 100:.2f}%")
            
            last_heartbeat = data.get('lastHeartbeat', 0)
            if last_heartbeat > 0:
                age = (datetime.now().timestamp() * 1000 - last_heartbeat) / 1000
                if age < 120:
                    print(f"   ✅ Heartbeat is fresh ({int(age)}s ago)")
                else:
                    print(f"   ⚠️ Heartbeat is stale ({int(age)}s ago)")
            else:
                print(f"   ⚠️ No heartbeat received (Brain may not be running)")
            
            brain_state = data.get('brainState')
            if brain_state:
                print(f"   ✅ Brain state received")
                print(f"      Regime: {brain_state.get('regime', 'UNKNOWN')}")
                print(f"      Market Data: {len(brain_state.get('market', {}))} symbols")
            else:
                print(f"   ⚠️ No brain state in response")
            
            return True
        else:
            print(f"❌ Gatekeeper returned status {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error connecting to Gatekeeper: {e}")
        return False

def check_streamlit_dashboard():
    """Check Streamlit dashboard"""
    print("\n" + "=" * 60)
    print("4. STREAMLIT DASHBOARD CHECK")
    print("=" * 60)
    
    try:
        response = requests.get("http://localhost:8502", timeout=5)
        if response.status_code == 200:
            print("✅ Dashboard is running on http://localhost:8502")
            return True
        else:
            print(f"⚠️ Dashboard returned status {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Dashboard not accessible: {e}")
        print("   Make sure Streamlit is running: python3 -m streamlit run brain/dashboard.py")
        return False

def check_sync_configuration():
    """Check sync configuration"""
    print("\n" + "=" * 60)
    print("5. SYNC CONFIGURATION CHECK")
    print("=" * 60)
    
    # Check if sync method exists in code
    market_feed_file = Path('brain/src/market_feed.py')
    if not market_feed_file.exists():
        print("❌ market_feed.py not found")
        return False
    
    with open(market_feed_file, 'r') as f:
        content = f.read()
    
    checks = [
        ('sync_positions_with_tradier', 'Full sync method exists'),
        ('last_sync', 'Sync timer variable exists'),
        ('600', '10-minute sync interval configured'),
        ('PERIODIC SYNC', 'Sync logging present'),
    ]
    
    all_good = True
    for check, desc in checks:
        if check in content:
            print(f"   ✅ {desc}")
        else:
            print(f"   ❌ {desc} - NOT FOUND")
            all_good = False
    
    return all_good

def main():
    print("\n" + "=" * 60)
    print("GEKKO3 SYSTEM VERIFICATION")
    print("=" * 60)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    results = {
        'brain_state': check_brain_state(),
        'positions_file': check_positions_file(),
        'cloudflare': check_cloudflare_gatekeeper(),
        'dashboard': check_streamlit_dashboard(),
        'sync_config': check_sync_configuration(),
    }
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    all_passed = all(results.values())
    
    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name.replace('_', ' ').title()}")
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ ALL SYSTEMS OPERATIONAL")
        print("\nSystem is ready to run for several hours.")
        print("Monitor logs for:")
        print("  - Position sync every 10 minutes: '🔄 PERIODIC SYNC'")
        print("  - Heartbeat every 60 seconds: '💓 Heartbeat sent'")
        print("  - Position monitoring: '📊 MONITORING X open positions'")
    else:
        print("⚠️ SOME SYSTEMS NEED ATTENTION")
        print("\nPlease review the failures above and restart components as needed.")
    print("=" * 60 + "\n")
    
    return 0 if all_passed else 1

if __name__ == '__main__':
    sys.exit(main())
