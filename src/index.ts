/**
 * Gekko3 Main Worker
 * API Router: Routes requests to the Gatekeeper Durable Object
 */

import { GatekeeperDO } from './GatekeeperDO';
import type { Env } from './config';

// UI HTML (Pro Dashboard - Phase C: Final Polish)
const UI_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gekko3 Remote Command</title>
    <style>
        :root { --bg: #0f172a; --card: #1e293b; --text: #e2e8f0; --green: #22c55e; --red: #ef4444; --orange: #f59e0b; --blue: #3b82f6; --border: rgba(255,255,255,0.1); }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 20px; display: flex; justify-content: center; }
        .container { width: 100%; max-width: 900px; display: flex; flex-direction: column; gap: 20px; }
        
        .card { background: var(--card); padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2); border: 1px solid var(--border); }
        
        /* Headers */
        .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 15px; margin-bottom: 20px; }
        h1 { margin: 0; font-size: 1.4rem; letter-spacing: -0.5px; }
        .subtitle { font-size: 0.8rem; opacity: 0.6; font-weight: 400; margin-left: 10px; }
        
        /* Status Badges */
        .status-badge { padding: 4px 12px; border-radius: 99px; font-weight: bold; font-size: 0.75rem; letter-spacing: 0.05em; text-transform: uppercase; }
        .status-normal { background: rgba(34, 197, 94, 0.15); color: var(--green); border: 1px solid rgba(34, 197, 94, 0.3); }
        .status-locked { background: rgba(239, 68, 68, 0.15); color: var(--red); border: 1px solid rgba(239, 68, 68, 0.3); }
        
        /* Metrics Grid */
        .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; }
        @media (max-width: 600px) { .grid { grid-template-columns: 1fr 1fr; } }
        
        .metric { display: flex; flex-direction: column; background: rgba(0,0,0,0.2); padding: 12px; border-radius: 8px; }
        .label { font-size: 0.7rem; opacity: 0.6; text-transform: uppercase; margin-bottom: 4px; }
        .value { font-size: 1.2rem; font-weight: 700; }
        .sub-value { font-size: 0.8rem; opacity: 0.8; }
        
        /* Tables */
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.9rem; }
        th { text-align: left; opacity: 0.6; font-weight: 600; padding: 8px; border-bottom: 1px solid var(--border); font-size: 0.75rem; text-transform: uppercase; }
        td { padding: 10px 8px; border-bottom: 1px solid var(--border); }
        tr:last-child td { border-bottom: none; }
        
        /* Utility Colors */
        .text-green { color: var(--green); }
        .text-red { color: var(--red); }
        .text-blue { color: var(--blue); }
        .text-orange { color: var(--orange); }
        
        /* Controls */
        .controls { display: flex; gap: 10px; margin-top: 20px; }
        button { flex: 1; padding: 12px; border: none; border-radius: 8px; font-weight: bold; cursor: pointer; transition: 0.2s; }
        .btn-lock { background: rgba(239, 68, 68, 0.2); color: var(--red); border: 1px solid var(--red); }
        .btn-unlock { background: rgba(34, 197, 94, 0.2); color: var(--green); border: 1px solid var(--green); }
        .btn-lock:hover, .btn-unlock:hover { opacity: 0.8; }
        
        .hidden { display: none; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <div class="header">
                <div style="display:flex; align-items:baseline;">
                    <h1>Gekko3 <span style="color:var(--blue)">Remote</span></h1>
                    <span class="subtitle">Cloudflare Edge Node</span>
                </div>
                <span id="systemStatus" class="status-badge status-normal">CONNECTING...</span>
            </div>
            
            <div class="grid">
                <div class="metric">
                    <span class="label">Market Regime</span>
                    <span id="regime" class="value" style="color: var(--blue)">--</span>
                </div>
                <div class="metric">
                    <span class="label">Portfolio Delta</span>
                    <span id="delta" class="value">--</span>
                </div>
                <div class="metric">
                    <span class="label">Day P&L</span>
                    <span id="pnl" class="value">--</span>
                </div>
                <div class="metric">
                    <span class="label">Brain Heartbeat</span>
                    <span id="heartbeat" class="value">--</span>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="header" style="margin-bottom:0; border-bottom:none;">
                <h1>Asset Surveillance</h1>
            </div>
            <div style="overflow-x: auto;">
                <table id="marketTable">
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>Price</th>
                            <th>Trend</th>
                            <th>RSI (14)</th>
                            <th>IV Rank</th>
                            <th>Signal</th>
                        </tr>
                    </thead>
                    <tbody id="marketBody">
                        <tr><td colspan="6" style="text-align:center; padding:20px; opacity:0.5;">Waiting for Brain Data...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <div class="card">
            <div class="label" style="margin-bottom:10px;"> EMERGENCY OVERRIDE </div>
            <div class="controls">
                <button id="btnLock" class="btn-lock" onclick="toggleLock(true)">🔒 LOCK SYSTEM (HALT TRADING)</button>
                <button id="btnUnlock" class="btn-unlock hidden" onclick="toggleLock(false)">🔓 UNLOCK SYSTEM</button>
            </div>
        </div>
    </div>

    <script>
        const API_BASE = '/v1';
        
        async function updateStatus() {
            try {
                const res = await fetch(API_BASE + '/status');
                const data = await res.json();
                const bs = data.brainState || {};
                const greeks = bs.greeks || {};
                const market = bs.market || {}; // The new data

                // 1. System Status
                const statusBadge = document.getElementById('systemStatus');
                const btnLock = document.getElementById('btnLock');
                const btnUnlock = document.getElementById('btnUnlock');
                
                if (data.status === 'LOCKED') {
                    statusBadge.textContent = 'LOCKED: ' + (data.lockReason || 'Manual');
                    statusBadge.className = 'status-badge status-locked';
                    btnLock.classList.add('hidden');
                    btnUnlock.classList.remove('hidden');
                } else {
                    statusBadge.textContent = 'SYSTEM ACTIVE';
                    statusBadge.className = 'status-badge status-normal';
                    btnLock.classList.remove('hidden');
                    btnUnlock.classList.add('hidden');
                }

                // 2. Metrics
                document.getElementById('regime').textContent = bs.regime || 'WAITING...';
                
                const delta = greeks.delta || 0;
                const deltaEl = document.getElementById('delta');
                deltaEl.textContent = (delta > 0 ? '+' : '') + delta.toFixed(1);
                deltaEl.style.color = Math.abs(delta) > 50 ? 'var(--orange)' : 'var(--text)';

                const pnl = data.dailyPnL || 0;
                const pnlEl = document.getElementById('pnl');
                pnlEl.textContent = (pnl >= 0 ? '+' : '') + (pnl * 100).toFixed(2) + '%';
                pnlEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';

                // 3. Heartbeat
                const lastHeartbeat = data.lastHeartbeat || 0;
                const secondsAgo = lastHeartbeat > 0 ? Math.floor((Date.now() - lastHeartbeat) / 1000) : 999;
                const hbEl = document.getElementById('heartbeat');
                if (secondsAgo < 60) {
                    hbEl.textContent = '🟢 Online';
                    hbEl.style.color = 'var(--green)';
                } else {
                    hbEl.textContent = '🔴 ' + secondsAgo + 's Ago';
                    hbEl.style.color = 'var(--red)';
                }

                // 4. Market Table (Dynamic Rendering)
                const tbody = document.getElementById('marketBody');
                if (Object.keys(market).length > 0) {
                    tbody.innerHTML = ''; // Clear loading
                    for (const [sym, m] of Object.entries(market)) {
                        const row = document.createElement('tr');
                        
                        // Trend Color
                        let trendColor = 'var(--text)';
                        if (m.trend === 'UPTREND') trendColor = 'var(--green)';
                        if (m.trend === 'DOWNTREND') trendColor = 'var(--red)';

                        // RSI Color
                        let rsiColor = 'var(--text)';
                        if (m.rsi > 70) rsiColor = 'var(--red)';
                        if (m.rsi < 30) rsiColor = 'var(--green)';

                        // IV Rank Color
                        let ivColor = 'var(--text)';
                        if (m.iv_rank < 20) ivColor = 'var(--green)'; // Buy Premium
                        if (m.iv_rank > 50) ivColor = 'var(--orange)'; // Sell Premium

                        row.innerHTML = '<td style="font-weight:bold;">' + sym + '</td>' +
                            '<td>$' + m.price.toFixed(2) + '</td>' +
                            '<td style="color:' + trendColor + '">' + m.trend + '</td>' +
                            '<td style="color:' + rsiColor + '">' + m.rsi.toFixed(1) + '</td>' +
                            '<td style="color:' + ivColor + '">' + m.iv_rank.toFixed(0) + '%</td>' +
                            '<td>' + (m.active_signal ? '<span class="status-badge status-normal" style="font-size:0.6rem">SIGNAL</span>' : '-') + '</td>';
                        tbody.appendChild(row);
                    }
                }

            } catch (e) { console.error(e); }
        }

        async function toggleLock(lock) {
            const endpoint = lock ? 'admin/lock' : 'admin/unlock';
            const reason = lock ? prompt("Reason:", "Manual") : null;
            if (lock && !reason) return;
            await fetch(API_BASE + '/' + endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ reason })
            });
            updateStatus();
        }

        setInterval(updateStatus, 2000);
        updateStatus();
    </script>
