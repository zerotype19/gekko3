/**
 * Tradier API Wrapper
 * Lightweight fetch-based client for Tradier brokerage operations
 */

import type { Env } from '../config';

// Use sandbox for testing, switch to production when ready
// Sandbox: https://sandbox.tradier.com/v1
// Production: https://api.tradier.com/v1
const TRADIER_API_BASE = 'https://sandbox.tradier.com/v1';

/**
 * Tradier API Client
 */
export class TradierClient {
  private accessToken: string;
  private accountId: string;

  constructor(accessToken: string, accountId: string) {
    this.accessToken = accessToken;
    this.accountId = accountId;
  }

  /**
   * Get authentication headers
   */
  private getHeaders(): HeadersInit {
    return {
      'Authorization': `Bearer ${this.accessToken}`,
      'Accept': 'application/json',
    };
  }

  /**
   * Make authenticated request to Tradier API
   */
  private async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const url = `${TRADIER_API_BASE}${endpoint}`;
    const response = await fetch(url, {
      ...options,
      headers: {
        ...this.getHeaders(),
        ...options.headers,
      },
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Tradier API error (${response.status}): ${errorText}`);
    }

    const data = await response.json() as { [key: string]: unknown };
    return data as T;
  }

  /**
   * Get real-time quote for a symbol
   */
  async getQuote(symbol: string): Promise<{ last: number; bid: number; ask: number }> {
    const data = await this.request<{ quotes?: { quote?: unknown } }>(`/markets/quotes?symbols=${symbol}`);
    
    if (!data.quotes?.quote) {
      throw new Error(`No quote data returned for symbol: ${symbol}`);
    }

    const quote = Array.isArray(data.quotes.quote) 
      ? data.quotes.quote[0] 
      : data.quotes.quote;

    const last = (quote as { last?: number }).last ?? 0;
    const bid = (quote as { bid?: number }).bid ?? 0;
    const ask = (quote as { ask?: number }).ask ?? 0;

    return { last, bid, ask };
  }

  /**
   * Get account balances and equity
   */
  async getBalances(): Promise<{
    total_equity: number;
    buying_power: number;
    day_buying_power: number;
    cash?: { cash?: number };
  }> {
    const data = await this.request<{
      accounts?: {
        account?: {
          total_equity?: number;
          buying_power?: number;
          day_buying_power?: number;
          cash?: { cash?: number };
        };
      };
    }>(`/accounts/${this.accountId}/balances`);

    const account = data.accounts?.account;
    if (!account) {
      throw new Error('No account data returned from Tradier');
    }

    return {
      total_equity: account.total_equity ?? 0,
      buying_power: account.buying_power ?? 0,
      day_buying_power: account.day_buying_power ?? 0,
      cash: account.cash,
    };
  }

  /**
   * Place an order (Supports Multileg)
   * @param orderPayload Tradier order payload
   * @returns Order ID and status
   */
  async placeOrder(orderPayload: {
    class: 'option' | 'equity' | 'multileg';
    symbol: string;
    type: 'market' | 'limit';
    duration: 'day' | 'gtc';
    price?: number;
    
    // For Single Leg
    option_symbol?: string;
    side?: 'buy' | 'buy_to_open' | 'buy_to_close' | 'sell' | 'sell_to_open' | 'sell_to_close';
    quantity?: number;

    // For Multi Leg
    'option_symbol[]'?: string[];
    'side[]'?: string[];
    'quantity[]'?: number[];
  }): Promise<{ order_id: string; status: string }> {
    const formData = new FormData();
    formData.append('class', orderPayload.class);
    formData.append('symbol', orderPayload.symbol);
    formData.append('type', orderPayload.type);
    formData.append('duration', orderPayload.duration);
    
    if (orderPayload.price !== undefined) {
      formData.append('price', orderPayload.price.toFixed(2));
    }

    // Handle Multileg Arrays
    if (orderPayload.class === 'multileg') {
      const symbols = orderPayload['option_symbol[]'] || [];
      const sides = orderPayload['side[]'] || [];
      const quantities = orderPayload['quantity[]'] || [];

      // Tradier expects arrays to be appended individually with indexed keys
      // e.g. option_symbol[0], option_symbol[1]...
      symbols.forEach((sym, idx) => formData.append(`option_symbol[${idx}]`, sym));
      sides.forEach((side, idx) => formData.append(`side[${idx}]`, side));
      quantities.forEach((qty, idx) => formData.append(`quantity[${idx}]`, qty.toString()));
    } else {
      // Single Leg
      if (orderPayload.option_symbol) {
        formData.append('option_symbol', orderPayload.option_symbol);
      }
      if (orderPayload.side) {
        formData.append('side', orderPayload.side);
      }
      if (orderPayload.quantity !== undefined) {
        formData.append('quantity', orderPayload.quantity.toString());
      }
    }

    const data = await this.request<{
      order?: {
        id?: number;
        status?: string;
      };
    }>(`/accounts/${this.accountId}/orders`, {
      method: 'POST',
      body: formData,
    });

    const order = data.order;
    if (!order || !order.id) {
      throw new Error('Failed to place order: No order ID returned');
    }

    return {
      order_id: order.id.toString(),
      status: order.status ?? 'pending',
    };
  }

  /**
   * Cancel an order
   */
  async cancelOrder(orderId: string): Promise<{ order_id: string; status: string }> {
    const data = await this.request<{
      order?: {
        id?: number;
        status?: string;
      };
    }>(`/accounts/${this.accountId}/orders/${orderId}`, {
      method: 'DELETE',
    });

    const order = data.order;
    if (!order) {
      throw new Error(`Failed to cancel order ${orderId}`);
    }

    return {
      order_id: order.id?.toString() ?? orderId,
      status: order.status ?? 'cancelled',
    };
  }
}

/**
 * Create Tradier client from environment
 */
export function createTradierClient(env: Env): TradierClient {
  if (!env.TRADIER_ACCESS_TOKEN || !env.TRADIER_ACCOUNT_ID) {
    throw new Error('TRADIER_ACCESS_TOKEN and TRADIER_ACCOUNT_ID must be set');
  }
  return new TradierClient(env.TRADIER_ACCESS_TOKEN, env.TRADIER_ACCOUNT_ID);
}

