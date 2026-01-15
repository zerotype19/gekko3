"""
Position Recovery Script
Fetches open positions from Tradier and converts them to Brain format.
Run this once to recover positions after a Brain restart.
"""
import os
import json
import requests
import logging
import re
from datetime import datetime
from dotenv import load_dotenv

# Load env for tokens
load_dotenv()

# Config
# IMPORTANT: Use SANDBOX token for paper trading (VA accounts)
# The Brain uses PRODUCTION token for WebSocket, but recovery needs SANDBOX token
# Try to get sandbox token from env, or use the known sandbox token
SANDBOX_TOKEN = os.getenv('TRADIER_SANDBOX_TOKEN')
if not SANDBOX_TOKEN:
    # Fallback: Use the known sandbox token from TRADIER_CREDENTIALS.md
    SANDBOX_TOKEN = 'XFE6d2z7hJnleNbpQ789otJmvW3z'
    print("⚠️ Using hardcoded sandbox token. Set TRADIER_SANDBOX_TOKEN in .env for security.")

ACCESS_TOKEN = SANDBOX_TOKEN  # Use sandbox token for recovery
# Use sandbox API for paper trading (VA accounts)
API_BASE = "https://sandbox.tradier.com/v1"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# Known symbols we trade
KNOWN_SYMBOLS = ['SPY', 'QQQ', 'IWM', 'DIA']

def get_account_id():
    """Fetch the Paper Trading Account ID (starts with VA)"""
    headers = {'Authorization': f'Bearer {ACCESS_TOKEN}', 'Accept': 'application/json'}
    try:
        print(f"📡 Fetching profile from: {API_BASE}/user/profile")
        r = requests.get(f"{API_BASE}/user/profile", headers=headers)
        if r.status_code != 200:
            print(f"❌ Error fetching profile: {r.status_code}")
            print(f"   Response: {r.text}")
            return None
            
        data = r.json()
        print(f"🔍 Profile response keys: {list(data.keys())}")
        
        # Handle list or single account
        profile = data.get('profile', {})
        if not profile:
            print(f"❌ No 'profile' in response. Full response: {json.dumps(data, indent=2)}")
            return None
            
        acct = profile.get('account', {})
        if not acct:
            print(f"❌ No 'account' in profile. Profile: {json.dumps(profile, indent=2)}")
            return None
            
        accounts = acct if isinstance(acct, list) else [acct]
        
        print(f"🔍 Found {len(accounts)} account(s)")
        
        # Look for paper trading account (starts with VA)
        for account in accounts:
            account_num = account.get('account_number', '')
            print(f"   - Account: {account_num}")
            if account_num.startswith('VA'):
                print(f"✅ Found Paper Trading Account: {account_num}")
                return account_num
        
        # Fallback: if no VA account found, use first account but warn
        if accounts:
            account_num = accounts[0].get('account_number', '')
            print(f"⚠️ WARNING: No VA account found. Using first account: {account_num}")
            print(f"   Available accounts: {[a.get('account_number', '') for a in accounts]}")
            print(f"   Make sure you're using SANDBOX token and SANDBOX API.")
            return account_num
        else:
            print("❌ No accounts found in profile response")
    except Exception as e:
        logging.error(f"Failed to get account: {e}")
        import traceback
        traceback.print_exc()
    return None

def get_positions(account_id):
    """Fetch raw positions from Tradier"""
    headers = {'Authorization': f'Bearer {ACCESS_TOKEN}', 'Accept': 'application/json'}
    url = f"{API_BASE}/accounts/{account_id}/positions"
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        logging.error(f"Error fetching positions: {r.status_code} - {r.text}")
        return []
    
    data = r.json()
    print(f"🔍 API Response structure: {list(data.keys())}")
    print(f"🔍 Full API response: {json.dumps(data, indent=2)}")
    
    # Try multiple possible response formats
    pos_data = data.get('positions', {})
    
    # Debug: Print the actual response structure
    print(f"🔍 Positions data type: {type(pos_data)}")
    print(f"🔍 Positions data value: {repr(pos_data)}")
    if pos_data and isinstance(pos_data, dict):
        print(f"🔍 Positions data keys: {list(pos_data.keys())}")
    
    # Handle different response formats
    # Tradier sometimes returns the string "null" instead of null/None
    if pos_data == 'null' or pos_data == 'null' or pos_data is None:
        print("ℹ️ Positions field is 'null' or None - no positions in this account")
        return []
    
    if isinstance(pos_data, str) and pos_data.lower() == 'null':
        print("ℹ️ Positions field is string 'null' - no positions in this account")
        return []
    
    if not pos_data or (isinstance(pos_data, dict) and len(pos_data) == 0):
        print("ℹ️ Positions field is empty")
        return []
    
    # Get position array
    positions = pos_data.get('position', [])
    
    # Handle different formats
    if positions == 'null' or positions is None:
        print("ℹ️ Position field is 'null' or None")
        return []
    
    if isinstance(positions, dict):
        positions = [positions]
    elif isinstance(positions, list):
        print(f"✅ Found {len(positions)} position(s) in list format")
    else:
        print(f"⚠️ Unexpected position format: {type(positions)}")
        return []
    
    # Filter to only option positions (have symbol with date pattern)
    option_positions = []
    for p in positions:
        symbol = p.get('symbol', '')
        if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', symbol):
            option_positions.append(p)
        else:
            print(f"   ⏭️ Skipping non-option position: {symbol}")
    
    print(f"✅ Found {len(option_positions)} option position(s) out of {len(positions)} total")
    return option_positions

