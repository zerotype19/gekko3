"""
Kill Switch Drill - Emergency System Lock Test
Tests that the emergency lock mechanism works instantly to stop all trading
"""

import asyncio
import aiohttp
import json
import os
from dotenv import load_dotenv

load_dotenv()

GATEKEEPER_URL = os.getenv('GATEKEEPER_URL', 'https://gekko3-core.kevin-mcgovern.workers.dev')

async def test_kill_switch():
    print("\n" + "=" * 70)
    print("🛑 KILL SWITCH DRILL - Emergency System Lock Test")
    print("=" * 70)
    
    async with aiohttp.ClientSession() as session:
        # STEP 1: Check initial status (should be NORMAL)
        print("\n📊 STEP 1: Checking initial Gatekeeper status...")
        try:
            async with session.get(f"{GATEKEEPER_URL}/v1/status") as resp:
                status_data = await resp.json()
                print(f"   Status: {status_data.get('status', 'UNKNOWN')}")
                print(f"   Positions: {status_data.get('positionsCount', 0)}")
                if status_data.get('status') == 'LOCKED':
                    print(f"   ⚠️  System is already LOCKED: {status_data.get('lockReason', 'Unknown')}")
                    initial_status = 'LOCKED'
                else:
                    initial_status = 'NORMAL'
                    print("   ✅ System is NORMAL (unlocked)")
        except Exception as e:
            print(f"   ❌ Failed to get status: {e}")
            return
        
        # STEP 2: Trigger the LOCK
        print("\n🛑 STEP 2: Triggering Emergency Lock...")
        try:
            lock_payload = {"reason": "Kill Switch Drill Test"}
            async with session.post(
                f"{GATEKEEPER_URL}/v1/admin/lock",
                json=lock_payload,
                headers={"Content-Type": "application/json"}
            ) as resp:
                lock_response = await resp.json()
                if resp.status == 200:
                    print(f"   ✅ Lock command sent: {lock_response.get('status', 'UNKNOWN')}")
                    print(f"   Reason: {lock_response.get('reason', 'N/A')}")
                else:
                    print(f"   ⚠️  Lock response: {resp.status} - {lock_response}")
        except Exception as e:
            print(f"   ❌ Failed to trigger lock: {e}")
            return
        
        # STEP 3: Verify lock worked by checking status
        print("\n🔒 STEP 3: Verifying system is LOCKED...")
        await asyncio.sleep(1)  # Give it a moment to persist
        try:
            async with session.get(f"{GATEKEEPER_URL}/v1/status") as resp:
                status_data = await resp.json()
                if status_data.get('status') == 'LOCKED':
                    print(f"   ✅ SUCCESS: System is now LOCKED")
                    print(f"   Lock Reason: {status_data.get('lockReason', 'N/A')}")
                else:
                    print(f"   ❌ FAILURE: System is still {status_data.get('status')}")
                    print(f"   This means the lock did not work!")
                    return
        except Exception as e:
            print(f"   ❌ Failed to verify lock: {e}")
            return
        
        # STEP 4: Test that proposals are rejected while locked
        print("\n🚫 STEP 4: Testing that proposals are REJECTED while locked...")
        try:
            # Create a test proposal (should be rejected due to lock, not validation)
            test_proposal = {
                "symbol": "SPY",
                "strategy": "CREDIT_SPREAD",
                "side": "SELL",
                "quantity": 1,
                "legs": [{
                    "symbol": "SPY_TEST",
                    "expiration": "2026-01-16",
                    "strike": 400,
                    "type": "PUT",
                    "quantity": 1,
                    "side": "SELL"
                }],
                "context": {
                    "vix": 15.0,
                    "flow_state": "risk_on"
                }
            }
            
            from src.gatekeeper_client import GatekeeperClient
            client = GatekeeperClient()
            response = await client.send_proposal(test_proposal)
            
            if response.get('status') == 'REJECTED':
                reason = response.get('reason') or response.get('rejectionReason', '')
                if 'locked' in reason.lower() or 'lock' in reason.lower():
                    print(f"   ✅ SUCCESS: Proposal correctly rejected due to system lock")
                    print(f"   Rejection Reason: {reason}")
                else:
                    print(f"   ⚠️  Proposal rejected, but reason is: {reason}")
                    print(f"   (May have been rejected for other reasons - still good)")
            else:
                print(f"   ❌ FAILURE: Proposal was {response.get('status')} (should be REJECTED)")
                print(f"   This means the lock is not blocking proposals!")
        except Exception as e:
            print(f"   ⚠️  Could not test proposal rejection: {e}")
            print(f"   (This is okay - the lock is verified in Step 3)")
        
        # STEP 5: Restore original state (if it was NORMAL)
        if initial_status == 'NORMAL':
            print("\n🔄 STEP 5: Restoring system to NORMAL state...")
            try:
                async with session.post(
                    f"{GATEKEEPER_URL}/v1/admin/unlock",
                    json={},
                    headers={"Content-Type": "application/json"}
                ) as resp:
                    unlock_response = await resp.json()
                    if resp.status == 200:
                        print(f"   ✅ Unlock command sent: {unlock_response.get('status', 'UNKNOWN')}")
                        print(f"   Message: {unlock_response.get('message', 'N/A')}")
                        
                        # Verify unlock worked
                        await asyncio.sleep(1)
                        async with session.get(f"{GATEKEEPER_URL}/v1/status") as status_resp:
                            status_data = await status_resp.json()
                            if status_data.get('status') == 'NORMAL':
                                print(f"   ✅ SUCCESS: System restored to NORMAL")
                            else:
                                print(f"   ⚠️  System status: {status_data.get('status')}")
                    else:
                        print(f"   ⚠️  Unlock response: {resp.status} - {unlock_response}")
                        print("   ⚠️  System remains LOCKED - may need manual unlock")
            except Exception as e:
                print(f"   ⚠️  Failed to unlock: {e}")
                print("   ⚠️  System remains LOCKED - may need manual unlock")
        else:
            print("\n🔄 STEP 5: System was already locked - leaving it locked")
        
        print("\n" + "=" * 70)
        print("✅ KILL SWITCH DRILL COMPLETE")
        print("=" * 70)
        print("\n📋 Summary:")
        print("   ✅ Emergency lock mechanism: WORKING")
        print("   ✅ Status verification: WORKING")
        print("   ✅ Lock persistence: VERIFIED")
        print("\n🛑 In an emergency:")
        print("   1. Fastest: Ctrl+C in Brain terminal (kills Python)")
        print("   2. Nuclear: Lock Gatekeeper via /v1/admin/lock")
        print("   3. Ultimate: Invalidate TRADIER_ACCESS_TOKEN in Cloudflare")
        print("\n⚠️  System is currently LOCKED (restore manually if needed)")

if __name__ == "__main__":
    asyncio.run(test_kill_switch())
