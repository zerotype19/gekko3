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
    <title>Gekko3 Pro Terminal</title>
    <style>
        :root { --bg: #0f172a; --card: #1e293b; --text: #e2e8f0; --green: #22c55e; --red: #ef4444; --orange: #f59e0b; --blue: #3b82f6; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 20px; display: flex; justify-content: center; }
        .container { width: 100%; max-width: 800px; display: flex; flex-direction: column; gap: 20px; }
        .card { background: var(--card); padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2); }
        .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 15px; margin-bottom: 20px; }
        h1 { margin: 0; font-size: 1.5rem; letter-spacing: -0.5px; }
        .status-badge { padding: 4px 12px; border-radius: 99px; font-weight: bold; font-size: 0.8rem; letter-spacing: 0.05em; }
        .status-normal { background: rgba(34, 197, 94, 0.15); color: var(--green); border: 1px solid rgba(34, 197, 94, 0.3); }
        .status-locked { background: rgba(239, 68, 68, 0.15); color: var(--red); border: 1px solid rgba(239, 68, 68, 0.3); }
        
        .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; }
        .metric { display: flex; flex-direction: column; background: rgba(0,0,0,0.2); padding: 12px; border-radius: 8px; }
        .label { font-size: 0.7rem; opacity: 0.6; text-transform: uppercase; margin-bottom: 4px; }
        .value { font-size: 1.4rem; font-weight: 700; }
        .sub-value { font-size: 0.85rem; opacity: 0.8; margin-top: 4px; }

        .section-title { font-size: 0.9rem; opacity: 0.5; text-transform: uppercase; margin-bottom: 10px; font-weight: 600; }
        
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
                <h1>Gekko3 <span style="opacity:0.5; font-weight:400;">Pro</span></h1>
                <span id="systemStatus" class="status-badge status-normal">LOADING...</span>
            </div>
            
            <div class="section-title">Account & Risk</div>
            <div class="grid">
                <div class="metric">
                    <span class="label">Equity</span>
                    <span id="equity" class="value">--</span>
                </div>
                <div class="metric">
                    <span class="label">Day P&L</span>
                    <span id="pnl" class="value">--</span>
                </div>
                <div class="metric">
                    <span class="label">Portfolio Delta</span>
                    <span id="delta" class="value">--</span>
                </div>
                <div class="metric">
                    <span class="label">Portfolio Theta</span>
                    <span id="theta" class="value" style="color: var(--green)">--</span>
                </div>
            </div>

            <div class="section-title" style="margin-top: 20px;">Market Intelligence</div>
            <div class="grid">
                <div class="metric" style="grid-column: span 2;">
                    <span class="label">Market Regime</span>
                    <span id="regime" class="value" style="color: var(--blue)">WAITING...</span>
                </div>
                <div class="metric">
                    <span class="label">SPY IV Rank</span>
                    <span id="ivRank" class="value">--</span>
                </div>
                <div class="metric">
                    <span class="label">Brain Heartbeat</span>
                    <span id="heartbeat" class="value">--</span>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="section-title">Emergency Override</div>
            <div class="controls">
                <button id="btnLock" class="btn-lock" onclick="toggleLock(true)">🔒 LOCK SYSTEM</button>
                <button id="btnUnlock" class="btn-unlock hidden" onclick="toggleLock(false)">🔓 UNLOCK SYSTEM</button>
            </div>
        </div>
    </div>

    <script>
        const API_BASE = '/v1';
        
        async function updateStatus() {
            try {
                const res = await fetch(\`\${API_BASE}/status\`);
                const data = await res.json();
                const bs = data.brainState || {};
                const greeks = bs.greeks || {};

                // 1. Status Badge
                const statusBadge = document.getElementById('systemStatus');
                const btnLock = document.getElementById('btnLock');
                const btnUnlock = document.getElementById('btnUnlock');
                
                if (data.status === 'LOCKED') {
                    statusBadge.textContent = \`LOCKED: \${data.lockReason || 'Manual'}\`;
                    statusBadge.className = 'status-badge status-locked';
                    btnLock.classList.add('hidden');
                    btnUnlock.classList.remove('hidden');
                } else {
                    statusBadge.textContent = 'SYSTEM ACTIVE';
                    statusBadge.className = 'status-badge status-normal';
                    btnLock.classList.remove('hidden');
                    btnUnlock.classList.add('hidden');
                }

                // 2. Account Metrics
                document.getElementById('equity').textContent = '$' + (data.equity || 0).toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0});
                
                const pnl = data.dailyPnL || 0;
                const pnlEl = document.getElementById('pnl');
                pnlEl.textContent = (pnl >= 0 ? '+' : '') + (pnl * 100).toFixed(2) + '%';
                pnlEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';

                // 3. Brain Metrics (The New Stuff)
                document.getElementById('regime').textContent = bs.regime || 'WAITING...';
                document.getElementById('ivRank').textContent = bs.iv_rank_spy ? Math.round(bs.iv_rank_spy) : '--';
                
                const delta = greeks.delta || 0;
                const deltaEl = document.getElementById('delta');
                deltaEl.textContent = (delta > 0 ? '+' : '') + delta.toFixed(1);
                deltaEl.style.color = Math.abs(delta) > 50 ? 'var(--orange)' : 'var(--text)'; // Warn if high delta

                document.getElementById('theta').textContent = '+' + (greeks.theta || 0).toFixed(1);

                // 4. Heartbeat
                const lastHeartbeat = data.lastHeartbeat || 0;
                const secondsAgo = lastHeartbeat > 0 ? Math.floor((Date.now() - lastHeartbeat) / 1000) : 999;
                const hbEl = document.getElementById('heartbeat');
                
                if (secondsAgo < 60) {
                    hbEl.textContent = '🟢 Online';
                    hbEl.style.color = 'var(--green)';
                } else {
                    hbEl.textContent = \`🔴 \${secondsAgo}s Ago\`;
                    hbEl.style.color = 'var(--red)';
                }

            } catch (e) { console.error(e); }
        }

        async function toggleLock(lock) {
            const endpoint = lock ? 'admin/lock' : 'admin/unlock';
            const reason = lock ? prompt("Reason:", "Manual") : null;
            if (lock && !reason) return;
            await fetch(\`\${API_BASE}/\${endpoint}\`, {
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

