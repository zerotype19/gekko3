"""
Quick Fix: Sync brain_state.json with brain_positions.json
Updates the entry_price in brain_state.json from brain_positions.json
"""
import json
import os

positions_file = 'brain_positions.json'
state_file = 'brain_state.json'

if not os.path.exists(positions_file):
    print(f"❌ {positions_file} not found")
    exit(1)

if not os.path.exists(state_file):
    print(f"❌ {state_file} not found")
    exit(1)

# Load positions
with open(positions_file, 'r') as f:
    positions_data = json.load(f)

# Load state
with open(state_file, 'r') as f:
    state_data = json.load(f)

# Update entry_price in state from positions
positions_list = state_data.get('system', {}).get('positions', [])
updated_count = 0

for state_pos in positions_list:
    trade_id = state_pos.get('trade_id', '')
    if trade_id in positions_data:
        disk_pos = positions_data[trade_id]
        old_entry = state_pos.get('entry_price', 0)
        new_entry = disk_pos.get('entry_price', 0)
        
        if abs(new_entry - old_entry) > 0.01:
            print(f"🔧 Updating {trade_id}: ${old_entry:.2f} -> ${new_entry:.2f}")
            state_pos['entry_price'] = new_entry
            updated_count += 1

# Save updated state
if updated_count > 0:
    with open(state_file, 'w') as f:
        json.dump(state_data, f, indent=2)
    print(f"✅ Updated {updated_count} position(s) in {state_file}")
else:
    print("ℹ️ No updates needed")
