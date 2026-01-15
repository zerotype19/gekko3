"""
Position Sizer (Professional Grade)
Implements the "2% Rule" for institutional position sizing.

Calculates safe trade sizes based on:
- Account Equity
- Risk Percentage (default 2%)
- Spread Width (max loss per contract)
"""

import logging
import math


class PositionSizer:
    """Calculates optimal position size based on risk management rules"""
    
    def __init__(
        self,
        risk_percent: float = 0.02,  # 2% risk per trade
        min_quantity: int = 1,
        max_quantity: int = 20,  # Hard cap for liquidity/safety
        max_allocation_percent: float = 0.10  # Never risk >10% of account in one trade
    ):
        self.risk_percent = risk_percent
        self.min_quantity = min_quantity
        self.max_quantity = max_quantity
        self.max_allocation_percent = max_allocation_percent
    
    def calculate_size(self, equity: float, spread_width: float) -> int:
        """
        Calculate position size based on equity and spread width.
        
        Args:
            equity: Total account equity (e.g., $100,000)
            spread_width: Width of the spread in dollars (e.g., 5.0 for $5 wide spread)
        
        Returns:
            Quantity (number of contracts) to trade
        
        Logic:
            1. Risk Amount = Equity * Risk Percentage
            2. Max Loss Per Contract = Spread Width * 100 (standard option multiplier)
            3. Quantity = floor(Risk Amount / Max Loss Per Contract)
            4. Apply constraints (min, max, allocation cap)
        """
        if equity <= 0:
            logging.warning(f"⚠️ Invalid equity: ${equity}. Using default quantity: {self.min_quantity}")
            return self.min_quantity
        
        if spread_width <= 0:
            logging.warning(f"⚠️ Invalid spread width: ${spread_width}. Using default quantity: {self.min_quantity}")
            return self.min_quantity
        
        # Step 1: Calculate risk amount
        risk_amount = equity * self.risk_percent
        
        # Step 2: Calculate max loss per contract
        # For credit spreads: max loss = (spread_width * 100) - credit_received
        # For simplicity, we assume worst case: max_loss = spread_width * 100
        # (This is conservative and accounts for the credit received being small relative to width)
        max_loss_per_contract = spread_width * 100
        
        # Step 3: Calculate quantity based on risk
        if max_loss_per_contract == 0:
            logging.warning(f"⚠️ Zero max loss per contract. Using default quantity: {self.min_quantity}")
            return self.min_quantity
        
        raw_quantity = risk_amount / max_loss_per_contract
        quantity = int(math.floor(raw_quantity))
        
        # Step 4: Apply constraints
        
        # Constraint 1: Minimum quantity
        if quantity < self.min_quantity:
            quantity = self.min_quantity
        
        # Constraint 2: Maximum quantity (hard cap)
        if quantity > self.max_quantity:
            quantity = self.max_quantity
            logging.info(f"⚖️ Quantity capped at {self.max_quantity} (hard limit)")
        
        # Constraint 3: Maximum allocation (never risk >10% of account)
        max_allocation_dollars = equity * self.max_allocation_percent
        max_quantity_by_allocation = int(math.floor(max_allocation_dollars / max_loss_per_contract))
        
        if quantity > max_quantity_by_allocation:
            quantity = max_quantity_by_allocation
            logging.info(f"⚖️ Quantity capped at {quantity} (10% allocation limit)")
        
        # Ensure quantity is at least min_quantity even after allocation check
        if quantity < self.min_quantity:
            quantity = self.min_quantity
        
        return quantity
    
    def get_risk_amount(self, equity: float) -> float:
        """Get the risk amount based on equity and risk percentage"""
        return equity * self.risk_percent
    
    def get_max_allocation(self, equity: float) -> float:
        """Get the maximum allocation amount (10% of equity)"""
        return equity * self.max_allocation_percent
