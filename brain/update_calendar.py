#!/usr/bin/env python3
"""
Update Event Calendar (Restricted Trading Dates)
Run this script to block trading on specific dates (e.g., FOMC, CPI releases, earnings)

Usage:
    python3 brain/update_calendar.py

Edit the RESTRICTED_DATES list below to add/remove dates.
"""

import requests
import json
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Your Worker URL (from .env or hardcode)
GATEKEEPER_URL = os.getenv('GATEKEEPER_URL', 'https://gekko3-core.kevin-mcgovern.workers.dev')

# Dates to BLOCK opening trades (YYYY-MM-DD format)
RESTRICTED_DATES = [
    "2026-01-20",  # Example: FOMC Meeting
    "2026-01-21",  # Example: CPI Release
    # Add more dates as needed
]

def update_calendar():
    """Send restricted dates to Gatekeeper"""
    url = f"{GATEKEEPER_URL}/v1/admin/calendar"
    
    payload = {
        "dates": RESTRICTED_DATES
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        
        result = response.json()
        print(f"✅ Calendar updated successfully!")
        print(f"   Status: {result.get('status', 'UNKNOWN')}")
        print(f"   Restricted dates: {result.get('count', 0)}")
        print(f"   Dates: {', '.join(RESTRICTED_DATES)}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ Error updating calendar: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Response: {e.response.text}")
        return False

if __name__ == "__main__":
    print("📅 Gekko3 Event Calendar Updater")
    print(f"   Gatekeeper URL: {GATEKEEPER_URL}")
    print(f"   Restricted Dates: {len(RESTRICTED_DATES)}")
    print()
    
    success = update_calendar()
    
    if success:
        print()
        print("💡 Tip: The Gatekeeper will now REJECT all OPEN proposals on these dates.")
        print("   CLOSE proposals are still allowed (for risk management).")
    else:
        print()
        print("⚠️  Failed to update calendar. Check your GATEKEEPER_URL and network connection.")
