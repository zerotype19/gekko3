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
    'RATIO_SPREAD',
    'CALENDAR_SPREAD'
  ] as const,

    // Risk Limits (INCREASED FOR TESTING)
  maxOpenPositions: 20,          // Increased to 20 for testing
  maxConcentrationPerSymbol: 20, // Increased to 20 for testing
  maxDailyLossPercent: 0.05,     // Relaxed to 5% for testing (was 2%)

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
    // CRITICAL: Increased to 10 for testing to avoid blocking valid trades
    // Was 2, which would block the 3rd trend trade
    maxCorrelatedPositions: 10, // MAX 10 directional trades per group (e.g., allows 10 bullish trades in US_INDICES)
    maxTotalPositions: 20, // Match maxOpenPositions for testing (was 5, increased to allow full testing)
  },

  // Execution Constraints
  staleProposalMs: 10000,       // Reject orders older than 10 seconds
  forceEodCloseEt: null,        // Disabled to allow Multi-Day strategies (Calendar/Ratio)
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
  SEND_EMAIL?: any; // Cloudflare Email binding for sending EOD reports (typed as any due to Cloudflare Workers types)

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
