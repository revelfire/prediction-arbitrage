# Wallet Setup & Execution Integration Guide

This guide walks you through configuring wallet credentials and capital controls for the one-click execution engine. The execution engine places orders on **Polymarket** (via CLOB API) and **Kalshi** (via REST API) from approved execution tickets.

---

## Prerequisites

- Arb Scanner dashboard running with database (`uv run arb-scanner serve`)
- Migration 018 applied (`uv run arb-scanner migrate`)
- Funded accounts on both Polymarket and Kalshi

---

## 1. Polymarket Wallet Setup

Polymarket uses an on-chain wallet on **Polygon (chain ID 137)** for trading. Orders are signed with EIP-712 via the `py-clob-client` SDK.

### Create or Export Your Private Key

If you already trade on Polymarket, export the signer private key from the wallet associated with your account.

For browser-wallet accounts (MetaMask/Rabby/Coinbase Wallet), Polymarket commonly uses a proxy wallet model:
- signer wallet private key (your MetaMask account) signs requests
- funded proxy wallet address holds collateral

In this model, set `signature_type=2` and provide the proxy address as `funder`.

**From MetaMask:**
1. Open MetaMask, select your Polymarket wallet
2. Click the three dots > Account Details > Show Private Key
3. Enter your password and copy the hex key (starts with `0x`)

**From a dedicated trading wallet (recommended):**
1. Create a new wallet in MetaMask or use `cast wallet new` (from Foundry)
2. Fund it with USDC on Polygon
3. Approve the Polymarket CTF Exchange contract to spend your USDC
4. Export the private key

### Set the Environment Variable

```bash
# In your .env file or shell environment
export POLY_PRIVATE_KEY="0xYOUR_POLYGON_PRIVATE_KEY_HERE"
export POLY_SIGNATURE_TYPE="2"          # for proxy-wallet accounts
export POLY_FUNDER="0xYOUR_PROXY_ADDR"  # funded proxy wallet address

# Level-2 API creds (required for balance + order endpoints)
export POLY_API_KEY="..."
export POLY_API_SECRET="..."
export POLY_API_PASSPHRASE="..."
```

### Install the SDK

```bash
uv add py-clob-client
```

### Fund Your Wallet

The executor reads your USDC balance on Polygon. Ensure your wallet has USDC at this contract address:

```
0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174  (USDC on Polygon)
```

