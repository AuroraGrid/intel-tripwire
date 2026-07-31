# Phase 39 infrastructure-risk layers

Phase 39 adds eight evidence-gated operational layers:

- severe weather
- wildfire
- outage
- BGP routing
- power
- cyber
- sanctions
- government alerts

A layer is not qualified merely because its provider is registered or configured. Qualification requires a recent successful provider retrieval, at least one valid observation, durable persistence, and an `ONLINE` provider state.

## Built-in official providers

The release includes these keyless or scoped official sources:

- NOAA National Weather Service active alerts for United States severe-weather coverage.
- NASA EONET open wildfire events. EONET is curated event metadata, not a complete global fire-detection feed.
- CISA Known Exploited Vulnerabilities. KEV is a catalog, not a complete cyber-incident feed.
- U.S. Treasury OFAC SDN XML. The SDN list is not every sanctions regime worldwide and does not replace legal screening.
- FEMA disaster declarations. These are U.S. government declaration records, not a universal real-time alert system.
- RIPEstat routing status for operator-selected prefixes or ASNs. Results reflect RIPE RIS visibility and are not a complete Internet-outage detector.

## Configuration-gated layers

Outage and power coverage require an operator-selected official or licensed JSON feed. They remain `NOT_CONFIGURED` until their environment variables are present.

```text
AURORA_OUTAGE_FEED_URL
AURORA_OUTAGE_FEED_LICENSE
AURORA_POWER_FEED_URL
AURORA_POWER_FEED_LICENSE
AURORA_POWER_API_KEY
AURORA_BGP_RESOURCES
```

`AURORA_BGP_RESOURCES` is a comma-separated list of prefixes, IP addresses, or ASNs. BGP scope is never generalized beyond those resources.

The outage and power JSON adapters accept a top-level list or a top-level `events`, `outages`, `data`, or `items` array. A power URL may contain `{api_key}`; the worker replaces that placeholder at runtime. Secret values are never returned by configuration APIs or persisted in provenance.

## Persistence

Production should use PostgreSQL through one of these variables:

```text
AURORA_INFRASTRUCTURE_DB
AURORA_OPERATIONAL_DB
AURORA_DATABASE_URL
DATABASE_URL
```

One-shot local execution may use SQLite:

```bash
python phase39_worker.py --database var/aurora_infrastructure.sqlite3
```

Run a single provider:

```bash
python phase39_worker.py --provider cisa-kev
```

Require all eight layers to qualify:

```bash
python phase39_worker.py --require-all
```

The standard worker exits successfully when unconfigured optional providers remain `NOT_CONFIGURED`, but it fails when a configured provider cannot produce valid observations. `--require-all` additionally fails until every layer qualifies.

## Freshness model

Operational freshness is based on the age of the successful provider retrieval. Event age is reported separately as `event_freshness_seconds`. This prevents a freshly downloaded official sanctions or vulnerability catalog from being incorrectly marked stale merely because some entries are old.

## Public APIs

```text
GET /.well-known/aurora-phase39.json
GET /api/public/infrastructure/coverage
GET /api/public/infrastructure/health
GET /api/public/infrastructure/providers
GET /api/public/infrastructure/runs
GET /api/public/infrastructure/observations
GET /api/public/infrastructure/configuration
GET /api/public/global-operating-picture
```

Authenticated provider execution:

```text
POST /api/platform/infrastructure/run/{provider}
```

## Evidence boundary

A passing run proves only that the named provider returned valid observations that were durably persisted and remained fresh under the configured threshold. It does not prove complete global coverage, absence of outages, legal compliance, or competitive superiority.
