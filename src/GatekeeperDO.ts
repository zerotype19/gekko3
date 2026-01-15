/**
 * Gatekeeper Durable Object
 * The Risk Engine: Validates proposals and executes trades
 */

import { CONSTITUTION, type Env } from './config';
import { createTradierClient } from './lib/tradier';
import { verifySignature, extractSignatureFromHeaders } from './lib/security';
import type { TradeProposal, ProposalEvaluation, ProposalStatus, SystemStatus } from './types';

/**
 * Calculate Days To Expiration (DTE) from an ISO date string
 */
function calculateDTE(expirationDateStr: string): number {
  const expiration = new Date(expirationDateStr);
  const now = new Date();
  const diffMs = expiration.getTime() - now.getTime();
  const diffDays = Math.ceil(diffMs / (1000 * 60 * 60 * 24));
  return diffDays;
}

/**
 * Get current time in ET (Eastern Time)
 */
function getCurrentTimeET(): { hour: number; minute: number } {
  const now = new Date();
  // Convert UTC to ET (simplified - in production, use proper timezone library)
  const etOffset = -5; // EST offset (adjust for DST in production)
  const etTime = new Date(now.getTime() + etOffset * 60 * 60 * 1000);
  return {
    hour: etTime.getUTCHours(),
    minute: etTime.getUTCMinutes(),
  };
}

/**
 * Gatekeeper Durable Object
 */
export class GatekeeperDO {
  private state: DurableObjectState;
  private env: Env;
  private tradierClient: ReturnType<typeof createTradierClient>;
  private systemLocked: boolean = false;
  private lockReason?: string;
  private startOfDayEquity?: number;
  private equityCache: { value: number; timestamp: number } | null = null;
  private EQUITY_CACHE_TTL_MS = 60000; // Cache equity for 1 minute
  private lastHeartbeat: number = 0; // Timestamp of last heartbeat from Brain
  private restrictedDates: Set<string> = new Set(); // Phase C: Event Calendar Lock

  constructor(state: DurableObjectState, env: Env) {
    this.state = state;
    this.env = env;
    this.tradierClient = createTradierClient(env);
  }

  // Alias for compatibility
  get ctx() {
    return this.state;
  }

  /**
   * Initialize state from D1 database
   */
  async initializeState(): Promise<void> {
    return this.state.blockConcurrencyWhile(async () => {
      // Load system status from D1
      const statusResult = await this.env.DB.prepare(
        'SELECT status FROM system_status WHERE id = ?'
      )
        .bind('singleton')
        .first<{ status: 'NORMAL' | 'LOCKED' }>();

      this.systemLocked = statusResult?.status === 'LOCKED';

      // Load restricted dates (Phase C: Event Calendar)
      const dates = await this.state.storage.get<string[]>('restrictedDates');
      this.restrictedDates = new Set(dates || []);

      // Load start of day equity (from most recent account snapshot before market open)
      // For now, we'll fetch current equity and use it as baseline on first run
      if (!this.startOfDayEquity) {
        try {
          const balances = await this.tradierClient.getBalances();
          this.startOfDayEquity = balances.total_equity;
        } catch (error) {
          console.error('Failed to fetch start of day equity:', error);
        }
      }
    });
  }

  /**
   * Get current equity (with caching)
   */
  async getCurrentEquity(): Promise<number> {
    const now = Date.now();
    
    // Use cache if still valid
    if (this.equityCache && (now - this.equityCache.timestamp) < this.EQUITY_CACHE_TTL_MS) {
      return this.equityCache.value;
    }

    // Fetch fresh equity
    try {
      const balances = await this.tradierClient.getBalances();
      const equity = balances.total_equity;
      this.equityCache = { value: equity, timestamp: now };
      
      // Update start of day equity if not set
      if (!this.startOfDayEquity) {
        this.startOfDayEquity = equity;
      }
      
      return equity;
    } catch (error) {
      console.error('Failed to fetch equity:', error);
      // Fallback to cache or start of day equity
      return this.equityCache?.value ?? this.startOfDayEquity ?? 0;
    }
  }

  /**
   * Calculate daily loss percentage
   */
  async getDailyLossPercent(): Promise<number> {
    const currentEquity = await this.getCurrentEquity();
    if (!this.startOfDayEquity || this.startOfDayEquity === 0) {
      return 0;
    }
    
    const loss = this.startOfDayEquity - currentEquity;
    return loss / this.startOfDayEquity;
  }

  /**
   * Get open positions count from D1
   */
  async getOpenPositionsCount(): Promise<number> {
    const result = await this.env.DB.prepare(
      'SELECT COUNT(DISTINCT symbol) as count FROM positions WHERE quantity != 0'
    ).first<{ count: number }>();
    
    return result?.count ?? 0;
  }

  /**
   * Get open position metadata (for correlation checking)
   * Returns list of { symbol, bias, strategy }
   */
  async getOpenPositionMetadata(): Promise<Array<{ symbol: string; bias: string; strategy: string }>> {
    const metadataKey = 'positionMetadata';
    const stored = await this.state.storage.get<Record<string, { symbol: string; bias: string; strategy: string }>>(metadataKey);
    if (!stored) {
      return [];
    }
    return Object.values(stored);
  }

  /**
   * Save position metadata (called when trade opens)
   */
  async savePositionMetadata(tradeId: string, data: { symbol: string; bias: string; strategy: string }): Promise<void> {
    const metadataKey = 'positionMetadata';
    const stored = await this.state.storage.get<Record<string, { symbol: string; bias: string; strategy: string }>>(metadataKey) || {};
    stored[tradeId] = data;
    await this.state.storage.put(metadataKey, stored);
  }

  /**
   * Remove position metadata (called when trade closes)
   */
  async removePositionMetadata(tradeId: string): Promise<void> {
    const metadataKey = 'positionMetadata';
    const stored = await this.state.storage.get<Record<string, { symbol: string; bias: string; strategy: string }>>(metadataKey);
    if (stored && stored[tradeId]) {
      delete stored[tradeId];
      await this.state.storage.put(metadataKey, stored);
    }
  }

