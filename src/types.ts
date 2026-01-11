/**
 * Gekko3 Type Definitions
 * Strict interfaces for type safety across the system
 */

/**
 * Credit Spread Leg: Individual option leg in a spread
 */
export interface SpreadLeg {
  symbol: string; // Option symbol (e.g., "SPY240120C00500000")
  expiration: string; // ISO 8601 date string (e.g., "2024-01-20")
  strike: number;
  type: 'CALL' | 'PUT';
  quantity: number;
  side: 'BUY' | 'SELL';
}

/**
 * Trade Proposal: The signed request from the Python Brain
 */
export interface TradeProposal {
  // Identification
  id: string; // UUID
  timestamp: number; // Unix timestamp (ms)

  // Trade details
  symbol: string; // Underlying symbol (e.g., "SPY")
  strategy: 'CREDIT_SPREAD';
  
  // OPEN = Enter Position, CLOSE = Exit Position
  side: 'OPEN' | 'CLOSE';
  
  quantity: number;
  
  // LIMIT PRICE is mandatory for spreads (Net Credit for Open, Net Debit for Close)
  price: number;

  // Spread legs (for credit spreads, typically 2 legs)
  legs: SpreadLeg[];

  // Context: The "why" from the Brain (market conditions, signals, etc.)
  context: {
    vix?: number; // VIX level (risk check)
    flow_state?: string; // Flow state check (e.g., 'UNKNOWN', 'NORMAL')
    [key: string]: unknown; // Allow additional context fields
  };

  // Authentication
  signature: string; // HMAC signature for verification
}

/**
 * Gatekeeper State: The Durable Object's internal memory
 */
export interface GatekeeperState {
  // System status
  systemLocked: boolean;
  lastLockReason?: string;
  lastLockTimestamp?: number;

  // Risk tracking
  currentPositions: Map<string, number>; // symbol -> quantity
  dailyPnL: number;
  lastEquitySnapshot?: number;

  // Operational
  lastHealthCheck: number;
}

/**
 * Risk Configuration: Shape of the Constitution
 */
export interface RiskConfig {
  // Universe constraints
  allowedSymbols: readonly string[];
  allowedStrategies: readonly string[];

  // Risk limits
  maxOpenPositions: number;
  maxConcentrationPerSymbol: number;
  maxDailyLossPercent: number;

  // Execution constraints
  staleProposalMs: number;
  forceEodCloseEt: string; // "HH:MM" format in ET timezone
}

/**
 * Proposal Evaluation Result
 */
export type ProposalStatus = 'APPROVED' | 'REJECTED';

export interface ProposalEvaluation {
  status: ProposalStatus;
  rejectionReason?: string;
  evaluatedAt: number;
}

/**
 * System Status Response
 */
export interface SystemStatus {
  status: 'NORMAL' | 'LOCKED';
  lockReason?: string;
  positionsCount: number;
  dailyPnL: number;
  equity?: number;
  lastUpdated: number;
  lastHeartbeat?: number; // Timestamp of last heartbeat from Brain (0 if never received)
}

