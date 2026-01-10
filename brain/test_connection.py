"""
Test Connection Script
Simple script to verify connectivity with the Gekko3 Gatekeeper
"""

import asyncio
import sys
from src.gatekeeper_client import GatekeeperClient


async def main():
    """Test connection to the Gatekeeper"""
    print("🔌 Testing connection to Gekko3 Gatekeeper...")
    print("-" * 60)
    
    try:
        # Initialize client
        client = GatekeeperClient()
        print(f"✅ Client initialized")
        print(f"   Gatekeeper URL: {client.base_url}")
        print()
        
        # Test status endpoint
        print("📡 Calling GET /v1/status...")
        result = await client.get_status()
        
        print(f"   HTTP Status: {result.get('http_status')}")
        print(f"   Result Status: {result.get('status')}")
        
        if result.get('status') == 'OK':
            data = result.get('data', {})
            print()
            print("✅ Connection successful! Gatekeeper is alive.")
            print()
            print("📊 System Status:")
            print(f"   Status: {data.get('status', 'N/A')}")
            print(f"   Positions: {data.get('positionsCount', 0)}")
            print(f"   Daily P&L: {data.get('dailyPnL', 0):.4f}")
            if 'equity' in data:
                print(f"   Equity: ${data.get('equity', 0):,.2f}")
            if 'lockReason' in data:
                print(f"   Lock Reason: {data.get('lockReason')}")
        elif result.get('status') == 'UNAUTHORIZED':
            print()
            print("⚠️  401 Unauthorized - This is expected if status endpoint requires auth")
            print("   The Gatekeeper is alive and responding, but requires authentication.")
        else:
            print()
            print(f"❌ Error: {result.get('error', 'Unknown error')}")
            sys.exit(1)
            
    except ValueError as e:
        print(f"❌ Configuration error: {e}")
        print()
        print("Please ensure your .env file is configured with:")
        print("  - GATEKEEPER_URL")
        print("  - API_SECRET")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Connection error: {e}")
        print()
        print("Please verify:")
        print("  1. The Gatekeeper is deployed")
        print("  2. The GATEKEEPER_URL in .env is correct")
        print("  3. Your network connection is active")
        sys.exit(1)
    
    print("-" * 60)
    print("✅ Test complete!")


if __name__ == '__main__':
    asyncio.run(main())