You can bridge USDC from Ethereum mainnet via the [Polygon Bridge](https://portal.polygon.technology/bridge) or buy USDC directly on Polygon through an exchange.

### Verify Configuration

Once the dashboard is running with execution enabled, the header shows **EXEC READY** (green) if credentials are detected. For method-2 accounts, both `POLY_PRIVATE_KEY` and `POLY_FUNDER` are required.

---

## 2. Kalshi API Key Setup

Kalshi uses **RSA-PSS signing** for authenticated API requests. You need an API key ID and an RSA private key file.

### Generate API Credentials

1. Log in to [Kalshi](https://kalshi.com)
2. Go to **Settings > API Keys**
3. Click **Create API Key**
4. Download the RSA private key file (PEM format) when prompted — this is the only time you can download it
5. Note the **API Key ID** displayed after creation

### Store the RSA Key File

Save the downloaded PEM file somewhere secure on your machine:

```bash
# Example: store in ~/.kalshi/
mkdir -p ~/.kalshi
mv ~/Downloads/kalshi-api-key.pem ~/.kalshi/private_key.pem
chmod 600 ~/.kalshi/private_key.pem
```

### Set the Environment Variables

```bash
# In your .env file or shell environment
export KALSHI_API_KEY_ID="YOUR_API_KEY_ID"
export KALSHI_RSA_PRIVATE_KEY_PATH="/Users/you/.kalshi/private_key.pem"
```

### Install the Cryptography Library

```bash
uv add cryptography
```

### How Authentication Works

Every write request (order placement, cancellation) is signed with your RSA key:

```
Message = "{timestamp_ms}{HTTP_METHOD}{path}"
Signature = RSA-PSS(SHA256, message)
```

Three headers are sent with each authenticated request:
- `KALSHI-ACCESS-KEY` — your API key ID
- `KALSHI-ACCESS-SIGNATURE` — base64-encoded RSA signature
- `KALSHI-ACCESS-TIMESTAMP` — current time in milliseconds

Read endpoints (order book, market data) do not require authentication.

---

## 3. Enable Execution in config.yaml

Add the `execution` section to your `config.yaml`:

```yaml
execution:
  enabled: true
  max_size_usd: 100.0
  max_slippage_pct: 0.02
  pct_of_balance: 0.02
  max_exposure_pct: 0.25
  min_reserve_usd: 50.0
  daily_loss_limit_usd: 100.0
  max_open_positions: 5
  max_per_market_pct: 0.10
  cooldown_after_loss_seconds: 300
  min_book_depth_contracts: 20
```

The venue sub-configs use sensible defaults. Override only if needed:

```yaml
execution:
  enabled: true
  # ... capital controls above ...
  polymarket:
    chain_id: 137                              # Polygon mainnet
    clob_api_url: "https://clob.polymarket.com"
  kalshi:
    api_base_url: "https://trading-api.kalshi.com/trade-api/v2"
  polymarket:
    chain_id: 137
    clob_api_url: "https://clob.polymarket.com"
    signature_type: 2
    funder: "0xYOUR_PROXY_ADDR"
```

---

## 4. Capital Controls Reference

The execution engine enforces multiple safety checks before any order is placed. All of these are configurable in `config.yaml` under `execution:`.

### Position Sizing

| Parameter | Default | What It Does |
|-----------|---------|--------------|
| `pct_of_balance` | 0.02 (2%) | Suggested trade size as a fraction of the smaller venue balance |
| `max_pct_per_venue` | 0.05 (5%) | Hard cap per venue — never allocate more than this to one trade |
| `max_size_usd` | 100.0 | Absolute maximum USD per trade, regardless of balance |

**How suggested size is computed:**

```
venue_based = min(poly_balance, kalshi_balance) * pct_of_balance
venue_cap   = min(poly_balance, kalshi_balance) * max_pct_per_venue
suggested   = min(venue_based, venue_cap, max_size_usd)
```

### Risk Limits

| Parameter | Default | What It Does |
|-----------|---------|--------------|
| `max_exposure_pct` | 0.25 (25%) | Total open positions cannot exceed 25% of combined balance |
| `daily_loss_limit_usd` | 100.0 | Trading halts if daily realized P&L drops below -$100 |
| `cooldown_after_loss_seconds` | 300 (5 min) | After hitting the loss limit, no new trades for 5 minutes |
| `max_open_positions` | 5 | Maximum concurrent open positions across both venues |
| `max_per_market_pct` | 0.10 (10%) | No single market can consume more than 10% of total balance |
| `min_reserve_usd` | 50.0 | Each venue must retain at least $50 after any trade |

### Liquidity Controls

| Parameter | Default | What It Does |
|-----------|---------|--------------|
| `max_slippage_pct` | 0.02 (2%) | Maximum acceptable VWAP slippage from mid price |
| `min_book_depth_contracts` | 20 | Minimum order book depth (in contracts) to proceed |
| `price_staleness_seconds` | 30 | Prices older than this trigger a refresh before execution |

### Conservative Starter Configuration

If you're starting with small balances or want maximum safety:

```yaml
execution:
  enabled: true
  max_size_usd: 25.0          # Small trades
  pct_of_balance: 0.01        # 1% of balance
  max_exposure_pct: 0.10       # Max 10% deployed
  daily_loss_limit_usd: 25.0   # Tight daily stop
  max_open_positions: 2        # Few concurrent trades
  min_reserve_usd: 100.0       # Keep $100 reserve
  cooldown_after_loss_seconds: 600  # 10 min cooldown
```

---

## 5. Preflight Validation

Before any order is placed, the engine runs 10 preflight checks. All must pass before the Execute button activates.

| Check | What It Validates |
|-------|-------------------|
| **enabled** | `execution.enabled` is `true` in config |
| **credentials** | `POLY_PRIVATE_KEY` is set (plus `POLY_FUNDER` when `POLY_SIGNATURE_TYPE!=0`) and Kalshi creds are set |
| **balances** | Both venue balances are positive |
| **reserve** | Trade won't drop either venue below `min_reserve_usd` |
| **exposure** | Total open positions + this trade stay under `max_exposure_pct` |
| **daily_pnl** | Daily realized P&L hasn't breached `-daily_loss_limit_usd` |
| **cooldown** | Not in a post-loss cooldown period |
| **open_positions** | Current open positions below `max_open_positions` |
| **concentration** | This market's total exposure stays under `max_per_market_pct` |
| **liquidity** | Order books on both venues have sufficient depth and acceptable slippage |

### Reading Preflight Results

Click **1-Click** on an approved ticket to run preflight. The modal shows:

- Each check with a pass/fail indicator and description
- Suggested trade size (pre-populated, editable)
- Estimated slippage on each venue
- Current balances on both venues
- Available book depth in contracts

If all checks pass, the **Execute** button activates. Adjust the size if needed, then confirm.

---

## 6. Execution Flow

When you click Execute:

1. **Order creation** — Two orders are created in the database (status: `submitting`)
2. **Concurrent placement** — Both legs execute simultaneously via `asyncio.gather`:
   - Polymarket: `create_and_post_order()` via py-clob-client SDK
   - Kalshi: Signed `POST /trade-api/v2/portfolio/orders`
3. **Status recording** — Each order updates to `submitted`, `filled`, or `failed`
4. **Result assessment**:
   - Both succeed → `complete`, ticket moves to `executed`
   - One succeeds → `partial` (warning logged, manual intervention needed)
   - Both fail → `failed`, ticket remains in current status

### Partial Execution

If one leg fills but the other fails, you have an **unhedged position**. The system logs a warning and marks the result as `partial`. You will need to manually close the open leg or wait for it to resolve.

To view order details: **GET /api/execution/orders/{arb_id}**

To cancel a pending order: **DELETE /api/execution/orders/{order_id}**

---

## 7. API Endpoints

All execution endpoints are under `/api/execution/`:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/execution/status` | Engine status and config summary |
| POST | `/api/execution/preflight/{arb_id}` | Run all preflight checks for a ticket |
| POST | `/api/execution/execute/{arb_id}` | Place both legs (body: `{"size_usd": 50.0}`) |
| GET | `/api/execution/orders/{arb_id}` | Get orders for a ticket |
| DELETE | `/api/execution/orders/{order_id}` | Cancel a pending order |
| GET | `/api/execution/open-orders` | List all currently open orders |

---

## 8. Environment Variables Summary

Add these to your `.env` file alongside the existing variables:

```bash
# Polymarket execution (Polygon wallet)
POLY_PRIVATE_KEY=0xYOUR_PRIVATE_KEY
POLY_SIGNATURE_TYPE=2
POLY_FUNDER=0xYOUR_PROXY_WALLET
POLY_API_KEY=...
POLY_API_SECRET=...
POLY_API_PASSPHRASE=...

# Kalshi execution (RSA-PSS signing)
KALSHI_API_KEY_ID=your-api-key-id
KALSHI_RSA_PRIVATE_KEY_PATH=/path/to/kalshi/private_key.pem
```

| Variable | Required For | Description |
|----------|-------------|-------------|
| `POLY_PRIVATE_KEY` | Polymarket | Polygon wallet private key (hex, with 0x prefix) |
| `POLY_SIGNATURE_TYPE` | Polymarket | Signature mode (`0` EOA, `2` proxy-wallet/Gnosis-style signing) |
| `POLY_FUNDER` | Polymarket method-2 | Funded proxy wallet address used as funder |
| `POLY_API_KEY` | Polymarket | CLOB API key for level-2 authenticated calls |
| `POLY_API_SECRET` | Polymarket | CLOB API secret |
| `POLY_API_PASSPHRASE` | Polymarket | CLOB API passphrase |
| `KALSHI_API_KEY_ID` | Kalshi | API key identifier from Kalshi settings |
| `KALSHI_RSA_PRIVATE_KEY_PATH` | Kalshi | Absolute path to RSA private key PEM file |

---

## 9. Security Considerations

- **Never commit private keys** to version control. Use `.env` files (already in `.gitignore`).
- **Use a dedicated wallet** for Polymarket trading. Don't reuse your main wallet.
- **Store the Kalshi PEM file** with `chmod 600` (owner read/write only).
- **Start with small balances** until you've verified the system works correctly with your credentials.
- **The `min_reserve_usd` setting** prevents the engine from draining your accounts. Set it to an amount you're comfortable keeping untouched.
- **Daily loss limits** are your circuit breaker. The default `-$100` stops all trading for the day. Adjust based on your risk tolerance.

---

## 10. Troubleshooting

### Dashboard shows "EXEC OFF"

- Check that `execution.enabled: true` is set in `config.yaml`
- Verify you're running with a database (`--no-db` mode disables execution)

### Preflight fails on "credentials"

- Verify env vars are exported: `echo $POLY_PRIVATE_KEY` (should show your key)
- If using method-2/proxy wallets, verify `echo $POLY_FUNDER`
- Verify `POLY_SIGNATURE_TYPE` matches your account type
- For Kalshi, verify the PEM file exists at the configured path
- Restart the dashboard after setting new env vars

### "py-clob-client not installed"

```bash
uv add py-clob-client
```

### "cryptography not installed"

```bash
uv add cryptography
```

### "RSA key file not found"

- Check `KALSHI_RSA_PRIVATE_KEY_PATH` points to an existing file
- Use an absolute path, not relative

### Preflight fails on "balances"

- Both venues must show a positive balance
- Fund your Polymarket wallet with USDC on Polygon
- Fund your Kalshi account via their deposit flow

### Preflight fails on "liquidity"

- The order books on one or both venues are too thin
- Either wait for more liquidity or reduce your trade size
- The `min_book_depth_contracts` setting controls the minimum threshold

### Partial execution warning

- One leg filled but the other failed
- Check `/api/execution/orders/{arb_id}` for details
- Manually close the open position on the venue where it filled
- Consider cancelling the failed leg if it's still pending
