# Phase 38 provider adapters

Phase 38 now includes two evidence-gated transport providers.

## AviationWeather.gov

- Official, keyless AviationWeather.gov Data API.
- Current adapter ingests METAR station observations.
- This is aviation-weather coverage, not a complete aircraft-position feed.
- A successful request plus durably persisted observations may qualify this provider as online, but does not by itself prove complete aviation coverage.

Run:

```bash
python phase38_worker.py --provider aviation --database var/aurora_transport.sqlite3
```

## AISStream

- Real-time maritime AIS WebSocket provider.
- The API key is read only from `AURORA_AISSTREAM_API_KEY`.
- The application never returns, logs, stores, or commits the credential.
- AISStream is a beta provider with no assumed SLA or complete global reception.

Configure the credential in the deployment secret manager or GitHub Actions secret store:

```text
AURORA_AISSTREAM_API_KEY
```

Then run:

```bash
python phase38_worker.py --provider maritime --database var/aurora_transport.sqlite3
```

Any credential pasted into chat, an issue, a pull request, a log, or source code must be considered exposed and rotated before production use.

## Qualification boundary

Provider registration is not live evidence. A domain is qualified only when at least one provider is online and fresh, licensed for the intended use, and has successful observations durably persisted. Transportation and the independent 70-camera webcam matrix remain unqualified until their separate evidence gates pass.
