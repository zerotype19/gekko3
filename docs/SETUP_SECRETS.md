# Setting Up Gekko3 Secrets

## Generated API Secret
**API_SECRET:** `0d03cc45af09744228164da6003b865883598cdd6fc85065672a51eba31c718f`

⚠️ **Save this secret securely!** This is shared between your Python Brain and Cloudflare Gatekeeper.

## Upload Secrets to Cloudflare

Run these commands from the `/gekko3` directory:

```bash
# 1. Upload Tradier Access Token (you'll be prompted to paste your token)
npx wrangler secret put TRADIER_ACCESS_TOKEN

# 2. Upload Tradier Account ID (you'll be prompted to paste your account ID)
npx wrangler secret put TRADIER_ACCOUNT_ID

# 3. Upload API Secret (paste the generated secret above)
npx wrangler secret put API_SECRET
# When prompted, paste: 0d03cc45af09744228164da6003b865883598cdd6fc85065672a51eba31c718f
```

## Deploy

After all secrets are uploaded:

```bash
npx wrangler deploy
```

## Verify Deployment

Visit your Worker URL:
```
https://gekko3-core.<your-subdomain>.workers.dev/v1/status
```

**Expected responses:**
- ✅ `200 OK` with JSON status → Gatekeeper is alive and working
- ✅ `401 Unauthorized` → Gatekeeper is alive and blocking (this is also good!)
- ❌ `Error 1101` or connection error → Deployment issue

## Next Steps

Once verified, proceed to set up the Python Brain in `/gekko3-brain`:
1. Copy `env.template` to `.env`
2. Update `.env` with your Gatekeeper URL
3. Run `python test_connection.py` to verify connectivity

