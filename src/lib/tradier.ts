/**
 * Tradier API Wrapper
 */

import type { Env } from '../config';

const SANDBOX_API_BASE = 'https://sandbox.tradier.com/v1';
const PRODUCTION_API_BASE = 'https://api.tradier.com/v1';

export class TradierClient {
  private accessToken: string;
  private accountId: string;
  private baseUrl: string;

  constructor(accessToken: string, accountId: string, isProduction: boolean = false) {
    this.accessToken = accessToken;
    this.accountId = accountId;
    this.baseUrl = isProduction ? PRODUCTION_API_BASE : SANDBOX_API_BASE;
    
    console.log(`[Tradier] Initialized in ${isProduction ? 'PRODUCTION' : 'SANDBOX'} mode`);
  }

  private getHeaders(): HeadersInit {
    return {
      'Authorization': `Bearer ${this.accessToken}`,
      'Accept': 'application/json',
    };
  }

  private async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const url = `${this.baseUrl}${endpoint}`;
    const method = options.method || 'GET';
    
    console.log(`[Tradier] Req: ${method} ${endpoint}`);
    
    const response = await fetch(url, {
      ...options,
      headers: {
        ...this.getHeaders(),
        ...options.headers,
      },
    });

    if (!response.ok) {
      const errorText = await response.text();
      // Enhanced error logging - try to parse JSON error if available
      let errorDetails = errorText;
      try {
        const errorJson = JSON.parse(errorText);
        if (errorJson.errors) {
          errorDetails = JSON.stringify(errorJson.errors, null, 2);
        } else if (errorJson.error) {
          errorDetails = errorJson.error;
        } else {
          errorDetails = JSON.stringify(errorJson, null, 2);
        }
      } catch {
        // Not JSON, use text as-is
      }
      
      console.error(`[Tradier] Error ${response.status} on ${method} ${endpoint}:`, errorDetails);
      throw new Error(`Tradier API error (${response.status}): ${errorDetails}`);
    }

    const data = await response.json() as { [key: string]: unknown };
    return data as T;
  }

  async getQuote(symbol: string): Promise<{ last: number; bid: number; ask: number }> {
    const data = await this.request<{ quotes?: { quote?: unknown } }>(`/markets/quotes?symbols=${symbol}`);
    if (!data.quotes?.quote) throw new Error(`No quote data returned for symbol: ${symbol}`);
    const quote = Array.isArray(data.quotes.quote) ? data.quotes.quote[0] : data.quotes.quote;
    const last = (quote as { last?: number }).last ?? 0;
    const bid = (quote as { bid?: number }).bid ?? 0;
    const ask = (quote as { ask?: number }).ask ?? 0;
    return { last, bid, ask };
  }

  async getBalances(): Promise<{ total_equity: number; buying_power: number; day_buying_power: number; cash?: { cash?: number }; }> {
    const data = await this.request<{ balances?: { total_equity?: number; buying_power?: number; day_buying_power?: number; cash?: { cash?: number }; }; }>(`/accounts/${this.accountId}/balances`);
    const balances = data.balances;
    if (!balances) throw new Error('No account data returned from Tradier');
    return {
      total_equity: balances.total_equity ?? 0,
      buying_power: balances.buying_power ?? 0,
      day_buying_power: balances.day_buying_power ?? 0,
      cash: balances.cash,
    };
  }

  async getPositions(): Promise<Array<{ symbol: string; quantity: number; cost_basis: number; date_acquired: string }>> {
    const data = await this.request<{ positions?: { position?: unknown } }>(`/accounts/${this.accountId}/positions`);
    if (!data.positions || data.positions === 'null') return [];
    const posArray = Array.isArray(data.positions.position) ? data.positions.position : [data.positions.position];
    return posArray.map((p: any) => ({
      symbol: p.symbol,
      quantity: p.quantity,
      cost_basis: p.cost_basis,
      date_acquired: p.date_acquired
    })).filter((p: any) => p.symbol && p.quantity !== 0);
  }

  async placeOrder(orderPayload: {
    class: 'option' | 'equity' | 'multileg'; 
    symbol: string;
    type: 'market' | 'limit' | 'credit' | 'debit' | 'even';
    duration: 'day' | 'gtc';
    price?: number;
    option_symbol?: string;
    side?: string;
    quantity?: number;
    'option_symbol[]'?: string[];
    'side[]'?: string[];
    'quantity[]'?: number[];
  }): Promise<{ order_id: string; status: string }> {
    // FIX: Use URLSearchParams instead of FormData to ensure application/x-www-form-urlencoded
    const body = new URLSearchParams();
    
    body.append('class', orderPayload.class);
    body.append('symbol', orderPayload.symbol);
    body.append('type', orderPayload.type);
    body.append('duration', orderPayload.duration);
    
    if (orderPayload.price !== undefined) {
      body.append('price', orderPayload.price.toFixed(2));
    }

    if (orderPayload.class === 'multileg') {
      const symbols = orderPayload['option_symbol[]'] || [];
      const sides = orderPayload['side[]'] || [];
      const quantities = orderPayload['quantity[]'] || [];

      symbols.forEach((sym, idx) => body.append(`option_symbol[${idx}]`, sym));
      sides.forEach((side, idx) => body.append(`side[${idx}]`, side));
      quantities.forEach((qty, idx) => body.append(`quantity[${idx}]`, qty.toString()));
    } else {
      if (orderPayload.option_symbol) body.append('option_symbol', orderPayload.option_symbol);
      if (orderPayload.side) body.append('side', orderPayload.side);
      if (orderPayload.quantity) body.append('quantity', orderPayload.quantity.toString());
    }

    console.log(`[Tradier] Order Body: ${body.toString()}`);

    const data = await this.request<{ order?: { id?: number; status?: string; }; }>(`/accounts/${this.accountId}/orders`, {
      method: 'POST',
      body: body,
    });

    const order = data.order;
    if (!order || !order.id) throw new Error('Failed to place order: No order ID returned');

    return {
      order_id: order.id.toString(),
      status: order.status ?? 'pending',
    };
  }

  async cancelOrder(orderId: string): Promise<{ order_id: string; status: string }> {
    const data = await this.request<{ order?: { id?: number; status?: string; }; }>(`/accounts/${this.accountId}/orders/${orderId}`, { method: 'DELETE' });
    const order = data.order;
    if (!order) throw new Error(`Failed to cancel order ${orderId}`);
    return { order_id: order.id?.toString() ?? orderId, status: order.status ?? 'cancelled' };
  }
}

export function createTradierClient(env: Env): TradierClient {
  if (!env.TRADIER_ACCESS_TOKEN || !env.TRADIER_ACCOUNT_ID) throw new Error('TRADIER_ACCESS_TOKEN and TRADIER_ACCOUNT_ID must be set');
  
  // Auto-detect production mode from environment variable
  const isProduction = env.ENV === 'production';
  return new TradierClient(env.TRADIER_ACCESS_TOKEN, env.TRADIER_ACCOUNT_ID, isProduction);
}
