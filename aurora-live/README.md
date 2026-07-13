# AURORA LIVE OSINT Browser — MVP 0.1

A runnable, evidence-first global event browser. It is designed as the next generation of `intel-tripwire`: not just a stream of headlines or map pins, but a system that exposes what is verified, what is merely plausible, why an alert fired, what could disprove it, and what action state follows.

## Run

```bash
python3 app.py --offline
```

Open `http://127.0.0.1:8080`.

For live public feeds, omit `--offline`:

```bash
python3 app.py
```

No third-party Python packages are required. When all upstream feeds fail, the server automatically falls back to demonstration fixtures and marks the operating mode clearly.

## Current live adapters

- GDELT DOC 2.0: multilingual global news discovery.
- USGS GeoJSON: official earthquake observations.
- NASA EONET v3: official natural-event catalog.
- GDACS RSS: official global disaster alerts.

## Evidence logic

Each event is a claim backed by one or more evidence records. Syndicated copies from the same source family do not count as independent corroboration.

- **SUPPORTED / G3**: directly observed or published by an issuing authority. Verification applies only to the underlying observation, not every claim about cause, attribution, damage, or intent.
- **SUPPORTED / G2**: three or more independent source families, including a Tier 1 or Tier 2 source.
- **PLAUSIBLE / G2**: at least two independent source families.
- **NOT_PROVEN / G1**: one source family or an unverified social claim.

The confidence score is shown with its components. It is a triage instrument, not proof.

## Product architecture

`SCOUT` ingestion → `SOURCEGRID` evidence normalization and source-lineage deduplication → `K-ALIGN` claim status → `BLACKGLASS` counterargument and falsifier → `CRF/IPR` impact and phase analysis → `AURORA GRID` action state.

The UI has three operational surfaces:

1. **Operating picture**: global map, severity/category filters, and live event stream.
2. **Incident room**: claim, evidence chain, confidence components, changes, counterargument, falsifier, and action state.
3. **Source health**: every upstream feed reports live/degraded/fallback status rather than failing silently.

## Next build gates

1. Persistent event/version store and corrections ledger.
2. Real source-lineage graph that detects wire syndication, screenshots, and copied social claims.
3. Image/video verification queue: reverse-image matches, EXIF warnings, perceptual hashes, frame extraction, and geolocation workbench.
4. Live aviation, maritime, internet outage, wildfire, weather, cyber, sanctions, and market adapters.
5. Analyst review states, shared incident rooms, watchlists, geofences, Telegram/email/webhook alerts.
6. Forecast calibration ledger: probability, horizon, trigger map, falsifier, Brier score, and outcome resolution.
7. MCP/REST interface so agents can query evidence rather than scrape the UI.

## Safety boundary

The product should not expose private addresses, non-public personal data, credentials, or real-time tracking of private individuals. Sensitive operational layers should be delayed, aggregated, or access-controlled where disclosure could create direct harm.
