/**
 * Gekko3 Main Worker
 * API Router: Routes requests to the Gatekeeper Durable Object
 */

import { GatekeeperDO } from './GatekeeperDO';
import type { Env } from './config';

// UI HTML (embedded for simplicity)
const UI_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gekko3 Command Center</title>
    <style>
        :root { --bg: #0f172a; --card: #1e293b; --text: #e2e8f0; --green: #22c55e; --red: #ef4444; --blue: #3b82f6; }
        body { font-family: system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 20px; display: flex; justify-content: center; }
        .container { width: 100%; max-width: 600px; display: flex; flex-direction: column; gap: 20px; }
        .card { background: var(--card); padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        h1 { margin: 0; font-size: 1.5rem; }
        .status-badge { padding: 4px 12px; border-radius: 99px; font-weight: bold; font-size: 0.875rem; }
        .status-normal { background: rgba(34, 197, 94, 0.2); color: var(--green); }
        .status-locked { background: rgba(239, 68, 68, 0.2); color: var(--red); }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        .metric { display: flex; flex-direction: column; }
        .label { font-size: 0.75rem; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.05em; }
        .value { font-size: 1.5rem; font-weight: bold; }
        .controls { display: flex; gap: 10px; }
        button { flex: 1; padding: 12px; border: none; border-radius: 8px; font-weight: bold; cursor: pointer; transition: 0.2s; }
        .btn-lock { background: var(--red); color: white; }
        .btn-unlock { background: var(--green); color: white; }
        .btn-lock:hover, .btn-unlock:hover { opacity: 0.9; }
        .hidden { display: none; }
        .lock-reason { margin-top: 10px; padding: 10px; background: rgba(239, 68, 68, 0.1); border-radius: 6px; font-size: 0.875rem; }
        .log-box { background: #000; padding: 10px; border-radius: 8px; font-family: monospace; font-size: 0.8rem; height: 200px; overflow-y: auto; color: #0f0; }
        table { width: 100%; border-collapse: collapse; }
        th { text-align: left; opacity: 0.6; border-bottom: 1px solid #333; padding: 8px 0; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }
        td { padding: 8px 0; border-bottom: 1px solid #222; }
        h3 { margin-top: 0; margin-bottom: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <div class="header">
                <h1>Gekko3 Command Center</h1>
                <span id="systemStatus" class="status-badge status-normal">LOADING...</span>
            </div>
            <div id="lockReason" class="lock-reason hidden"></div>
            <div class="grid">
                <div class="metric">
                    <span class="label">Positions</span>
                    <span id="posCount" class="value">--</span>
                </div>
                <div class="metric">
                    <span class="label">Daily PnL</span>
                    <span id="pnl" class="value">--</span>
                </div>
                <div class="metric">
                    <span class="label">Heartbeat</span>
                    <span id="heartbeat" class="value">--</span>
                </div>
                <div class="metric">
                    <span class="label">Equity</span>
                    <span id="equity" class="value">--</span>
                </div>
            </div>
        </div>

        <div class="card">
            <h3 style="margin-top: 0;">Emergency Controls</h3>
            <div class="controls">
                <button id="btnLock" class="btn-lock" onclick="toggleLock(true)">🔒 LOCK SYSTEM</button>
                <button id="btnUnlock" class="btn-unlock hidden" onclick="toggleLock(false)">🔓 UNLOCK SYSTEM</button>
            </div>
            <p style="font-size: 0.8rem; opacity: 0.6; margin-top: 10px;">Locking prevents all new trades immediately.</p>
        </div>

        <div class="card">
            <h3>Active Positions</h3>
            <div id="positionsList" style="margin-top: 10px; font-size: 0.9rem;">
                <div style="opacity: 0.5; font-style: italic;">No active positions</div>
            </div>
        </div>

        <div class="card">
            <h3>Recent Activity (Brain Log)</h3>
            <div class="log-box" id="activityLog">
                <div>Loading...</div>
            </div>
        </div>
    </div>

    <script>
        const API_BASE = '/v1';
        
        async function updateStatus() {
            try {
                const res = await fetch(\`\${API_BASE}/status\`);
                const data = await res.json();
                
                const statusBadge = document.getElementById('systemStatus');
                const btnLock = document.getElementById('btnLock');
                const btnUnlock = document.getElementById('btnUnlock');
                const lockReasonEl = document.getElementById('lockReason');
                
                if (data.status === 'LOCKED') {
                    statusBadge.textContent = 'SYSTEM LOCKED';
                    statusBadge.className = 'status-badge status-locked';
                    btnLock.classList.add('hidden');
                    btnUnlock.classList.remove('hidden');
                    if (data.lockReason) {
                        lockReasonEl.textContent = \`Lock Reason: \${data.lockReason}\`;
                        lockReasonEl.classList.remove('hidden');
                    } else {
                        lockReasonEl.classList.add('hidden');
                    }
                } else {
                    statusBadge.textContent = 'OPERATIONAL';
                    statusBadge.className = 'status-badge status-normal';
                    btnLock.classList.remove('hidden');
                    btnUnlock.classList.add('hidden');
                    lockReasonEl.classList.add('hidden');
                }

                document.getElementById('posCount').textContent = data.positionsCount || 0;
                const pnlPercent = ((data.dailyPnL || 0) * 100).toFixed(2);
                document.getElementById('pnl').textContent = (data.dailyPnL >= 0 ? '+' : '') + pnlPercent + '%';
                document.getElementById('pnl').style.color = data.dailyPnL >= 0 ? 'var(--green)' : 'var(--red)';
                document.getElementById('equity').textContent = '$' + (data.equity || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});

                const lastHeartbeat = data.lastHeartbeat || 0;
                const secondsAgo = lastHeartbeat > 0 ? Math.floor((Date.now() - lastHeartbeat) / 1000) : 999999;
                const hbEl = document.getElementById('heartbeat');
                
                if (lastHeartbeat === 0) {
                    hbEl.textContent = '--';
                    hbEl.style.color = 'var(--text)';
                } else if (secondsAgo < 60) {
                    hbEl.textContent = '🟢 Online';
                    hbEl.style.color = 'var(--green)';
                } else if (secondsAgo < 300) {
                    hbEl.textContent = \`⚠️ \${secondsAgo}s Ago\`;
                    hbEl.style.color = 'orange';
                } else {
                    hbEl.textContent = '🔴 Offline';
                    hbEl.style.color = 'var(--red)';
                }

                // RENDER POSITIONS
                const posContainer = document.getElementById('positionsList');
                if (data.activePositions && data.activePositions.length > 0) {
                    posContainer.innerHTML = \`<table>
                        <tr><th>Symbol</th><th>Quantity</th><th>Cost Basis</th></tr>
                        \${data.activePositions.map(p => \`
                            <tr>
                                <td style="font-weight: bold; color: var(--blue);">\${p.symbol}</td>
                                <td>\${p.quantity}</td>
                                <td>\${'$' + p.cost_basis.toFixed(2)}</td>
                            </tr>
                        \`).join('')}
                    </table>\`;
                } else {
                    posContainer.innerHTML = '<div style="opacity: 0.5; font-style: italic;">No active positions</div>';
                }

                // RENDER ACTIVITY LOG
                const logContainer = document.getElementById('activityLog');
                if (data.recentProposals && data.recentProposals.length > 0) {
                    logContainer.innerHTML = data.recentProposals.map(p => {
                        // Format timestamp (stored as seconds, convert to ms for JS Date)
                        const date = new Date(p.timestamp * 1000);
                        const time = date.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
                        
                        const statusColor = p.status === 'APPROVED' ? 'var(--green)' : 'var(--red)';
                        const reason = p.rejectionReason ? \` <span style="opacity: 0.6;">- \${p.rejectionReason}</span>\` : '';
                        
                        return \`<div style="margin-bottom: 6px; border-bottom: 1px solid #222; padding-bottom: 4px;">
                            <span style="opacity: 0.5; margin-right: 8px;">[\${time}]</span>
                            <span style="font-weight: bold; color: var(--text);">\${p.symbol}</span>
                            <span style="font-size: 0.85em; opacity: 0.8; margin: 0 5px;">\${p.side}</span>
                            <span style="font-weight: bold; color: \${statusColor};">\${p.status}</span>
                            \${reason}
                        </div>\`;
                    }).join('');
                } else {
                    logContainer.innerHTML = '<div style="opacity: 0.5; font-style: italic;">No recent activity</div>';
                }

            } catch (e) {
                console.error('Status update error:', e);
                document.getElementById('systemStatus').textContent = 'ERROR';
                document.getElementById('systemStatus').className = 'status-badge status-locked';
            }
        }

        async function toggleLock(lock) {
            const endpoint = lock ? 'admin/lock' : 'admin/unlock';
            const reason = lock ? prompt("Enter reason for locking (optional):", "Manual Override") : null;
            
            try {
                const res = await fetch(\`\${API_BASE}/\${endpoint}\`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(reason ? { reason } : {})
                });
                if (!res.ok) {
                    alert('Failed to ' + (lock ? 'lock' : 'unlock') + ' system');
                }
                updateStatus();
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        setInterval(updateStatus, 2000);
        updateStatus();
    </script>
</body>
</html>`;

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
   * Runs EOD enforcement checks
   * Configure cron in wrangler.toml (see wrangler.toml comments for cron syntax)
   */
  async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    const stub = getGatekeeperDO(env);
    
    // Trigger EOD check by calling the alarm endpoint
    // The DO's alarm() method will call enforceEOD()
    ctx.waitUntil(
      stub.fetch(new Request('http://do-singleton/alarm', { method: 'POST' })).catch((error) => {
        console.error('Scheduled event error:', error);
      })
    );
  },
};

// Export the Durable Object class for wrangler.toml
export { GatekeeperDO };

