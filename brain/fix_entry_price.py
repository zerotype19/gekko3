"""
Quick Fix Script: Recalculate entry_price for MANUAL_RECOVERY positions
Fetches actual cost_basis from Tradier and recalculates entry_price correctly
"""
import os
import json
import requests
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Use SANDBOX token (where positions are)
SANDBOX_TOKEN = os.getenv('TRADIER_SANDBOX_TOKEN', 'XFE6d2z7hJnleNbpQ789otJmvW3z')
API_BASE = "https://sandbox.tradier.com/v1"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def get_account_id():
    """Fetch the Paper Trading Account ID"""
    headers = {'Authorization': f'Bearer {SANDBOX_TOKEN}', 'Accept': 'application/json'}
    try:
        r = requests.get(f"{API_BASE}/user/profile", headers=headers)
        if r.status_code != 200:
            return None
        data = r.json()
        profile = data.get('profile', {})
        acct = profile.get('account', {})
        accounts = acct if isinstance(acct, list) else [acct]
        for account in accounts:
            account_num = account.get('account_number', '')
            if account_num.startswith('VA'):
                return account_num
        if accounts:
            return accounts[0].get('account_number', '')
    except Exception as e:
        logging.error(f"Failed to get account: {e}")
    return None

def get_positions(account_id):
    """Fetch positions from Tradier"""
    headers = {'Authorization': f'Bearer {SANDBOX_TOKEN}', 'Accept': 'application/json'}
    url = f"{API_BASE}/accounts/{account_id}/positions"
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return {}
    
    data = r.json()
    positions = data.get('positions', {}).get('position', [])
    if positions == 'null' or not positions:
        return {}
    
    pos_list = positions if isinstance(positions, list) else [positions]
    result = {}
    for p in pos_list:
        symbol = p.get('symbol')
        if symbol:
            result[symbol] = {
                'quantity': float(p.get('quantity', 0)),
                'cost_basis': float(p.get('cost_basis', 0))
            }
    return result

def recalculate_entry_price(pos, tradier_positions):
    """Recalculate entry_price from Tradier cost_basis"""
    net_credit = 0.0
    legs_found = 0
    
    for leg in pos.get('legs', []):
        leg_symbol = leg.get('symbol')
        tradier_pos = tradier_positions.get(leg_symbol)
        
        if not tradier_pos:
            continue
        
        legs_found += 1
        qty = float(tradier_pos.get('quantity', 0))
        cost_basis = float(tradier_pos.get('cost_basis', 0))
        
        # Tradier's cost_basis is already the TOTAL cost basis (not per contract)
        # For SELL (qty < 0): cost_basis is negative (we received money)
        # For BUY (qty > 0): cost_basis is positive (we paid money)
        # So we can use cost_basis directly without dividing by quantity
        
        if qty < 0:  # SELL leg (credit received, cost_basis is negative)
            net_credit += abs(cost_basis)  # Add the credit received
        else:  # BUY leg (debit paid, cost_basis is positive)
            net_credit -= abs(cost_basis)  # Subtract the debit paid
    
    if legs_found == 0:
        return None
    
    if net_credit > 0:
        return net_credit
    elif net_credit < 0:
        return abs(net_credit)
    else:
        return None

def main():
    """Main fix function"""
    print("🔧 Fixing entry_price for MANUAL_RECOVERY positions...")
    
    # Get account ID
    account_id = get_account_id()
    if not account_id:
        print("❌ Failed to get account ID")
        return
    
    print(f"✅ Account ID: {account_id}")
    
    # Fetch positions from Tradier
    tradier_positions = get_positions(account_id)
    if not tradier_positions:
        print("❌ Failed to fetch positions from Tradier")
        return
    
    print(f"✅ Fetched {len(tradier_positions)} positions from Tradier")
    
    # Load brain_positions.json
    positions_file = 'brain_positions.json'
    if not os.path.exists(positions_file):
        print(f"❌ {positions_file} not found")
        return
    
    with open(positions_file, 'r') as f:
        data = json.load(f)
    
    # Find and fix MANUAL_RECOVERY positions
    fixed_count = 0
    for trade_id, pos in data.items():
        if pos.get('strategy') == 'MANUAL_RECOVERY':
            old_entry = pos.get('entry_price', 0)
            new_entry = recalculate_entry_price(pos, tradier_positions)
            
            if new_entry and new_entry > 0:
                if abs(new_entry - old_entry) > 0.01:
                    print(f"🔧 {trade_id}: ${old_entry:.2f} -> ${new_entry:.2f}")
                    pos['entry_price'] = round(new_entry, 2)
                    fixed_count += 1
                else:
                    print(f"✓ {trade_id}: Already correct (${old_entry:.2f})")
            else:
                print(f"⚠️ {trade_id}: Could not recalculate (missing Tradier data)")
    
    # Save updated positions
    if fixed_count > 0:
        with open(positions_file, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"✅ Fixed {fixed_count} position(s). Saved to {positions_file}")
    else:
        print("ℹ️ No positions needed fixing")

if __name__ == '__main__':
    main()
