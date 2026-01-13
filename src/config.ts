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

  // Risk Limits (INCREASED FOR TESTING)
  maxOpenPositions: 20,          // Increased to 20 for testing
  maxConcentrationPerSymbol: 20, // Increased to 20 for testing
  maxDailyLossPercent: 0.02,    // Keeps 2% hard stop (Safety First).

  // Execution Constraints
  staleProposalMs: 10000,       // Reject orders older than 10 seconds
  forceEodCloseEt: '15:45',     // Hard close at 3:45 PM ET
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
  DISCORD_WEBHOOK_URL?: string; // Optional: Discord webhook for EOD reports

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