  /**
   * Update restricted trading dates (Phase C: Event Calendar)
   */
  async updateCalendar(dates: string[]): Promise<void> {
    this.restrictedDates = new Set(dates);
    await this.state.storage.put('restrictedDates', dates);
  }

  /**
   * Get position count for a specific symbol
   */
  async getSymbolPositionCount(symbol: string): Promise<number> {
    const result = await this.env.DB.prepare(
      'SELECT COUNT(*) as count FROM positions WHERE symbol = ? AND quantity != 0'
    )
      .bind(symbol)
      .first<{ count: number }>();
    
    return result?.count ?? 0;
  }

  /**
   * Lock the system
   */
  async lockSystem(reason: string): Promise<void> {
    this.systemLocked = true;
    this.lockReason = reason;
    
    await this.env.DB.prepare(
      'UPDATE system_status SET status = ?, updated_at = unixepoch("now") WHERE id = ?'
    )
      .bind('LOCKED', 'singleton')
      .run();
  }

  /**
   * Unlock the system
   */
  async unlockSystem(): Promise<void> {
    this.systemLocked = false;
    this.lockReason = undefined;
    
    await this.env.DB.prepare(
      'UPDATE system_status SET status = ?, updated_at = unixepoch("now") WHERE id = ?'
    )
      .bind('NORMAL', 'singleton')
      .run();
  }

  /**
   * Sync account state from Tradier (Source of Truth)
   * Updates positions table and equity cache from live broker data
   * CRITICAL: Called before every proposal evaluation to ensure accurate position counts
   */
  async syncAccountState(): Promise<void> {
    try {
      // 1. Get Real Balances (update equity cache)
      const balances = await this.tradierClient.getBalances();
      const now = Date.now();
      this.equityCache = { value: balances.total_equity, timestamp: now };
      
      // Set start of day equity baseline if missing
      if (!this.startOfDayEquity) {
        this.startOfDayEquity = balances.total_equity;
      }

      // 2. Get Real Positions from Tradier
      const realPositions = await this.tradierClient.getPositions();

      // 3. Update Database (Source of Truth is Broker, not DB)
      // Clear stale cache - we trust Tradier's data, not our assumptions
      await this.env.DB.prepare('DELETE FROM positions').run();

      // Insert real positions if any exist
      if (realPositions.length > 0) {
        const stmt = this.env.DB.prepare(
          `INSERT INTO positions (symbol, quantity, cost_basis, date_acquired, updated_at)
           VALUES (?, ?, ?, ?, unixepoch('now'))`
        );
        
        // Use batch for efficiency
        const batch = realPositions.map(p => {
          // Convert date_acquired string to timestamp if needed
          const dateAcquired = p.date_acquired 
            ? Math.floor(new Date(p.date_acquired).getTime() / 1000)
            : Math.floor(Date.now() / 1000);
          
          return stmt.bind(p.symbol, p.quantity, p.cost_basis, dateAcquired);
        });
        
        await this.env.DB.batch(batch);
      }
    } catch (error) {
      // Log error but don't fail the proposal evaluation
      // If sync fails, we'll use cached data (better than blocking trades)
      console.error('Failed to sync account state from Tradier:', error);
    }
  }

