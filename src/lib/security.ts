/**
 * Security Utilities
 * HMAC signature verification for authenticated requests
 */

import type { Env } from '../config';

/**
 * Recursively sort object keys to ensure canonical JSON representation.
 * Matches Python's json.dumps(sort_keys=True) behavior.
 */
function recursiveSort(obj: unknown): unknown {
  if (obj === null || typeof obj !== 'object') {
    return obj;
  }
  // Preserve array order, but sort objects inside arrays
  if (Array.isArray(obj)) {
    return obj.map(recursiveSort);
  }
  
  // Sort object keys
  const sorted: Record<string, unknown> = {};
  Object.keys(obj as Record<string, unknown>)
    .sort()
    .forEach(key => {
      sorted[key] = recursiveSort((obj as Record<string, unknown>)[key]);
    });
  return sorted;
}

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
  
  // Recursively sort keys to match Python's canonical form
  const sortedPayload = recursiveSort(payloadForSigning);
  
  // Stringify (standard JSON.stringify removes whitespace, matching Python separators)
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

