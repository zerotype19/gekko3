#!/usr/bin/env python3
"""
Verification Script: Verify all critical fixes are present in the current codebase
"""
import os
import re

def check_startup_sweep():
    """Verify startup order sweep is in reconcile_state()"""
    print("\n" + "="*70)
    print("VERIFICATION 1: Startup Order Sweep")
    print("="*70)
    
    file_path = "brain/src/market_feed.py"
    if not os.path.exists(file_path):
        print(f"❌ File not found: {file_path}")
        return False
    
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Check if reconcile_state has the sweep call
    reconcile_idx = content.find("async def reconcile_state")
    if reconcile_idx == -1:
        print("❌ reconcile_state function not found")
        return False
    
    # Find the sweep call within reconcile_state
    reconcile_end = content.find("async def ", reconcile_idx + 1)
    if reconcile_end == -1:
        reconcile_end = len(content)
    
    reconcile_code = content[reconcile_idx:reconcile_end]
    
    # Check for sweep call
    if "await self._sweep_stale_orders()" in reconcile_code:
        print("✅ FOUND: await self._sweep_stale_orders() in reconcile_state()")
        
        # Show context
        sweep_idx = reconcile_code.find("await self._sweep_stale_orders()")
        context_start = max(0, sweep_idx - 100)
        context_end = min(len(reconcile_code), sweep_idx + 200)
        context = reconcile_code[context_start:context_end]
        
        print("\n--- Context ---")
        print(context)
        return True
    else:
        print("❌ NOT FOUND: await self._sweep_stale_orders() in reconcile_state()")
        return False

def check_market_order_support():
    """Verify market order support in Gatekeeper"""
    print("\n" + "="*70)
    print("VERIFICATION 2: Market Order Support")
    print("="*70)
    
    file_path = "src/GatekeeperDO.ts"
    if not os.path.exists(file_path):
        print(f"❌ File not found: {file_path}")
        return False
    
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Check for proposal.type support
    if "let orderType = proposal.type" in content or "const orderType = proposal.type" in content:
        print("✅ FOUND: proposal.type support in GatekeeperDO.ts")
        
        # Show context
        type_idx = content.find("orderType = proposal.type")
        if type_idx == -1:
            type_idx = content.find("orderType = proposal.type")
        
        if type_idx != -1:
            context_start = max(0, type_idx - 150)
            context_end = min(len(content), type_idx + 300)
            context = content[context_start:context_end]
            
            print("\n--- Context ---")
            print(context)
        
        # Check for market order price exclusion
        if 'orderType !== "market"' in content or "orderType !== 'market'" in content:
            print("✅ FOUND: Price parameter excluded for market orders")
        else:
            print("⚠️ WARNING: May include price for market orders (could cause API errors)")
        
        return True
    else:
        print("❌ NOT FOUND: proposal.type support in GatekeeperDO.ts")
        
        # Check what it actually has
        if "const orderType = proposal.side" in content:
            print("⚠️ Still using hardcoded logic: const orderType = proposal.side === 'OPEN' ? ...")
        
        return False

def check_type_interface():
    """Verify TradeProposal interface has type field"""
    print("\n" + "="*70)
    print("VERIFICATION 3: TradeProposal Interface")
    print("="*70)
    
    file_path = "src/types.ts"
    if not os.path.exists(file_path):
        print(f"❌ File not found: {file_path}")
        return False
    
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Check for type field in TradeProposal
    if "type?: 'market'" in content or "type?:" in content:
        print("✅ FOUND: type field in TradeProposal interface")
        
        # Show the interface
        interface_start = content.find("export interface TradeProposal")
        if interface_start != -1:
            interface_end = content.find("}", interface_start)
            if interface_end != -1:
                interface = content[interface_start:interface_end+100]
                
                # Extract type field line
                type_match = re.search(r"type\?[^;]+;", interface)
                if type_match:
                    print(f"\n--- Type Field ---")
                    print(type_match.group(0))
        
        return True
    else:
        print("❌ NOT FOUND: type field in TradeProposal interface")
        return False

def main():
    print("\n" + "="*70)
    print("GEKKO3 FIXES VERIFICATION")
    print("Checking current working directory files (not commit folders)")
    print("="*70)
    
    results = []
    results.append(check_startup_sweep())
    results.append(check_market_order_support())
    results.append(check_type_interface())
    
    print("\n" + "="*70)
    print("VERIFICATION SUMMARY")
    print("="*70)
    
    fixes = [
        "Startup Order Sweep",
        "Market Order Support",
        "Type Interface"
    ]
    
    for i, (fix, result) in enumerate(zip(fixes, results), 1):
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{i}. {fix}: {status}")
    
    all_passed = all(results)
    
    if all_passed:
        print("\n✅ ALL FIXES VERIFIED - System is ready!")
    else:
        print("\n❌ SOME FIXES MISSING - Please review above")
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    exit(main())