</body>
</html>
`;

/**
 * Get or create Gatekeeper Durable Object instance
 */
function getGatekeeperDO(env: Env): DurableObjectStub<GatekeeperDO> {
  const id = env.GATEKEEPER_DO.idFromName('singleton');
  return env.GATEKEEPER_DO.get(id);
}

/**
 * Handle proposal submission
 */
async function handleProposal(request: Request, env: Env): Promise<Response> {
  const stub = getGatekeeperDO(env);
  const url = new URL(request.url);
  url.pathname = '/process';
  return stub.fetch(new Request(url.toString(), {
    method: 'POST',
    headers: request.headers,
    body: request.body,
  }));
}

/**
 * Handle admin lock
 */
async function handleAdminLock(request: Request, env: Env): Promise<Response> {
  const stub = getGatekeeperDO(env);
  const url = new URL(request.url);
  url.pathname = '/lock';
  return stub.fetch(new Request(url.toString(), {
    method: 'POST',
    headers: request.headers,
    body: request.body,
  }));
}

/**
 * Handle admin unlock
 */
async function handleAdminUnlock(request: Request, env: Env): Promise<Response> {
  const stub = getGatekeeperDO(env);
  const url = new URL(request.url);
  url.pathname = '/unlock';
  return stub.fetch(new Request(url.toString(), {
    method: 'POST',
    headers: request.headers,
    body: request.body,
  }));
}

/**
 * Handle emergency liquidation
 */
async function handleEmergencyLiquidate(request: Request, env: Env): Promise<Response> {
  const stub = getGatekeeperDO(env);
  const url = new URL(request.url);
  url.pathname = '/liquidate';
  return stub.fetch(new Request(url.toString(), {
    method: 'POST',
    headers: request.headers,
    body: request.body,
  }));
}

/**
 * Handle status check
 */
async function handleStatus(request: Request, env: Env): Promise<Response> {
  const stub = getGatekeeperDO(env);
  const url = new URL(request.url);
  url.pathname = '/status';
  return stub.fetch(new Request(url.toString(), {
    method: 'GET',
    headers: request.headers,
  }));
}

/**
 * Handle heartbeat from Brain
 */
async function handleHeartbeat(request: Request, env: Env): Promise<Response> {
  const stub = getGatekeeperDO(env);
  const url = new URL(request.url);
  url.pathname = '/heartbeat';
  return stub.fetch(new Request(url.toString(), {
    method: 'POST',
    headers: request.headers,
    body: request.body,
  }));
}

/**
 * Main Worker export
 */
export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    // CORS headers (adjust as needed for production)
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Signature',
    };

    // Handle OPTIONS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    try {
      let response: Response;

      // Serve UI on root path
      if (path === '/' || path === '/dashboard') {
        return new Response(UI_HTML, {
          headers: { 'Content-Type': 'text/html' },
        });
      }

      // Route requests
      if (path === '/v1/proposal' && request.method === 'POST') {
        response = await handleProposal(request, env);
      } else if (path === '/v1/admin/lock' && request.method === 'POST') {
        response = await handleAdminLock(request, env);
      } else if (path === '/v1/admin/unlock' && request.method === 'POST') {
        response = await handleAdminUnlock(request, env);
      } else if (path === '/v1/admin/liquidate' && request.method === 'POST') {
        response = await handleEmergencyLiquidate(request, env);
      } else if (path === '/v1/status' && request.method === 'GET') {
        response = await handleStatus(request, env);
      } else if (path === '/v1/heartbeat' && request.method === 'POST') {
        response = await handleHeartbeat(request, env);
      } else {
        response = new Response(JSON.stringify({ error: 'Not Found' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json', ...corsHeaders },
        });
      }

      // Create new response with CORS headers (responses are immutable)
      const responseBody = await response.clone().text();
      return new Response(responseBody, {
        status: response.status,
        statusText: response.statusText,
        headers: {
          ...Object.fromEntries(response.headers.entries()),
          ...corsHeaders,
        },
      });
    } catch (error) {
      console.error('Worker error:', error);
      return new Response(
        JSON.stringify({
          error: 'Internal Server Error',
          message: error instanceof Error ? error.message : 'Unknown error',
        }),
        {
          status: 500,
          headers: { 'Content-Type': 'application/json', ...corsHeaders },
        }
      );
    }
  },

  /**
   * Scheduled event handler (cron trigger)
   * Runs EOD reporting at 4:30 PM ET (21:30 UTC)
   * Configure cron in wrangler.toml
   */
  async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    const stub = getGatekeeperDO(env);
    
    // Route to EOD report generation
    ctx.waitUntil(
      stub.fetch(new Request('http://internal/scheduler/eod-report', { method: 'POST' })).catch((error) => {
        console.error('Scheduled EOD report error:', error);
      })
    );
  },
};

// Export the Durable Object class for wrangler.toml
export { GatekeeperDO };