  /**
   * Evaluate a trade proposal against all risk rules
   */
  async evaluateProposal(proposal: TradeProposal, signature: string): Promise<ProposalEvaluation> {
    // Initialize state if needed
    await this.initializeState();

    // CRITICAL: Sync account state BEFORE checking position limits
    // This ensures we have accurate position counts from Tradier (source of truth)
    await this.syncAccountState();

    const evaluatedAt = Date.now();

    // 1. Verify Signature
    const isValidSignature = await verifySignature(proposal as unknown as { id: string; timestamp: number; signature?: string; [key: string]: unknown }, signature, this.env.API_SECRET);
    if (!isValidSignature) {
      return {
        status: 'REJECTED',
        rejectionReason: 'Invalid signature',
        evaluatedAt,
      };
    }

    // 2. System Lock Check
    if (this.systemLocked) {
      return {
        status: 'REJECTED',
        rejectionReason: `System is locked: ${this.lockReason ?? 'Unknown reason'}`,
        evaluatedAt,
      };
    }

    // 3. Freshness Check
    const ageMs = Date.now() - proposal.timestamp;
    if (ageMs > CONSTITUTION.staleProposalMs) {
      return {
        status: 'REJECTED',
        rejectionReason: `Proposal is stale: ${ageMs}ms old (max: ${CONSTITUTION.staleProposalMs}ms)`,
        evaluatedAt,
      };
    }

    // 4. Constitution Checks
    if (!CONSTITUTION.allowedSymbols.includes(proposal.symbol)) {
      return {
        status: 'REJECTED',
        rejectionReason: `Symbol not allowed: ${proposal.symbol}. Allowed: ${CONSTITUTION.allowedSymbols.join(', ')}`,
        evaluatedAt,
      };
    }

    // Strategy validation: Only enforce for OPEN proposals
    // CLOSE proposals can use any strategy (we're closing existing positions)
    if (proposal.side === 'OPEN' && !CONSTITUTION.allowedStrategies.includes(proposal.strategy)) {
      return {
        status: 'REJECTED',
        rejectionReason: `Strategy not allowed: ${proposal.strategy}`,
        evaluatedAt,
      };
    }

    // Strict Limit Price Check
    if (proposal.price === undefined || proposal.price === null || proposal.price <= 0) {
      return {
        status: 'REJECTED',
        rejectionReason: 'Limit Price is required for safety',
        evaluatedAt,
      };
    }

    // 4.b Structure Validation (New for Phase B)
    // Skip structure validation for CLOSE proposals (we're just closing existing positions)
    if (proposal.side === 'OPEN') {
      const legCount = proposal.legs.length;

      switch (proposal.strategy) {
        case 'CREDIT_SPREAD':
          if (legCount !== 2) {
            return {
              status: 'REJECTED',
              rejectionReason: `CREDIT_SPREAD must have exactly 2 legs (got ${legCount})`,
              evaluatedAt,
            };
          }
          break;

        case 'IRON_CONDOR':
          if (legCount !== 4) {
            return {
              status: 'REJECTED',
              rejectionReason: `IRON_CONDOR must have exactly 4 legs (got ${legCount})`,
              evaluatedAt,
            };
          }
          break;

        case 'IRON_BUTTERFLY':
          // Iron Fly usually has 4 legs (Long Put, Short Put, Short Call, Long Call)
          // Sometimes 3 if strikes overlap, but we stick to 4 for clarity
          if (legCount !== 4) {
            return {
              status: 'REJECTED',
              rejectionReason: `IRON_BUTTERFLY must have exactly 4 legs (got ${legCount})`,
              evaluatedAt,
            };
          }
          break;

        case 'RATIO_SPREAD':
          if (legCount !== 2) {
            return {
              status: 'REJECTED',
              rejectionReason: `RATIO_SPREAD must have exactly 2 legs (got ${legCount})`,
              evaluatedAt,
            };
          }
          // Ratio Check: Quantities must NOT be equal (that's just a spread)
          if (proposal.legs[0].quantity === proposal.legs[1].quantity) {
            return {
              status: 'REJECTED',
              rejectionReason: `RATIO_SPREAD must have unequal quantities`,
              evaluatedAt,
            };
          }
          break;
          
        default:
          // Should be caught by allowedStrategies check, but safety first
          return {
            status: 'REJECTED',
            rejectionReason: `Unknown strategy structure: ${proposal.strategy}`,
            evaluatedAt,
          };
      }
    }

    // DTE Check: Calculate from first leg's expiration
    // Skip for CLOSE proposals (we're just closing existing positions)
    if (proposal.side === 'OPEN') {
      if (proposal.legs.length === 0) {
        return {
          status: 'REJECTED',
          rejectionReason: 'Proposal must have at least one leg',
          evaluatedAt,
        };
      }

      const firstLeg = proposal.legs[0];
      if (!firstLeg.expiration) {
        return {
          status: 'REJECTED',
          rejectionReason: 'Leg expiration date is required',
          evaluatedAt,
        };
      }

      const dte = calculateDTE(firstLeg.expiration);
      // Use Constitution limits instead of hardcoded 1-7 days
      if (dte < CONSTITUTION.minDte || dte > CONSTITUTION.maxDte) {
        return {
          status: 'REJECTED',
          rejectionReason: `DTE out of range: ${dte} days (must be ${CONSTITUTION.minDte}-${CONSTITUTION.maxDte} days)`,
          evaluatedAt,
        };
      }
    }

    // 3.5. Event Calendar Check (Phase C, Step 3)
    if (proposal.side === 'OPEN') {
      const today = new Date().toISOString().split('T')[0]; // YYYY-MM-DD
      if (this.restrictedDates.has(today)) {
        return {
          status: 'REJECTED',
          rejectionReason: `Trading suspended for Event Risk (Calendar Lock): ${today}`,
          evaluatedAt,
        };
      }
    }

    // 5. Risk Checks
    const dailyLossPercent = await this.getDailyLossPercent();
    if (dailyLossPercent >= CONSTITUTION.maxDailyLossPercent) {
      await this.lockSystem(`Daily loss limit exceeded: ${(dailyLossPercent * 100).toFixed(2)}%`);
      return {
        status: 'REJECTED',
        rejectionReason: `Daily loss limit exceeded: ${(dailyLossPercent * 100).toFixed(2)}% (limit: ${(CONSTITUTION.maxDailyLossPercent * 100).toFixed(2)}%)`,
        evaluatedAt,
      };
    }

    // Only check max positions if we are OPENING a new one
    if (proposal.side === 'OPEN') {
      const openPositionsCount = await this.getOpenPositionsCount();
      if (openPositionsCount >= CONSTITUTION.maxOpenPositions) {
        return {
          status: 'REJECTED',
          rejectionReason: `Max open positions reached: ${openPositionsCount}/${CONSTITUTION.maxOpenPositions}`,
          evaluatedAt,
        };
      }
    }

    // 6. Correlation Guard (Phase C, Step 2)
    const bias = proposal.context?.trend_state; // 'bullish', 'bearish', 'neutral'
    
    if (proposal.side === 'OPEN' && bias && bias !== 'neutral' && CONSTITUTION.correlationGroups) {
      // Find which group this symbol belongs to
      let groupName: string | null = null;
      for (const [name, symbols] of Object.entries(CONSTITUTION.correlationGroups)) {
        if (symbols.includes(proposal.symbol)) {
          groupName = name;
          break;
        }
      }

      if (groupName && CONSTITUTION.riskLimits?.maxCorrelatedPositions) {
        // Count existing positions in this group with SAME bias
        const openPositions = await this.getOpenPositionMetadata();
        const groupSymbols = CONSTITUTION.correlationGroups[groupName];
        
        const correlatedCount = openPositions.filter(p => 
          groupSymbols.includes(p.symbol) && 
          p.bias === bias
        ).length;

        if (correlatedCount >= CONSTITUTION.riskLimits.maxCorrelatedPositions) {
          return { 
            status: 'REJECTED', 
            rejectionReason: `Correlation Limit Hit: ${correlatedCount} open ${bias} trades in ${groupName} (max: ${CONSTITUTION.riskLimits.maxCorrelatedPositions})`, 
            evaluatedAt 
          };
        }
      }
    }

    // Only check symbol concentration if we are OPENING a new position
    if (proposal.side === 'OPEN') {
      const symbolPositionCount = await this.getSymbolPositionCount(proposal.symbol);
      if (symbolPositionCount >= CONSTITUTION.maxConcentrationPerSymbol) {
        return {
          status: 'REJECTED',
          rejectionReason: `Max concentration per symbol reached for ${proposal.symbol}: ${symbolPositionCount}/${CONSTITUTION.maxConcentrationPerSymbol}`,
          evaluatedAt,
        };
      }
    }

    // 6. Context Checks
    // Skip context validation for CLOSE proposals (we're just closing existing positions)
    if (proposal.side === 'OPEN') {
      // VIX check: Reject if missing or too high
      if (proposal.context.vix === undefined || proposal.context.vix === null) {
        return {
          status: 'REJECTED',
          rejectionReason: 'VIX not available - system not warmed up or data fetch failed',
          evaluatedAt,
        };
      }
      
      if (proposal.context.vix > 28) {
        return {
          status: 'REJECTED',
          rejectionReason: `VIX too high: ${proposal.context.vix} (max: 28)`,
          evaluatedAt,
        };
      }

      if (proposal.context.flow_state === 'UNKNOWN') {
        return {
          status: 'REJECTED',
          rejectionReason: 'Flow state is UNKNOWN',
          evaluatedAt,
        };
      }
    }

    // All checks passed
    return {
      status: 'APPROVED',
      evaluatedAt,
    };
  }

