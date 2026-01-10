/**
 * The Constitution: Immutable Risk Rules
 * These constraints are hard-coded and cannot be changed at runtime.
 * Capital Preservation > Latency. Safety > Convenience.
 */

import type { RiskConfig } from './types';

export const CONSTITUTION: RiskConfig = {
  // Universe: Only these symbols are allowed
  allowedSymbols: ['SPY', 'QQQ'] as const,

  // Strategy: Only credit spreads are permitted
  allowedStrategies: ['CREDIT_SPREAD'] as const,

  // Risk Limits
  maxOpenPositions: 4, // Maximum total open positions
  maxConcentrationPerSymbol: 2, // Maximum positions per symbol
  maxDailyLossPercent: 0.02, // Lock system if NAV drops 2%

  // Execution Constraints
  staleProposalMs: 10000, // Reject orders older than 10 seconds
  forceEodCloseEt: '15:45', // Hard close at 3:45 PM ET (before market close)
};

/**
 * Environment configuration
 */
export interface Env {
  // Cloudflare bindings
  GATEKEEPER_DO: DurableObjectNamespace;
  DB: D1Database;

  // Secrets (set via: wrangler secret put <NAME>)
  TRADIER_ACCESS_TOKEN: string;
  TRADIER_ACCOUNT_ID: string;
  API_SECRET: string;

  // Environment
  ENV?: 'production' | 'staging' | 'development';
}

/**
 * Helper: Check if a symbol is allowed
 */
export function isAllowedSymbol(symbol: string): boolean {
  return CONSTITUTION.allowedSymbols.includes(symbol as any);
}

/**
 * Helper: Check if a strategy is allowed
 */
export function isAllowedStrategy(strategy: string): boolean {
  return CONSTITUTION.allowedStrategies.includes(strategy as any);
}

