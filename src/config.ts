/**
 * The Constitution: Immutable Risk Rules
 * These constraints are hard-coded and cannot be changed at runtime.
 * Capital Preservation > Latency. Safety > Convenience.
 */

import type { RiskConfig } from './types';

export const CONSTITUTION: RiskConfig = {
  // Universe: Expanded to include IWM (Small Caps) and DIA (Dow)
  allowedSymbols: ['SPY', 'QQQ', 'IWM', 'DIA'] as const,

  // Strategy: Multi-leg and ratio structures
  allowedStrategies: [
    'CREDIT_SPREAD', 
    'IRON_CONDOR', 
    'IRON_BUTTERFLY', 
    'RATIO_SPREAD'
  ] as const,

    // Risk Limits (INCREASED FOR TESTING)
  maxOpenPositions: 20,          // Increased to 20 for testing
  maxConcentrationPerSymbol: 20, // Increased to 20 for testing
  maxDailyLossPercent: 0.02,    // Keeps 2% hard stop (Safety First).

  // DTE Limits (NEW)
  minDte: 0,    // Allows Scalper (0DTE)
  maxDte: 60,   // Allows Trend/Farmer (30-45 DTE)

  // Correlation Groups (Phase C)
  // Assets in the same group share a risk bucket
  correlationGroups: {
    'US_INDICES': ['SPY', 'QQQ', 'IWM', 'DIA'] as const,
    'TECH': ['QQQ', 'XLK'] as const, // Example expansion
  },

  // Risk Limits (Phase C)
  riskLimits: {
    maxCorrelatedPositions: 2, // MAX 2 directional trades per group (e.g., 1 SPY + 1 QQQ is ok, but not 3)
    maxTotalPositions: 5,
  },

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
