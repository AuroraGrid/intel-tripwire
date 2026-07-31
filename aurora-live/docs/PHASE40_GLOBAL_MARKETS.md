# Phase 40 global markets and forecasting

Phase 40 adds seven evidence-gated domains:

- global equities
- energy
- commodities
- foreign exchange
- cryptocurrency
- economic indicators
- prediction markets

A provider is not operational merely because its adapter is registered, its credentials exist, or a dashboard displays a label. Qualification requires a recent successful retrieval, at least one valid numeric observation, durable persistence, and an `ONLINE` provider state.

## Providers and scope

### Global equities — Alpha Vantage

The Alpha Vantage `GLOBAL_QUOTE` endpoint is used for operator-selected symbols.

```text
AURORA_ALPHA_VANTAGE_API_KEY
AURORA_EQUITY_SYMBOLS
```

`AURORA_EQUITY_SYMBOLS` is comma-separated. Examples include `IBM`, `TSCO.LON`, or exchange-specific global symbols supported by the provider. Data freshness depends on entitlement and may be delayed or end-of-day. Exchange licensing and redistribution restrictions remain applicable.

### Energy — U.S. Energy Information Administration

The EIA API v2 requires a free API key. The default route retrieves recent monthly residential electricity prices for Colorado as a bounded official test scope.

```text
AURORA_EIA_API_KEY
AURORA_EIA_ROUTE
AURORA_EIA_QUERY
AURORA_EIA_VALUE_FIELD
```

The route, query and value field may be changed to another EIA v2 dataset. The resulting scope must remain disclosed; one EIA route does not imply complete global energy-market coverage.

### Commodities — World Bank Pink Sheet

The adapter downloads the World Bank monthly historical commodity workbook and records the latest selected series.

```text
AURORA_WORLD_BANK_COMMODITIES_URL
AURORA_COMMODITY_SERIES
```

Default selected series are Brent crude, gold, silver, copper, U.S. natural gas and Australian coal when matching columns exist. Pink Sheet data are monthly publication data, not executable real-time commodity quotes.

### Foreign exchange — European Central Bank

The ECB Data Portal provides daily euro reference rates.

```text
AURORA_ECB_CURRENCIES
```

The default scope is USD, GBP, JPY, CHF, AUD, CAD and CNY against EUR. ECB reference rates are informational averages and are not necessarily transaction prices.

### Cryptocurrency — Coinbase Exchange

The public Coinbase Exchange product ticker supplies last trade, best bid/ask and venue volume.

```text
AURORA_COINBASE_PRODUCTS
```

The default scope is `BTC-USD,ETH-USD`. This is one venue and is not a consolidated global crypto market.

### Economic indicators — World Bank Indicators API v2

The keyless World Bank API supplies latest non-empty observations for configured countries and indicators.

```text
AURORA_WORLD_BANK_COUNTRIES
AURORA_WORLD_BANK_INDICATORS
```

Defaults cover the United States, China, India, Germany, Japan and the United Kingdom for GDP, CPI inflation and unemployment. Publication lags, revisions and source-specific definitions are preserved as limitations.

### Prediction markets — Kalshi public market data

The adapter retrieves open markets through Kalshi’s unauthenticated market-data API.

```text
AURORA_KALSHI_MARKET_LIMIT
```

Prices are market-implied values from one venue, not objective probabilities or universal forecasts. Settlement rules, liquidity, spreads, legal availability and platform terms must be considered.

## Secret handling

API keys are read only from environment variables. Request URLs containing Alpha Vantage or EIA keys are never persisted. Provider failures are sanitized so credential-bearing URLs cannot enter logs, run history or health records.

## Freshness

Qualification uses retrieval freshness. The age of the underlying quote, market update or official publication is separately reported as `event_age_seconds`. This prevents a freshly retrieved annual economic series from being misrepresented as a real-time observation while still proving the provider path is operational.

## Persistence

Production requires PostgreSQL through one of:

```text
AURORA_MARKETS_DB
AURORA_OPERATIONAL_DB
AURORA_DATABASE_URL
DATABASE_URL
```

Local one-shot execution may use SQLite:

```bash
python phase40_worker.py --database var/aurora_markets.sqlite3
```

Run one provider:

```bash
python phase40_worker.py --provider ecb-reference-rates
```

Require all seven domains:

```bash
python phase40_worker.py --require-all
```

The normal worker permits unconfigured credentialed providers to remain `NOT_CONFIGURED`, but fails when a configured provider cannot return valid observations. `--require-all` also fails until every domain qualifies.

## APIs

```text
GET /.well-known/aurora-phase40.json
GET /api/public/markets/coverage
GET /api/public/markets/health
GET /api/public/markets/providers
GET /api/public/markets/runs
GET /api/public/markets/observations
GET /api/public/markets/configuration
GET /api/public/global-operating-picture
```

Authenticated provider execution:

```text
POST /api/platform/markets/run/{provider}
```

## Evidence boundary

A passing provider run proves that the documented provider returned numeric observations that were durably persisted and remained inside the retrieval-freshness window. It does not prove complete market coverage, real-time exchange entitlement, trading suitability, predictive accuracy, legal availability or investment merit.