def parse_option_symbol(opt_symbol):
    """
    Parse OCC option symbol format: SPY240120C00450000
    Returns: (root, expiration, type, strike)
    """
    # Pattern: SYMBOL + YYMMDD + C/P + STRIKE (8 digits)
    # Example: SPY240120C00450000 = SPY, 2024-01-20, CALL, 450.00
    match = re.match(r'^([A-Z]+)(\d{6})([CP])(\d{8})$', opt_symbol)
    if match:
        root = match.group(1)
        date_str = match.group(2)
        opt_type = 'CALL' if match.group(3) == 'C' else 'PUT'
        strike_str = match.group(4)
        
        # Parse date: YYMMDD -> YYYY-MM-DD
        year = 2000 + int(date_str[0:2])
        month = int(date_str[2:4])
        day = int(date_str[4:6])
        expiration = f"{year:04d}-{month:02d}-{day:02d}"
        
        # Parse strike: 00450000 -> 450.00
        strike = float(strike_str) / 1000.0
        
        return root, expiration, opt_type, strike
    return None, None, None, None

def group_positions_by_trade(raw_positions):
    """
    Group option positions by underlying symbol and expiration.
    Assumes positions with same root and expiration are part of same trade.
    """
    # First, filter to only option positions and parse them
    option_positions = []
    for p in raw_positions:
        symbol = p.get('symbol', '')
        # Check if it's an option (has date pattern)
        if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', symbol):
            root, exp, opt_type, strike = parse_option_symbol(symbol)
            if root:
                option_positions.append({
                    'raw': p,
                    'root': root,
                    'expiration': exp,
                    'type': opt_type,
                    'strike': strike,
                    'symbol': symbol
                })
    
    # Group by root + expiration (same trade)
    grouped = {}
    for pos in option_positions:
        key = f"{pos['root']}_{pos['expiration']}"
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(pos)
    
    return grouped

def determine_strategy(legs):
    """
    Guess strategy based on leg count and structure.
    
    Note: Ratio Spreads (3 legs: e.g., 1 Short + 2 Long) will be labeled
    as 'MANUAL_RECOVERY' since they don't match standard 2-leg or 4-leg patterns.
    This is acceptable - the Brain can still track and manage these positions.
    """
    if len(legs) == 2:
        # Check if it's a spread (one long, one short, same type)
        types = [l['type'] for l in legs]
        if types[0] == types[1]:
            return 'CREDIT_SPREAD'
        return 'CREDIT_SPREAD'  # Default for 2 legs
    elif len(legs) == 4:
        # Could be Iron Condor or Iron Butterfly
        types = [l['type'] for l in legs]
        calls = sum(1 for t in types if t == 'CALL')
        puts = sum(1 for t in types if t == 'PUT')
        if calls == 2 and puts == 2:
            return 'IRON_CONDOR'
        return 'IRON_BUTTERFLY'
    # 3 legs (Ratio Spread) or other patterns → Manual Recovery
    return 'MANUAL_RECOVERY'

def calculate_entry_price(legs):
    """
    Calculate entry price from Tradier cost basis.
    
    Math Verification:
    - Tradier cost_basis = Total Cash Value (e.g., -$250 for 5 contracts sold)
    - Brain entry_price = Aggregate Limit Price (e.g., $2.50 for 5 contracts)
    - Conversion: $250 / 100 = $2.50 ✓
    
    Process:
    1. For each leg: cost_basis / quantity = per_contract price
       Example: -$250 / 5 = -$50 per contract (credit received)
    2. Sum credits/debits across all legs to get total_cost
       Example: -$50 * 5 = -$250 (total credit received)
    3. Convert to Brain format: abs(total_cost) / 100.0
       Example: $250 / 100 = $2.50 (aggregate limit price)
    
    This matches MarketFeed's expectation where entry_price is stored
    as the aggregate limit price (unit price * quantity), not per-contract.
    """
    total_cost = 0.0
    for leg in legs:
        cost_basis = float(leg['raw'].get('cost_basis', 0))
        quantity = abs(float(leg['raw'].get('quantity', 0)))
        # Cost basis is total, so divide by quantity to get per-contract
        if quantity > 0:
            per_contract = cost_basis / quantity
            # For short positions (negative quantity in Tradier), this is credit
            # For long positions, this is debit
            if float(leg['raw'].get('quantity', 0)) < 0:
                total_cost += per_contract * quantity  # Credit received
            else:
                total_cost -= per_contract * quantity  # Debit paid
    
    # Entry price is the net credit (positive) or debit (negative)
    # Brain expects aggregate limit price: Total Cash Value / 100
    # Example: $250 total credit → $2.50 aggregate price ✓
    return abs(total_cost) / 100.0  # Convert total cash to aggregate limit price

