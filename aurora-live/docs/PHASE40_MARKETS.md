# Phase 40 — Markets and prediction surfaces

Phase 40 adds evidence-gated market layers:

- global stocks / indexes
- energy
- commodities
- currencies
- crypto
- economic indicators
- prediction markets

## Qualification rule

A layer is not operational merely because a provider is registered or configured. Qualification requires a recent successful provider retrieval, at least one valid observation, durable persistence, and an `ONLINE` provider state.

## Built-in providers

| Provider | Layer | Notes |
|----------|-------|-------|
| `stooq-global-indexes` | global_stocks | Delayed free-tier major indexes |
| `configured-energy-feed` | energy | Env-configured official JSON feed |
| `stooq-commodities` | commodities | Selected commodity proxies |
| `frankfurter-fx` | currencies | ECB reference rates via Frankfurter |
| `coingecko-markets` | crypto | Top market-cap coins (rate limited) |
| `fred-economic-indicators` | economic_indicators | Requires `AURORA_FRED_API_KEY` |
| `polymarket-gamma` | prediction_markets | Open market snapshots |

## Environment variables

```text
AURORA_MARKETS_DB
AURORA_OPERATIONAL_DB
AURORA_DATABASE_URL
DATABASE_URL
AURORA_FRED_API_KEY
AURORA_ENERGY_FEED_URL
AURORA_ENERGY_FEED_LICENSE
AURORA_ENERGY_API_KEY
AURORA_MARKETS_STALE_SECONDS
AURORA_MARKETS_TIMEOUT_SECONDS
```

Secrets are never returned by configuration APIs.

## Worker

```bash
python phase40_worker.py --database var/aurora_markets.sqlite3
python phase40_worker.py --provider coingecko-markets
python phase40_worker.py --require-all
```

## Public APIs

```text
GET /.well-known/aurora-phase40.json
GET /api/public/markets/coverage
GET /api/public/markets/health
GET /api/public/markets/providers
GET /api/public/markets/runs
GET /api/public/markets/observations
GET /api/public/markets/configuration
POST /api/platform/markets/run/{provider}
```

## Evidence boundary

A successful run proves only that the named provider returned valid observations that were durably persisted and remained fresh. It does not prove complete global market coverage, real-time pricing, or legal suitability for trading.