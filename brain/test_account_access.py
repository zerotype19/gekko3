import requests
import os
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

SANDBOX_URL = "https://sandbox.tradier.com/v1"
# Use Sandbox credentials directly (as specified)
ACCESS_TOKEN = "XFE6d2z7hJnleNbpQ789otJmvW3z"  # Sandbox Token
ACCOUNT_ID = "VA13978285"  # Sandbox Account ID

print("\n🕵️‍♂️ CHECKING TRADIER SANDBOX CREDENTIALS")
print("=" * 50)
print(f"🔑 Token: {ACCESS_TOKEN[:10]}... (Sandbox)")
print(f"🆔 Target Account ID: {ACCOUNT_ID}")

headers = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/json"
}

def check_profile():
    print("\n1. Checking User Profile...")
    try:
        r = requests.get(f"{SANDBOX_URL}/user/profile", headers=headers)
        print(f"   Status Code: {r.status_code}")
        
        if r.status_code == 200:
            profile = r.json()
            print("✅ SUCCESS: Token is valid.")
            
            # Extract profile data
            profile_data = profile.get('profile', {})
            print(f"   Name: {profile_data.get('name', 'N/A')}")
            
            # List all accounts associated with this token
            accounts = profile_data.get('account', [])
            # Handle single account vs list of accounts
            if isinstance(accounts, dict):
                accounts = [accounts]
            
            if not accounts:
                print("   ⚠️  No accounts found in profile")
                return []
                
            print(f"   📋 Accounts found in Sandbox for this token:")
            valid_ids = []
            for acc in accounts:
                aid = acc.get('account_number', 'N/A')
                valid_ids.append(aid)
                acc_type = acc.get('type', 'N/A')
                acc_status = acc.get('status', 'N/A')
                print(f"      - {aid} (Type: {acc_type}, Status: {acc_status})")
            
            return valid_ids
        elif r.status_code == 401:
            print(f"❌ FAILED (401): Unauthorized")
            print("   🚨 DIAGNOSIS: Access Token is invalid or wrong token type.")
            return []
        else:
            print(f"❌ FAILED ({r.status_code}): {r.text}")
            return []
    except Exception as e:
        print(f"❌ CONNECTION ERROR: {e}")
        return []

def check_balances(target_id):
    print(f"\n2. Checking Balances for Target Account {target_id}...")
    try:
        r = requests.get(f"{SANDBOX_URL}/accounts/{target_id}/balances", headers=headers)
        print(f"   Status Code: {r.status_code}")
        
        if r.status_code == 200:
            print("✅ SUCCESS: Account is accessible.")
            data = r.json()
            bal = data.get('balances', {})
            total_equity = bal.get('total_equity', 0)
            total_cash = bal.get('total_cash', 0)
            print(f"   💰 Total Equity: ${total_equity}")
            print(f"   💵 Cash: ${total_cash}")
            return True
        elif r.status_code == 404:
            print(f"❌ FAILED (404): Account not found")
            print("   🚨 DIAGNOSIS: This Account ID does not exist in Sandbox.")
            return False
        elif r.status_code == 500:
            print(f"❌ FAILED (500): Internal Server Error")
            print("   🚨 DIAGNOSIS: This Account ID likely does not exist in the Sandbox database.")
            return False
        else:
            print(f"❌ FAILED ({r.status_code}): {r.text}")
            return False
    except Exception as e:
        print(f"❌ CONNECTION ERROR: {e}")
        return False

if __name__ == "__main__":
    print("\n" + "=" * 50)
    valid_accounts = check_profile()
    print("\n" + "=" * 50)
    
    if not valid_accounts:
        print(f"\n❌ NO ACCOUNTS FOUND: Cannot verify Account ID")
        print(f"   Check that your Access Token is correct and is a Sandbox token.")
    elif ACCOUNT_ID in valid_accounts:
        print(f"\n✅ MATCH: Your Account ID ({ACCOUNT_ID}) matches a valid Sandbox account.")
        check_balances(ACCOUNT_ID)
        print("\n" + "=" * 50)
        print("✅ DIAGNOSIS: Credentials are correct.")
        print("   If orders still fail with 500, it's a Tradier Sandbox limitation,")
        print("   not a credential issue.")
    else:
        print(f"\n❌ MISMATCH: Your Account ID ({ACCOUNT_ID}) was NOT found in this Sandbox Token's profile.")
        print(f"\n   📋 Valid Account IDs for this token:")
        for acc_id in valid_accounts:
            print(f"      - {acc_id}")
        if valid_accounts:
            print(f"\n   👉 Please update your configuration to use: {valid_accounts[0]}")
        print("\n" + "=" * 50)
        print("❌ DIAGNOSIS: Wrong Account ID for Sandbox API.")
        print("   This would cause 500 errors when placing orders.")
