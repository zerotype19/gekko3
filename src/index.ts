/**
 * Gekko3 Main Worker
 * API Router: Routes requests to the Gatekeeper Durable Object
 */

import { GatekeeperDO } from './GatekeeperDO';
import type { Env } from './config';

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

