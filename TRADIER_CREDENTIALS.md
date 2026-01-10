# Tradier API Credentials

## Sandbox (Paper Trading) - Currently Active
- **Account ID:** `VA13978285`
- **API Key:** `XFE6d2z7hJnleNbpQ789otJmvW3z`
- **Base URL:** `https://sandbox.tradier.com`

## Production
- **API Key:** `wDp7ad3HAPeLCYPnjmzU6dQFM9kh`
- **Base URL:** `https://api.tradier.com`
- **Note:** Account ID needed for production - update when available

## Current Configuration
✅ **Cloudflare Secrets:** Configured with Sandbox credentials
✅ **Python Brain:** Use `env.template` for sandbox, `env.production.template` for production

## Switching to Production
1. Update Cloudflare secrets:
   ```bash
   echo "wDp7ad3HAPeLCYPnjmzU6dQFM9kh" | npx wrangler secret put TRADIER_ACCESS_TOKEN
   # Update account ID when available
   ```

2. Update Tradier client base URL in `src/lib/tradier.ts`:
   ```typescript
   const TRADIER_API_BASE = 'https://api.tradier.com/v1'; // Production
   // vs 'https://sandbox.tradier.com/v1' // Sandbox
   ```

3. Redeploy Worker

