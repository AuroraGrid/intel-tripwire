# AURORA LIVE private beta release

## Gate (current)

| Surface | Decision |
| --- | --- |
| Private local beta on `127.0.0.1` | **GO** |
| Home LAN share | **BLOCKED** |
| Tunnel / port-forward / public reverse proxy | **BLOCKED** |
| Invite-only or open public beta | **BLOCKED** |

Binding constraint: trustworthy operation under shared or hostile access. Local loopback contains residual remote-beta blockers (webhook SSRF hardening, distributed limiter/store, phase 38–40 orchestration in default launcher).

## Status (operator host)

Private **local beta** is the supported first release shape on Windows without Docker.

| Check | Result |
| --- | --- |
| `release_check.py --allow-local` | required before start |
| Platform | `http://127.0.0.1:8090` (Waitress / `release_wsgi`) |
| Worker | `release_worker.py` heartbeat required for readiness |
| Admin | `POST /api/platform/users` with role `admin` **and** `X-Bootstrap-Secret` |
| Stack data plane | phase38/39/40 workers started by default via `scripts/start_layer_workers.py` (`AURORA_START_LAYER_WORKERS=0` to disable) |
| Remote beta gate | eight fixes required (bootstrap, time-series, ops auth, redundancy, layer workers, durable limits/push, webhook SSRF, readiness+ingestion) |

## Start (Windows)

```powershell
cd aurora-live
# 1) copy and fill secrets (never commit .env)
copy .env.example .env
# or use scripts/start-beta-local.ps1 which expects .env already present

powershell -ExecutionPolicy Bypass -File scripts/start-beta-local.ps1
```

Open:

- Platform: http://127.0.0.1:8090/platform
- Health: http://127.0.0.1:8090/api/platform/health
- Ready: http://127.0.0.1:8090/api/platform/ready

## Public beta (when you have a host + domain)

1. Install Docker Desktop / Linux Docker.
2. `cp .env.production.example .env` and set real HTTPS host + secrets.
3. `python release_check.py --env .env` (no `--allow-local`).
4. `docker compose -f docker-compose.public.yml up --build -d`
5. Create admin via HTTPS API.
6. Invite-only traffic first (`PUBLIC_BETA.md`).

## Safety

- Do not commit `.env`, admin tokens, or API keys.
- Rotate any keys that were shared outside a secret store.
- YouTube webcam LIVE state can drift; re-probe periodically.
