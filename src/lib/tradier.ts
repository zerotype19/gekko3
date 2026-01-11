/**
 * Tradier API Wrapper
 */

import type { Env } from '../config';

const TRADIER_API_BASE = 'https://sandbox.tradier.com/v1';

export class TradierClient {
  private accessToken: string;
  private accountId: string;

  constructor(accessToken: string, accountId: string) {
    this.accessToken = accessToken;
    this.accountId = accountId;
  }

  private getHeaders(): HeadersInit {
    return {
      'Authorization': `Bearer ${this.accessToken}`,
      'Accept': 'application/json',
    };
  }

  private async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const url = `${TRADIER_API_BASE}${endpoint}`;
    const method = options.method || 'GET';
    
    // Log outgoing request
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
      // Log full error detail
      console.error(`[Tradier] Error ${response.status} on ${method} ${endpoint}:`, errorText);
      throw new Error(`Tradier API error (${response.status}): ${errorText}`);
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
    const data = await this.request<{ accounts?: { account?: { total_equity?: number; buying_power?: number; day_buying_power?: number; cash?: { cash?: number }; }; }; }>(`/accounts/${this.accountId}/balances`);
    const account = data.accounts?.account;
    if (!account) throw new Error('No account data returned from Tradier');
    return {
      total_equity: account.total_equity ?? 0,
      buying_power: account.buying_power ?? 0,
      day_buying_power: account.day_buying_power ?? 0,
      cash: account.cash,
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
    }));
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

      // Append array items with explicit indexed keys
      symbols.forEach((sym, idx) => body.append(`option_symbol[${idx}]`, sym));
      sides.forEach((side, idx) => body.append(`side[${idx}]`, side));
      quantities.forEach((qty, idx) => body.append(`quantity[${idx}]`, qty.toString()));
    } else {
      if (orderPayload.option_symbol) body.append('option_symbol', orderPayload.option_symbol);
      if (orderPayload.side) body.append('side', orderPayload.side);
      if (orderPayload.quantity) body.append('quantity', orderPayload.quantity.toString());
    }

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
  return new TradierClient(env.TRADIER_ACCESS_TOKEN, env.TRADIER_ACCOUNT_ID);
}