  /**
   * Helper: Send a formatted alert to Discord (fire-and-forget)
   */
  private async sendDiscordAlert(title: string, description: string, color: number, fields: any[] = []) {
    if (!this.env.DISCORD_WEBHOOK_URL) return;
    
    // Fire and forget - don't await/block the trading thread
    fetch(this.env.DISCORD_WEBHOOK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        embeds: [{
          title,
          description,
          color,
          fields,
          timestamp: new Date().toISOString()
        }]
      })
    }).catch(err => console.error('Discord Alert Failed:', err));
  }

  /**
   * Process a trade proposal: Evaluate -> Execute if approved -> Record
   */
  async processProposal(request: Request): Promise<Response> {
    try {
      // Extract signature from headers
      const signature = extractSignatureFromHeaders(request.headers);
      if (!signature) {
        return new Response(JSON.stringify({ error: 'Missing signature' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      // Parse proposal
      const proposal: TradeProposal = await request.json();

      // Evaluate proposal
      const evaluation = await this.evaluateProposal(proposal, signature);

      // Record proposal in D1 (always, for audit trail)
      await this.env.DB.prepare(
        `INSERT INTO proposals (id, timestamp, symbol, strategy, side, quantity, context_json, status, rejection_reason)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
      )
        .bind(
          proposal.id,
          Math.floor(proposal.timestamp / 1000), // Convert ms to seconds for SQLite
          proposal.symbol,
          proposal.strategy,
          proposal.side,
          proposal.quantity,
          JSON.stringify(proposal.context),
          evaluation.status,
          evaluation.rejectionReason ?? null
        )
        .run();

      // If rejected, return error
      if (evaluation.status === 'REJECTED') {
        // ALERT: Send Rejection Notification
        this.sendDiscordAlert(
          '❌ Proposal Rejected',
          `**${proposal.symbol}** ${proposal.strategy}`,
          0xef4444, // Red
          [
            { name: 'Reason', value: evaluation.rejectionReason ?? 'Unknown', inline: false },
            { name: 'Side', value: proposal.side, inline: true },
            { name: 'Context', value: `VIX: ${proposal.context.vix}`, inline: true }
          ]
        );

        return new Response(
          JSON.stringify({
            status: 'REJECTED',
            reason: evaluation.rejectionReason,
          }),
          {
            status: 403,
            headers: { 'Content-Type': 'application/json' },
          }
        );
      }

      // If approved, execute trade
      try {
        let orderResult;

        // Supported Strategies for Auto-Execution (OPEN only)
        // CLOSE proposals can use any strategy (we're just closing existing positions)
        const supportedStrategies = ['CREDIT_SPREAD', 'IRON_CONDOR', 'IRON_BUTTERFLY', 'RATIO_SPREAD'];
        
        // For CLOSE proposals, skip strategy validation - just execute
        // For OPEN proposals, validate strategy is supported
        if (proposal.side === 'CLOSE' || supportedStrategies.includes(proposal.strategy)) {
          // Construct Multileg Order
          // The logic for Sides (OPEN/CLOSE) and Quantities is generic enough to work for all structure types
          // provided the Brain sends the correct "SELL/BUY" flags in the legs.
          
          const optionSymbols: string[] = [];
          const sides: string[] = [];
          const quantities: number[] = [];

          for (const leg of proposal.legs) {
            optionSymbols.push(leg.symbol);
            quantities.push(leg.quantity); // Supports unequal quantities for Ratio Spreads

            // MAP SIDES
            if (proposal.side === 'OPEN') {
              // Opening: SELL->sell_to_open, BUY->buy_to_open
              sides.push(leg.side === 'SELL' ? 'sell_to_open' : 'buy_to_open');
            } else {
              // Closing: SELL->buy_to_close, BUY->sell_to_close
              sides.push(leg.side === 'SELL' ? 'buy_to_close' : 'sell_to_close');
            }
          }

          // Order Type Logic
          // Default: Credit for Open, Debit for Close (Standard for Premium Selling)
          // Ratio Spreads might be debit, but we assume "Credit Backspreads" for now based on strategy goals.
          const orderType = proposal.side === 'OPEN' ? 'credit' : 'debit';

          orderResult = await this.tradierClient.placeOrder({
            class: 'multileg',
            symbol: proposal.symbol,
            type: orderType,
            price: proposal.price,
            duration: 'day',
            'option_symbol[]': optionSymbols,
            'side[]': sides,
            'quantity[]': quantities
          });

        } else {
          throw new Error(`Unsupported strategy for execution: ${proposal.strategy}`);
        }

        // Record Order
        await this.env.DB.prepare(
          `INSERT INTO orders (id, proposal_id, symbol, status, quantity, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, unixepoch('now'), unixepoch('now'))`
        )
          .bind(
            orderResult.order_id,
            proposal.id,
            proposal.symbol,
            'pending',
            proposal.quantity
          )
          .run();

        // ALERT: Send Trade Execution Notification
        this.sendDiscordAlert(
          '✅ Trade Executed',
          `**${proposal.symbol}** ${proposal.strategy}`,
          0x22c55e, // Green
          [
            { name: 'Action', value: `${proposal.side} (Limit $${proposal.price})`, inline: true },
            { name: 'Quantity', value: proposal.quantity.toString(), inline: true },
            { name: 'Order ID', value: `${orderResult.order_id}`, inline: false }
          ]
        );

        // Phase C: Track Position Metadata for Correlation Guard
        if (proposal.side === 'OPEN') {
          const bias = proposal.context?.trend_state || 'neutral';
          await this.savePositionMetadata(orderResult.order_id, {
            symbol: proposal.symbol,
            bias: bias,
            strategy: proposal.strategy,
          });
        } else if (proposal.side === 'CLOSE') {
          // For CLOSE, find the most recent OPEN order for this symbol/strategy
          // This matches the position being closed
          const orderLookup = await this.env.DB.prepare(
            `SELECT o.id 
             FROM orders o
             JOIN proposals p ON o.proposal_id = p.id
             WHERE p.symbol = ? AND p.strategy = ? AND p.side = 'OPEN'
             ORDER BY o.created_at DESC
             LIMIT 1`
          )
            .bind(proposal.symbol, proposal.strategy)
            .first<{ id: string }>();
          
          if (orderLookup) {
            await this.removePositionMetadata(orderLookup.id);
          }
        }

        return new Response(
          JSON.stringify({
            status: 'APPROVED',
            order_id: orderResult.order_id,
            proposal_id: proposal.id,
          }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }
        );
      } catch (executionError) {
        console.error('Order execution failed:', executionError);
        return new Response(
          JSON.stringify({
            status: 'APPROVED_BUT_EXECUTION_FAILED',
            error: executionError instanceof Error ? executionError.message : 'Unknown error',
          }),
          {
            status: 500,
            headers: { 'Content-Type': 'application/json' },
          }
        );
      }
    } catch (error) {
      console.error('Proposal processing error:', error);
      return new Response(
        JSON.stringify({
          error: error instanceof Error ? error.message : 'Unknown error',
        }),
        {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        }
      );
    }
  }

  /**
   * Lock the system (admin endpoint)
   */
  async lockSystemEndpoint(request: Request): Promise<Response> {
    try {
      const body = await request.json() as { reason?: string };
      await this.lockSystem(body.reason ?? 'Manual lock');
      return new Response(JSON.stringify({ status: 'LOCKED', reason: this.lockReason }), {
        headers: { 'Content-Type': 'application/json' },
      });
    } catch (error) {
      return new Response(
        JSON.stringify({ error: error instanceof Error ? error.message : 'Unknown error' }),
        { status: 500, headers: { 'Content-Type': 'application/json' } }
      );
    }
  }

  /**
   * Unlock the system (admin endpoint)
   */
  async unlockSystemEndpoint(request: Request): Promise<Response> {
    try {
      await this.unlockSystem();
      return new Response(JSON.stringify({ status: 'UNLOCKED', message: 'System restored to NORMAL' }), {
        headers: { 'Content-Type': 'application/json' },
      });
    } catch (error) {
      return new Response(
        JSON.stringify({ error: error instanceof Error ? error.message : 'Unknown error' }),
        { status: 500, headers: { 'Content-Type': 'application/json' } }
      );
    }
  }

  /**
   * Emergency close all positions
   */
  async emergencyCloseAll(): Promise<Response> {
    try {
      // Lock system first
      await this.lockSystem('Emergency liquidation initiated');

      // Get all open positions
      const positions = await this.env.DB.prepare(
        'SELECT DISTINCT symbol FROM positions WHERE quantity != 0'
      ).all<{ symbol: string }>();

      const results = [];
      for (const pos of positions.results ?? []) {
        try {
          // Cancel all pending orders for this symbol
          const orders = await this.env.DB.prepare(
            'SELECT id FROM orders WHERE symbol = ? AND status = "pending"'
          )
            .bind(pos.symbol)
            .all<{ id: string }>();

          for (const order of orders.results ?? []) {
            try {
              await this.tradierClient.cancelOrder(order.id);
            } catch (err) {
              console.error(`Failed to cancel order ${order.id}:`, err);
            }
          }

          // Place market close order (simplified - would need full position details)
          results.push({ symbol: pos.symbol, status: 'queued_for_close' });
        } catch (err) {
          results.push({ symbol: pos.symbol, status: 'error', error: err instanceof Error ? err.message : 'Unknown' });
        }
      }

      return new Response(
        JSON.stringify({
          status: 'LOCKED',
          liquidation_initiated: true,
          results,
        }),
        {
          headers: { 'Content-Type': 'application/json' },
        }
      );
    } catch (error) {
      return new Response(
        JSON.stringify({ error: error instanceof Error ? error.message : 'Unknown error' }),
        { status: 500, headers: { 'Content-Type': 'application/json' } }
      );
    }
  }

  /**
   * Get system status
   */
  /**
   * Receive heartbeat from Brain (indicates Brain is alive)
   */
  async receiveHeartbeat(request: Request): Promise<Response> {
    this.lastHeartbeat = Date.now();
    
    try {
      const body = await request.json() as any;
      // Store the rich state if provided (Phase C: Final Polish)
      if (body.state) {
        await this.state.storage.put('brainState', body.state);
      }
    } catch (e) {
      // Ignore parsing errors, heartbeat is still valid
    }

    return new Response(JSON.stringify({ status: 'OK' }), {
      headers: { 'Content-Type': 'application/json' },
    });
  }

  async getStatus(): Promise<SystemStatus> {
    await this.initializeState();

    // Sync account state from Tradier before reading positions (ensures dashboard shows real data)
    await this.syncAccountState();

    const equity = await this.getCurrentEquity();
    const dailyPnL = await this.getDailyLossPercent();
    const positionsCount = await this.getOpenPositionsCount();

    // 1. Fetch Active Positions
    const positionsResult = await this.env.DB.prepare(
      'SELECT symbol, quantity, cost_basis FROM positions WHERE quantity != 0 ORDER BY symbol ASC'
    ).all<{ symbol: string; quantity: number; cost_basis: number }>();

    const activePositions = (positionsResult.results || []).map((p: { symbol: string; quantity: number; cost_basis: number }) => ({
      symbol: p.symbol,
      quantity: p.quantity,
      cost_basis: p.cost_basis,
    }));

    // 2. Fetch Recent Proposals (Last 10, most recent first)
    type ProposalRow = {
      id: string;
      timestamp: number;
      symbol: string;
      strategy: string;
      side: string;
      status: 'APPROVED' | 'REJECTED';
      rejection_reason: string | null;
    };
    
    const proposalsResult = await this.env.DB.prepare(
      'SELECT id, timestamp, symbol, strategy, side, status, rejection_reason FROM proposals ORDER BY timestamp DESC LIMIT 10'
    ).all<ProposalRow>();

    const recentProposals = (proposalsResult.results || []).map((p: ProposalRow) => ({
      id: p.id,
      timestamp: p.timestamp, // Already in seconds from DB
      symbol: p.symbol,
      strategy: p.strategy,
      side: p.side,
      status: p.status,
      rejectionReason: p.rejection_reason,
    }));

    // Fetch stored Brain State (Phase C: Final Polish)
    const brainState = await this.state.storage.get('brainState');

    return {
      status: this.systemLocked ? 'LOCKED' : 'NORMAL',
      lockReason: this.lockReason,
      positionsCount,
      dailyPnL: -dailyPnL, // Convert loss to PnL (negative of loss)
      equity,
      lastUpdated: Date.now(),
      lastHeartbeat: this.lastHeartbeat,
      activePositions,
      recentProposals,
      brainState, // Add brain state to response
    };
  }

  /**
   * Enforce End-of-Day: Close all positions before market close
   */
  async enforceEOD(): Promise<void> {
    const currentTime = getCurrentTimeET();
    const [closeHour, closeMinute] = CONSTITUTION.forceEodCloseEt.split(':').map(Number);

    // Check if we're past the EOD close time
    const currentMinutes = currentTime.hour * 60 + currentTime.minute;
    const closeMinutes = closeHour * 60 + closeMinute;

    if (currentMinutes >= closeMinutes) {
      // Lock system and initiate close
      await this.lockSystem(`EOD close enforced at ${CONSTITUTION.forceEodCloseEt} ET`);
      // Trigger emergency close (simplified - would need proper position closing logic)
      console.log('EOD close triggered - locking system');
    }
  }

  /**
   * Generate and send End-of-Day P&L Report (Discord + Email)
   */
  async generateEndOfDayReport(): Promise<void> {
    try {
      await this.initializeState();
      await this.syncAccountState(); // Ensure fresh data

      // 1. Calculate Overall P&L
      const balances = await this.tradierClient.getBalances();
      const currentEquity = balances.total_equity;
      
      const startOfDayKey = 'startOfDayEquity';
      const startOfDayTimestampKey = 'startOfDayTimestamp';
      const storedStartEquity = await this.state.storage.get<number>(startOfDayKey);
      const storedTimestamp = await this.state.storage.get<number>(startOfDayTimestampKey);

      const now = Date.now();
      const oneDayMs = 24 * 60 * 60 * 1000;
      const todayStartMs = now - (now % oneDayMs); 

      let startOfDayEquity = storedStartEquity;
      if (!startOfDayEquity || !storedTimestamp || storedTimestamp < todayStartMs) {
        startOfDayEquity = currentEquity;
        await this.state.storage.put(startOfDayKey, startOfDayEquity);
        await this.state.storage.put(startOfDayTimestampKey, now);
      }
      this.startOfDayEquity = startOfDayEquity;

      const dayPnLDollars = currentEquity - startOfDayEquity;
      const dayPnLPercent = startOfDayEquity > 0 ? (dayPnLDollars / startOfDayEquity) * 100 : 0;

      // 2. Fetch Today's Activity
      const todayStartSeconds = Math.floor(todayStartMs / 1000);
      
      // Proposals Summary by Symbol
      const proposalsBySymbol = await this.env.DB.prepare(
        `SELECT symbol, 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'APPROVED' THEN 1 ELSE 0 END) as approved,
                SUM(CASE WHEN status = 'REJECTED' THEN 1 ELSE 0 END) as rejected,
                SUM(CASE WHEN side = 'OPEN' AND status = 'APPROVED' THEN 1 ELSE 0 END) as entries,
                SUM(CASE WHEN side = 'CLOSE' AND status = 'APPROVED' THEN 1 ELSE 0 END) as exits
         FROM proposals 
         WHERE timestamp >= ? 
         GROUP BY symbol 
         ORDER BY symbol`
      ).bind(todayStartSeconds).all<any>();

      // Overall Stats
      const overallStats = await this.env.DB.prepare(
        `SELECT 
                COUNT(*) as total_proposals,
                SUM(CASE WHEN status = 'APPROVED' THEN 1 ELSE 0 END) as approved,
                SUM(CASE WHEN status = 'REJECTED' THEN 1 ELSE 0 END) as rejected,
                SUM(CASE WHEN side = 'OPEN' AND status = 'APPROVED' THEN 1 ELSE 0 END) as total_entries,
                SUM(CASE WHEN side = 'CLOSE' AND status = 'APPROVED' THEN 1 ELSE 0 END) as total_exits
         FROM proposals 
         WHERE timestamp >= ?`
      ).bind(todayStartSeconds).first<any>();

      // Current Open Positions by Symbol
      const positionsBySymbol = await this.env.DB.prepare(
        `SELECT symbol, 
                COUNT(*) as position_count,
                SUM(ABS(quantity)) as total_quantity
         FROM positions 
         WHERE quantity != 0 
         GROUP BY symbol 
         ORDER BY symbol`
      ).all<any>();

      // Build Summary Text
      const symbolSummaries: string[] = [];
      if (proposalsBySymbol.results && proposalsBySymbol.results.length > 0) {
        for (const row of proposalsBySymbol.results) {
          const symbol = row.symbol;
          const entries = row.entries || 0;
          const exits = row.exits || 0;
          const approved = row.approved || 0;
          const rejected = row.rejected || 0;
          
          // Get position count for this symbol
          const posRow = positionsBySymbol.results?.find((p: any) => p.symbol === symbol);
          const openPositions = posRow?.position_count || 0;
          
          symbolSummaries.push(
            `**${symbol}**: ${entries} entries, ${exits} exits, ${openPositions} open | ${approved}✓/${rejected}✗`
          );
        }
      } else {
        symbolSummaries.push('No activity today.');
      }

      // 3. Send Discord Report
      const color = dayPnLDollars >= 0 ? 0x22c55e : 0xef4444;
      const pnlSign = dayPnLDollars >= 0 ? '+' : '';
      const pnlEmoji = dayPnLDollars >= 0 ? '📈' : '📉';

      if (this.env.DISCORD_WEBHOOK_URL) {
        await fetch(this.env.DISCORD_WEBHOOK_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            embeds: [{
              title: `${pnlEmoji} Gekko3 Daily Report`,
              color: color,
              fields: [
                { name: 'Overall P&L', value: `${pnlSign}$${dayPnLDollars.toFixed(2)} (${pnlSign}${dayPnLPercent.toFixed(2)}%)`, inline: true },
                { name: 'Equity', value: `$${currentEquity.toFixed(2)}`, inline: true },
                { name: 'Start Equity', value: `$${startOfDayEquity.toFixed(2)}`, inline: true },
                { name: 'Activity Summary', value: `${overallStats?.total_entries || 0} entries | ${overallStats?.total_exits || 0} exits | ${overallStats?.approved || 0} approved | ${overallStats?.rejected || 0} rejected`, inline: false },
                { name: 'By Symbol', value: symbolSummaries.join('\n') || 'No activity', inline: false }
              ],
              timestamp: new Date().toISOString()
            }]
          })
        });
        console.log('✅ EOD Discord Report Sent');
      }

      // 4. Send Email Report
      if (this.env.RESEND_API_KEY) {
        await this.sendEmailReport({
          dayPnLDollars,
          dayPnLPercent,
          currentEquity,
          startOfDayEquity,
          overallStats: overallStats || {},
          symbolSummaries,
          positionsBySymbol: positionsBySymbol.results || []
        });
        console.log('✅ EOD Email Report Sent');
      }
    } catch (e) {
      console.error('EOD Report Error:', e);
    }
  }

  /**
   * Send email report via Resend API
   */
  private async sendEmailReport(data: {
    dayPnLDollars: number;
    dayPnLPercent: number;
    currentEquity: number;
    startOfDayEquity: number;
    overallStats: any;
    symbolSummaries: string[];
    positionsBySymbol: any[];
  }): Promise<void> {
    const { dayPnLDollars, dayPnLPercent, currentEquity, startOfDayEquity, overallStats, symbolSummaries, positionsBySymbol } = data;
    const pnlSign = dayPnLDollars >= 0 ? '+' : '';
    const pnlColor = dayPnLDollars >= 0 ? '#22c55e' : '#ef4444';
    const dateStr = new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });

    const html = `
<!DOCTYPE html>
<html>
<head>
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; line-height: 1.6; color: #333; }
    .container { max-width: 600px; margin: 0 auto; padding: 20px; }
    .header { background: #0f172a; color: white; padding: 20px; border-radius: 8px 8px 0 0; }
    .content { background: #f8f9fa; padding: 20px; border-radius: 0 0 8px 8px; }
    .metric { background: white; padding: 15px; margin: 10px 0; border-radius: 6px; border-left: 4px solid #3b82f6; }
    .metric-label { font-size: 0.85rem; color: #666; text-transform: uppercase; margin-bottom: 5px; }
    .metric-value { font-size: 1.5rem; font-weight: bold; }
    .pnl-positive { color: #22c55e; }
    .pnl-negative { color: #ef4444; }
    .summary-table { width: 100%; border-collapse: collapse; margin: 15px 0; }
    .summary-table th, .summary-table td { padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }
    .summary-table th { background: #f1f5f9; font-weight: 600; }
    .footer { text-align: center; margin-top: 20px; color: #666; font-size: 0.85rem; }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1 style="margin: 0;">📊 Gekko3 Daily Report</h1>
      <p style="margin: 5px 0 0 0; opacity: 0.9;">${dateStr}</p>
    </div>
    <div class="content">
      <div class="metric">
        <div class="metric-label">Overall P&L</div>
        <div class="metric-value" style="color: ${pnlColor};">
          ${pnlSign}$${dayPnLDollars.toFixed(2)} (${pnlSign}${dayPnLPercent.toFixed(2)}%)
        </div>
      </div>

      <div class="metric">
        <div class="metric-label">Account Equity</div>
        <div class="metric-value">$${currentEquity.toFixed(2)}</div>
        <div style="font-size: 0.9rem; color: #666; margin-top: 5px;">
          Start of Day: $${startOfDayEquity.toFixed(2)}
        </div>
      </div>

      <div class="metric">
        <div class="metric-label">Activity Summary</div>
        <table class="summary-table">
          <tr>
            <th>Metric</th>
            <th>Count</th>
          </tr>
          <tr>
            <td>Total Entries</td>
            <td><strong>${overallStats.total_entries || 0}</strong></td>
          </tr>
          <tr>
            <td>Total Exits</td>
            <td><strong>${overallStats.total_exits || 0}</strong></td>
          </tr>
          <tr>
            <td>Approved Proposals</td>
            <td><strong>${overallStats.approved || 0}</strong></td>
          </tr>
          <tr>
            <td>Rejected Proposals</td>
            <td><strong>${overallStats.rejected || 0}</strong></td>
          </tr>
        </table>
      </div>

      <div class="metric">
        <div class="metric-label">By Symbol</div>
        ${symbolSummaries.length > 0 
          ? symbolSummaries.map(s => `<div style="padding: 8px 0; border-bottom: 1px solid #eee;">${s.replace(/\*\*/g, '<strong>').replace(/\*\*/g, '</strong>').replace(/✓/g, '✅').replace(/✗/g, '❌')}</div>`).join('')
          : '<div>No activity today.</div>'
        }
      </div>

      ${positionsBySymbol.length > 0 ? `
      <div class="metric">
        <div class="metric-label">Current Open Positions</div>
        <table class="summary-table">
          <tr>
            <th>Symbol</th>
            <th>Position Count</th>
            <th>Total Quantity</th>
          </tr>
          ${positionsBySymbol.map((p: any) => `
            <tr>
              <td><strong>${p.symbol}</strong></td>
              <td>${p.position_count}</td>
              <td>${p.total_quantity}</td>
            </tr>
          `).join('')}
        </table>
      </div>
      ` : ''}

      <div class="footer">
        <p>Automated report from Gekko3 Trading System</p>
      </div>
    </div>
  </div>
</body>
</html>
    `;

    const text = `
Gekko3 Daily Report - ${dateStr}

Overall P&L: ${pnlSign}$${dayPnLDollars.toFixed(2)} (${pnlSign}${dayPnLPercent.toFixed(2)}%)
Account Equity: $${currentEquity.toFixed(2)} (Start: $${startOfDayEquity.toFixed(2)})

Activity Summary:
- Total Entries: ${overallStats.total_entries || 0}
- Total Exits: ${overallStats.total_exits || 0}
- Approved: ${overallStats.approved || 0}
- Rejected: ${overallStats.rejected || 0}

By Symbol:
${symbolSummaries.map(s => s.replace(/\*\*/g, '').replace(/✓/g, '✓').replace(/✗/g, '✗')).join('\n') || 'No activity today.'}

${positionsBySymbol.length > 0 ? `\nCurrent Open Positions:\n${positionsBySymbol.map((p: any) => `${p.symbol}: ${p.position_count} positions, ${p.total_quantity} total quantity`).join('\n')}` : ''}

---
Automated report from Gekko3 Trading System
    `;

    try {
      const response = await fetch('https://api.resend.com/emails', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${this.env.RESEND_API_KEY}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          from: this.env.RESEND_FROM_EMAIL || 'Gekko3 <onboarding@resend.dev>',
          to: ['kevin.mcgovern@gmail.com'],
          subject: `Gekko3 Daily Report - ${dateStr} - ${pnlSign}$${dayPnLDollars.toFixed(2)} (${pnlSign}${dayPnLPercent.toFixed(2)}%)`,
          html: html,
          text: text,
        }),
      });

      if (!response.ok) {
        const error = await response.text();
        console.error('Resend API error:', error);
        throw new Error(`Failed to send email: ${response.status}`);
      }
    } catch (e) {
      console.error('Email send error:', e);
      throw e;
    }
  }

  /**
   * Fetch handler (entry point for requests)
   */
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === '/process' && request.method === 'POST') {
      return this.processProposal(request);
    }

    if (path === '/lock' && request.method === 'POST') {
      return this.lockSystemEndpoint(request);
    }

    if (path === '/unlock' && request.method === 'POST') {
      return this.unlockSystemEndpoint(request);
    }

    if (path === '/admin/calendar' && request.method === 'POST') {
      try {
        const body = await request.json() as { dates: string[] };
        await this.updateCalendar(body.dates);
        return new Response(JSON.stringify({ status: 'UPDATED', count: body.dates.length }), {
          headers: { 'Content-Type': 'application/json' },
        });
      } catch (e) {
        return new Response(JSON.stringify({ error: 'Invalid JSON' }), { 
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        });
      }
    }

    if (path === '/liquidate' && request.method === 'POST') {
      return this.emergencyCloseAll();
    }

    if (path === '/status' && request.method === 'GET') {
      const status = await this.getStatus();
      return new Response(JSON.stringify(status), {
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // Handle heartbeat from Brain
    if (path === '/heartbeat' && request.method === 'POST') {
      return this.receiveHeartbeat(request);
    }

    // Handle alarm trigger (for scheduled events)
    if (path === '/alarm' && request.method === 'POST') {
      await this.alarm();
      return new Response(JSON.stringify({ status: 'alarm_triggered' }), {
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // Handle EOD report trigger (from scheduled cron)
    if (path === '/scheduler/eod-report') {
      // This is an internal route, so we don't need to check method
      await this.generateEndOfDayReport();
      return new Response(JSON.stringify({ status: 'eod_report_generated' }), {
        headers: { 'Content-Type': 'application/json' },
      });
    }

    return new Response('Not Found', { status: 404 });
  }

  /**
   * Alarm handler (for scheduled tasks)
   */
  async alarm(): Promise<void> {
    await this.enforceEOD();
    // Schedule next check in 1 minute
    await this.state.storage.setAlarm(Date.now() + 60000);
  }
}