def run_recovery():
    if not ACCESS_TOKEN:
        print("❌ Error: TRADIER_SANDBOX_TOKEN or TRADIER_ACCESS_TOKEN not found in .env")
        print("   For paper trading recovery, you need the SANDBOX token.")
        print("   Set TRADIER_SANDBOX_TOKEN in your .env file.")
        return

    print("🔌 Connecting to Tradier Sandbox (Paper Trading)...")
    print(f"📡 Using API: {API_BASE}")
    account_id = get_account_id()
    if not account_id:
        print("❌ Could not find Account ID.")
        return
    
    print(f"✅ Found Account: {account_id}")
    raw_positions = get_positions(account_id)
    
    if not raw_positions:
        print("ℹ️ No open positions found in Tradier.")
        return

    print(f"🔍 Found {len(raw_positions)} raw positions. Parsing and grouping...")

    # Group positions by trade
    grouped = group_positions_by_trade(raw_positions)
    
    if not grouped:
        print("ℹ️ No option positions found (or couldn't parse them).")
        return

    print(f"📦 Grouped into {len(grouped)} potential trades")

    # Convert to Brain Format
    brain_format = {}
    
    for key, legs in grouped.items():
        root = legs[0]['root']
        expiration = legs[0]['expiration']
        strategy = determine_strategy(legs)
        
        # Build Brain leg format
        brain_legs = []
        for leg in legs:
            qty = float(leg['raw'].get('quantity', 0))
            side = "SELL" if qty < 0 else "BUY"  # Negative qty = short = SELL
            
            brain_legs.append({
                'symbol': leg['symbol'],
                'expiration': expiration,
                'strike': leg['strike'],
                'type': leg['type'],
                'quantity': abs(int(qty)),
                'side': side
            })
        
        # Calculate entry price
        entry_price = calculate_entry_price(legs)
        
        # Determine bias (simplified: neutral for multi-leg, bullish for put spreads, bearish for call spreads)
        bias = "neutral"
        if strategy == 'CREDIT_SPREAD' and len(legs) == 2:
            if legs[0]['type'] == 'PUT':
                bias = 'bullish'  # Bull Put Spread
            else:
                bias = 'bearish'  # Bear Call Spread
        
        trade_id = f"{root}_{strategy}_RECOVERED_{int(datetime.now().timestamp())}"
        
        brain_format[trade_id] = {
            "symbol": root,
            "strategy": strategy,
            "legs": brain_legs,
            "entry_price": round(entry_price, 2),
            "bias": bias,
            "timestamp": datetime.now().isoformat(),
            "highest_pnl": -100.0  # Reset trailing stop
        }
        
        print(f"   ➕ Created {strategy} for {root} ({len(legs)} legs, Entry: ${entry_price:.2f})")

    # Determine file path (project root, same as brain_state.json)
    # This MUST match the logic in MarketFeed.__init__()
    current_dir = os.getcwd()
    if current_dir.endswith('brain'):
        # Running from brain/ directory, save to parent (project root)
        positions_file = os.path.join(os.path.dirname(current_dir), 'brain_positions.json')
        print(f"📁 Running from brain/ directory, saving to project root")
    else:
        # Running from project root
        positions_file = 'brain_positions.json'
        print(f"📁 Running from project root")
    
    # Show absolute path for clarity
    abs_path = os.path.abspath(positions_file)
    print(f"📂 File will be saved to: {abs_path}")
    
    # Save
    with open(positions_file, 'w') as f:
        json.dump(brain_format, f, indent=2)
    
    # Verify file was created
    if os.path.exists(positions_file):
        file_size = os.path.getsize(positions_file)
        print(f"\n✅ Recovery Complete!")
        print(f"💾 Saved {len(brain_format)} trades to '{abs_path}' ({file_size} bytes)")
        print("🚀 Restart the Brain now to adopt these positions.")
        print(f"   The Brain will log: '♻️ Restored {len(brain_format)} positions from disk'")
    else:
        print(f"\n❌ ERROR: File was not created at {abs_path}")
        print("   Please check file permissions and try again.")

if __name__ == "__main__":
    run_recovery()
