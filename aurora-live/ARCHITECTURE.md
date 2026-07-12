# AURORA LIVE architecture and competitive design

## Product decision

Build an evidence operating system, not another map. Maps are a display primitive. The durable advantage is a claim graph that can tell an analyst:

- what happened;
- which parts are directly evidenced;
- which reports are copied from the same origin;
- what remains unsupported;
- what changed since the previous update;
- what would falsify the assessment;
- what operational action is justified now.

## Competitive synthesis

### World Monitor

Best open-source technical base for a broad global operating picture: multi-domain map layers, aviation, maritime, infrastructure, hazards, news, markets, APIs, MCP and desktop delivery. Its strongest reusable lesson is the broad sensor-and-news convergence layer. The principal opening for AURORA LIVE is a richer claim/provenance model, a corrections ledger, explicit contradiction handling, and decision routing.

### Factal

Best benchmark for verified alerting and human-in-the-loop newsroom operations. Its useful lessons are fast verification, impact proximity, incident collaboration and editor access. The opening is transparent methodology and public/auditable evidence rather than a closed trust boundary.

### Dataminr

Best benchmark for earliest event detection at enterprise scale and tailored impact alerting. Its useful lessons are multimodal fusion, customer-specific relevance and alert-to-action workflows. The opening is an accessible, open, evidence-inspectable system.

### Liveuamap

Best benchmark for conflict-focused chronological map storytelling and human review. Its useful lessons are fast geographic narrative and archives. The opening is cross-domain correlation, source lineage and explicit uncertainty.

### GDELT and ACLED

GDELT is a high-coverage discovery layer; ACLED is a structured conflict-event dataset. Neither alone is the end-user verification and decision system. AURORA LIVE should ingest them as evidence sources while preserving their distinct collection methods and latency.

## System layers

1. **SCOUT — ingestion and triage**
   - Connectors for news, official alerts, sensors, satellite products, aviation, maritime, cyber, markets and public social sources.
   - Every connector emits an immutable raw record plus retrieval timestamp and parser version.

2. **SOURCEGRID — provenance and lineage**
   - Canonical source identity, ownership, jurisdiction, method, tier and historical reliability.
   - Syndication graph: wire origin, mirrors, screenshots, reposts and quote chains.
   - Content hashes and perceptual hashes to stop copied items counting as independent evidence.

3. **K-ALIGN — claim/evidence separation**
   - Atomic claims, each linked to supporting, contradicting and contextual evidence.
   - States: SUPPORTED, PLAUSIBLE, NOT PROVEN, DISPUTED, RETRACTED.
   - Facts, inferences, forecasts and speculation stored separately.

4. **BLACKGLASS — adversarial review**
   - Strongest alternative explanation.
   - Clearest falsifier.
   - Missing evidence and collection bias.
   - Automatic contradiction queue; human review for high-impact claims.

5. **CRF/IPR — constraint and phase analysis**
   - Pressure point, mechanism, timeline, constraints, trigger map and regime-shift detection.
   - Forecast ledger with probability, horizon, calibration and outcome resolution.

6. **COMMAND / AURORA GRID — decision routing**
   - Action states: MONITOR, WAIT, REJECT, INVESTIGATE, HEDGE, TRADE, PUBLISH, ESCALATE, PREPARE.
   - Every action state links back to the exact claims and thresholds that forced it.

## Core data model

- `RawRecord`: immutable fetched object, source URL, retrieval time, hash, parser version.
- `Source`: canonical family, owner, country, method, tier, reliability history.
- `Evidence`: normalized observation/report with time, place, media hashes and provenance path.
- `Claim`: atomic proposition; never a whole article.
- `ClaimEvidenceEdge`: supports, contradicts, contextualizes, duplicates or supersedes.
- `Event`: time-bounded collection of claims.
- `Assessment`: analyst/machine conclusion with confidence grade and explicit scope.
- `Forecast`: probability, time horizon, triggers, falsifier and resolution rule.
- `Action`: state, owner, threshold, expiry and linked assessment.
- `Revision`: append-only history of every material change and correction.

## Non-negotiable trust rules

- Source count is not origin count.
- Official does not mean complete or unbiased; it means direct attribution to an issuing authority.
- A sensor record verifies the measurement, not automatically its cause.
- A video verifies only what can be established about the pixels, time and place.
- Geolocation uncertainty must be numeric or categorical and visible.
- Deleted or corrected reports stay in the audit trail.
- Models may recommend; they do not silently rewrite evidence.
- High-impact alerts must show the evidence needed to reproduce the conclusion.

## Delivery plan

### Gate 1 — working browser

Public adapters, normalized claims, independent-origin counting, map, stream, incident room, source health and offline fallback. This repository package implements that gate.

### Gate 2 — persistent evidence graph

PostgreSQL + PostGIS, object storage, Redis streams, revision ledger, background workers and replayable ingestion.

### Gate 3 — multimodal verification

Image/video hashing, reverse-search integrations, metadata extraction, keyframe analysis, map/sun/shadow/geolocation workbench and analyst review queues.

### Gate 4 — operational coverage

ADS-B, AIS, NASA FIRMS, internet outages, BGP, weather, sanctions, cyber advisories, energy/infrastructure, commodity and prediction-market signals.

### Gate 5 — collaboration and delivery

Shared rooms, comments, assignments, alert rules, mobile/PWA, Telegram/email/webhooks, API, MCP and exportable intelligence briefs.

### Gate 6 — forecasting and calibration

IPR/CRF trigger maps, scenario trees, probability histories, Brier scoring, postmortems and model/analyst comparative calibration.
