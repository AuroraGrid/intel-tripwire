# Phase 38 production transport ingestion

## Operational contract

The production workflow runs on the default branch every five minutes and can also be started manually. Each enabled run:

1. validates that persistent PostgreSQL storage and the AISStream credential are configured;
2. ingests official AviationWeather.gov METAR observations;
3. ingests live AISStream vessel-position observations;
4. persists observations, provider runs, provider state, and worker heartbeat telemetry;
5. evaluates both transport domains against a 15-minute freshness window;
6. fails when aviation or maritime evidence is missing, stale, degraded, or not durably stored.

The workflow uses the concurrency group `phase38-production-transport`, so overlapping runs cannot write concurrently.

## Required repository secrets

`AURORA_AISSTREAM_API_KEY`

The AISStream API credential. It is read only from the environment and is never written to observations, summaries, artifacts, or the repository.

`AURORA_DATABASE_URL`

A PostgreSQL connection string for persistent production storage. `DATABASE_URL` is accepted as a compatibility fallback. SQLite is deliberately rejected by the production workflow because GitHub-hosted runner filesystems are ephemeral.

When no PostgreSQL secret exists, the scheduled workflow reports `dormant` and exits successfully. This prevents repeated failure notifications while preserving the deployment boundary. Once a supported database secret exists, the same workflow becomes active without another code change.

## Evidence boundary

A passing production run establishes that both configured providers returned valid observations, that the observations were committed to PostgreSQL, and that the latest provider evidence is within the configured freshness window.

It does not establish complete global aircraft or vessel coverage. AviationWeather.gov METAR is a weather-station feed rather than an aircraft-position feed. AIS coverage is receiver- and provider-dependent. Provider licensing, redistribution rights, quotas, and service availability remain external constraints.

## Manual verification

Run `AURORA Phase 38 Production Transport Ingestion` from the Actions page after configuring PostgreSQL. The job summary reports domain qualification, provider state, freshness, and observation counts without printing database credentials or API keys.

The health gate can also be run from an operational host:

```bash
cd aurora-live
python phase38_worker.py --provider all --worker-name phase38-production
python phase38_healthcheck.py --max-age 900 --require aviation maritime
```

The worker resolves storage in this order: `AURORA_TRANSPORT_DB`, `AURORA_DATABASE_URL`, `DATABASE_URL`.
