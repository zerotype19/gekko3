/**
 * Security Utilities
 * HMAC signature verification for authenticated requests
 */

import type { Env } from '../config';

/**
 * Verify HMAC signature
 * Implements proper HMAC-SHA256 verification with constant-time comparison
 */
export async function verifySignature(
  proposal: { id: string; timestamp: number; signature?: string; [key: string]: unknown },
  providedSignature: string,
  secret: string
): Promise<boolean> {
  if (!providedSignature || providedSignature.length === 0) {
    return false;
  }

  // Reconstruct the payload as it was signed (without the signature field)
  const payloadForSigning = { ...proposal };
  delete payloadForSigning.signature;
  
  // Create canonical JSON string (sorted keys, no spaces - matching Python's json.dumps with sort_keys=True, separators=(',', ':'))
  // Sort keys by creating a new object with sorted keys
  const sortedKeys = Object.keys(payloadForSigning).sort();
  const sortedPayload: Record<string, unknown> = {};
  for (const key of sortedKeys) {
    sortedPayload[key] = payloadForSigning[key];
  }
  const payloadJson = JSON.stringify(sortedPayload);

  // Compute HMAC-SHA256
  const encoder = new TextEncoder();
  const keyData = encoder.encode(secret);
  const messageData = encoder.encode(payloadJson);
  
  const cryptoKey = await crypto.subtle.importKey(
    'raw',
    keyData,
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  
  const signatureBuffer = await crypto.subtle.sign('HMAC', cryptoKey, messageData);
  const computedSignature = Array.from(new Uint8Array(signatureBuffer))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');

  // Constant-time comparison to prevent timing attacks
  if (computedSignature.length !== providedSignature.length) {
    return false;
  }

  let result = 0;
  for (let i = 0; i < computedSignature.length; i++) {
    result |= computedSignature.charCodeAt(i) ^ providedSignature.charCodeAt(i);
  }

  return result === 0;
}

/**
 * Extract signature from request headers
 */
export function extractSignatureFromHeaders(headers: Headers): string | null {
  // Check X-GW-Signature header (primary)
  const gwSigHeader = headers.get('X-GW-Signature');
  if (gwSigHeader) {
    return gwSigHeader;
  }
  
  // Fallback: Check Authorization header: "HMAC <signature>"
  const authHeader = headers.get('Authorization');
  if (authHeader?.startsWith('HMAC ')) {
    return authHeader.substring(5);
  }
  
  // Fallback: Check X-Signature header (legacy)
  const sigHeader = headers.get('X-Signature');
  if (sigHeader) {
    return sigHeader;
  }

  return null;
}

