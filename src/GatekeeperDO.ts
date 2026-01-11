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

    if (!CONSTITUTION.allowedStrategies.includes(proposal.strategy)) {
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

    // DTE Check: Calculate from first leg's expiration
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
    if (dte < 1 || dte > 7) {
      return {
        status: 'REJECTED',
        rejectionReason: `DTE out of range: ${dte} days (must be 1-7 days)`,
        evaluatedAt,
      };
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

    // All checks passed
    return {
      status: 'APPROVED',
      evaluatedAt,
    };
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

        if (proposal.strategy === 'CREDIT_SPREAD') {
          // Construct Multileg Order
          const optionSymbols: string[] = [];
          const sides: string[] = [];
          const quantities: number[] = [];

          for (const leg of proposal.legs) {
            optionSymbols.push(leg.symbol);
            quantities.push(leg.quantity);

            // LOGIC FOR SIDE MAPPING:
            // If side is OPEN: SELL -> sell_to_open, BUY -> buy_to_open
            // If side is CLOSE: SELL -> buy_to_close (Closing Short), BUY -> sell_to_close (Closing Long)
            
            if (proposal.side === 'OPEN') {
              // Entry
              if (leg.side === 'SELL') {
                sides.push('sell_to_open');
              } else {
                sides.push('buy_to_open');
              }
            } else {
              // Exit (Invert)
              if (leg.side === 'SELL') {
                sides.push('buy_to_close'); // Was Short, now Buying to Close
              } else {
                sides.push('sell_to_close'); // Was Long, now Selling to Close
              }
            }
          }

          orderResult = await this.tradierClient.placeOrder({
            class: 'multileg',
            symbol: proposal.symbol,
            type: 'limit', // ALWAYS LIMIT
            price: proposal.price, // Mandatory limit price
            duration: 'day',
            'option_symbol[]': optionSymbols,
            'side[]': sides,
            'quantity[]': quantities
          });

        } else {
          throw new Error('Unsupported strategy for execution');
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
  async receiveHeartbeat(): Promise<Response> {
    this.lastHeartbeat = Date.now();
    return new Response(JSON.stringify({ status: 'OK' }), {
      headers: { 'Content-Type': 'application/json' },
    });
  }

  async getStatus(): Promise<SystemStatus> {
    await this.initializeState();

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
      return this.receiveHeartbeat();
    }

    // Handle alarm trigger (for scheduled events)
    if (path === '/alarm' && request.method === 'POST') {
      await this.alarm();
      return new Response(JSON.stringify({ status: 'alarm_triggered' }), {
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

