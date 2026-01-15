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
      
      // Enhanced error parsing - Tradier API can return various error formats
      let errorDetails = errorText;
      let errorSummary = errorText;
      let errorCode: string | undefined;
      let errorFields: string[] = [];
      
      try {
        const errorJson = JSON.parse(errorText);
        
        // Extract error details based on common Tradier error structures
        if (errorJson.errors) {
          // Array of error objects: [{ field: 'symbol', message: '...' }]
          if (Array.isArray(errorJson.errors)) {
            const messages = errorJson.errors.map((e: any) => {
              if (e.field) errorFields.push(e.field);
              return e.message || e.error || JSON.stringify(e);
            });
            errorSummary = messages.join('; ');
            errorDetails = JSON.stringify(errorJson.errors, null, 2);
          } else if (typeof errorJson.errors === 'object') {
            // Single error object
            errorSummary = errorJson.errors.message || errorJson.errors.error || JSON.stringify(errorJson.errors);
            errorDetails = JSON.stringify(errorJson.errors, null, 2);
          } else {
            errorSummary = String(errorJson.errors);
            errorDetails = JSON.stringify(errorJson.errors, null, 2);
          }
        } else if (errorJson.error) {
          // Single error string or object
          if (typeof errorJson.error === 'object') {
            errorSummary = errorJson.error.message || errorJson.error.description || JSON.stringify(errorJson.error);
            errorDetails = JSON.stringify(errorJson.error, null, 2);
            if (errorJson.error.code) errorCode = errorJson.error.code;
          } else {
            errorSummary = String(errorJson.error);
            errorDetails = errorJson.error;
          }
        } else if (errorJson.fault) {
          // SOAP-style fault
          errorSummary = errorJson.fault.faultstring || JSON.stringify(errorJson.fault);
          errorDetails = JSON.stringify(errorJson.fault, null, 2);
        } else if (errorJson.message) {
          errorSummary = errorJson.message;
          errorDetails = JSON.stringify(errorJson, null, 2);
        } else {
          // Unknown structure, include full JSON
          errorSummary = JSON.stringify(errorJson);
          errorDetails = JSON.stringify(errorJson, null, 2);
        }
        
        // Extract error code if available
        if (!errorCode && errorJson.code) {
          errorCode = String(errorJson.code);
        }
      } catch {
        // Not JSON, use text as-is
        errorSummary = errorText.substring(0, 200); // Limit length
      }
      
      // Build comprehensive error message
      let errorMessage = `Tradier API error (${response.status})`;
      if (errorCode) {
        errorMessage += ` [${errorCode}]`;
      }
      if (errorFields.length > 0) {
        errorMessage += ` Fields: ${errorFields.join(', ')}`;
      }
      errorMessage += `: ${errorSummary}`;
      
      // Log full details for debugging
      console.error(`[Tradier] Error ${response.status} on ${method} ${endpoint}`);
      console.error(`[Tradier] Error Summary: ${errorSummary}`);
      if (errorDetails !== errorSummary) {
        console.error(`[Tradier] Full Error Details:\n${errorDetails}`);
      }
      if (options.body) {
        try {
          const bodyPreview = typeof options.body === 'string' 
            ? options.body.substring(0, 500) 
            : JSON.stringify(options.body).substring(0, 500);
          console.error(`[Tradier] Request Body: ${bodyPreview}`);
        } catch {
          console.error(`[Tradier] Request Body: [unable to serialize]`);
        }
      }
      
      throw new Error(errorMessage);
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

      // Tradier multileg format: option_symbol[0], option_symbol[1], etc.
      // side[0], side[1], etc.
      // quantity[0], quantity[1], etc.
      symbols.forEach((sym, idx) => body.append(`option_symbol[${idx}]`, sym));
      sides.forEach((side, idx) => {
        // Validate side is correct option order type (not stock order type)
        if (!['buy_to_open', 'sell_to_open', 'buy_to_close', 'sell_to_close'].includes(side)) {
          console.error(`[Tradier] Invalid side for option order: ${side} at index ${idx}`);
          throw new Error(`Invalid option side: ${side}. Must be buy_to_open, sell_to_open, buy_to_close, or sell_to_close`);
        }
        body.append(`side[${idx}]`, side);
      });
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
