# AURORA LIVE — Security auditor brief

**Audience:** cybersecurity friend reviewing this product  
**Goal:** keep **all current features**, give the auditor a **reproducible full stack**, and make the attack surface obvious.

This is **not** a production hardening cert. It is a honest map of how the system works today so review is fast and complete.

---

## Recommended audit setup (best)

| Mode | What you get | Use when |
|------|----------------|----------|
| **A. Local Docker (preferred)** | Full app + Postgres + workers, loopback only | Code review, config review, API abuse tests |
| **B. Private password session** | Same stack, friend password, open access **off** | Live walkthrough without public open admin |
| **C. Public open beta (Render free)** | Live HTTPS URL, **no password** | Casual demo only — **not** a clean security lab |

**Auditor should start with Mode A.** Live Render free tier sleeps, cold-starts, and flaky deploys add noise that is **not** part of the product security model.

### One-command local stack (full features)

```powershell
cd aurora-live
# If no .env yet:
#   copy .env.example .env   # or run setup-local-windows.ps1
# Ensure Docker Desktop is running
docker compose up -d --build
```

- UI: `http://127.0.0.1:8090/platform`  
- Health: `http://127.0.0.1:8090/api/platform/health`  
- Live: `http://127.0.0.1:8090/api/platform/live`  

Host bind is **loopback only** (`127.0.0.1:8090`) in default `docker-compose.yml`.

Optional helper:

```powershell
.\scripts\prepare-security-audit.ps1
```

### Source of truth

| Item | Location |
|------|----------|
| Application | this `aurora-live/` tree |
| Repo | `https://github.com/hr185882-creator/intel-tripwire` (path `aurora-live/`) |
| Main HTTP entry | `platform_wsgi.py` |
| Auth / open access | `storage.py`, `identity.py` |
| Hardening notes | `API_HARDENING.md`, `IDENTITY.md` |
| Webhook SSRF controls | `webhook_security.py`, `operations.py` |
| Tests | `tests/` (`python -m unittest discover -s tests -v` when deps installed) |

---

## Architecture (short)

```
Browser → platform_wsgi (Gunicorn) → Postgres
                ↓
         release_worker (+ optional phase 38–40 layer workers)
                ↓
         external public feeds (HTTP egress)
```

- **Dashboard:** `static/platform.html` (+ related static JS).  
- **API prefix:** `/api/platform/*` (plus public/media routes in later phases).  
- **Identity:** users, workspaces, memberships, session secrets, RBAC, **append-only audit events**.  
- **Workers:** ingestion, fusion, webhooks, operating picture, etc. (feature-rich; treat as part of trust boundary).

---

## Auth models (critical for review)

Controlled by env:

| Env | Meaning |
|-----|---------|
| `AURORA_OPEN_ACCESS=1` | **Open beta:** no password; `/api/platform/me` and `/open-session` act as first workspace user (admin-capable). Empty DB may auto-create `open-beta@aurora.local` (newer commits) or require bootstrap user create. |
| `AURORA_OPEN_ACCESS=0` + `AURORA_FRIEND_PASSWORD=…` | Password login issues a session token. |
| Neither / no users | Bearer token required; first user via `POST /api/platform/users` + `X-Bootstrap-Secret`. |

**Security implication:** open access on a **public** URL is intentional for friend demo and is **admin-equivalent** for anyone who can reach the host. Do not treat that as production auth.

### Bootstrap user create

```http
POST /api/platform/users
X-Bootstrap-Secret: <AURORA_BOOTSTRAP_SECRET>
Content-Type: application/json

{"email":"auditor@example.com","role":"admin"}
```

Response includes a **one-time** bearer token. Secret is required for **every** user create (including first admin).

---

## Attack surface checklist

### Internet-facing (when published)

| Surface | Notes |
|---------|--------|
| `GET /platform` | Static SPA shell |
| `GET /api/platform/live` | Unauth liveness (no DB) |
| `GET /api/platform/ready`, `/health` | DB / worker readiness info |
| `GET /api/platform/open-session` | **Open access only** — issues session |
| `POST /api/platform/login` | Password or open-session bypass when open access on |
| `POST /api/platform/users` | Bootstrap secret; user provisioning |
| Authenticated REST | Incidents, cases, webhooks, ingest, workers, audit, … |
| Webhooks (egress) | App delivers to operator-configured HTTPS URLs |

### Controls already present (verify in code)

- Host allowlist (`AURORA_ALLOWED_HOSTS`)  
- CORS allowlist (`AURORA_CORS_ORIGIN`; wildcards restricted unless configured)  
- Trusted proxy only for `X-Forwarded-*`  
- Rate limits on auth/write paths  
- Body size limit  
- Security headers (CSP, frame deny, nosniff, …)  
- Webhook destination SSRF guards (public HTTPS only; metadata/private ranges rejected)  
- RBAC + workspace scoping on many routes  
- Audit log immutability (DB triggers/rules — see `IDENTITY.md`)  

### Known intentional / residual risks (flag these)

1. **Open access = public admin** on any reachable URL.  
2. **Render free** env often uses `AURORA_CORS_ORIGIN=*` and broad trusted proxies for tunnel/proxy ease — **weaker than `API_HARDENING.md` defaults**.  
3. **Embedded worker** on free web dyno shares process/RAM with HTTP (DoS / noisy neighbor).  
4. **Bootstrap secret** is a full user-provisioning key; treat like root.  
5. Large feature surface (many phase modules) = large review surface.  
6. Free Postgres / sleep / cold start are **availability**, not confidentiality, issues — still matter for integrity of demo data.

---

## Suggested audit agenda (2–4 hours)

1. **Threat model** — who is trusted (operator, friend, random internet, compromised webhook target).  
2. **AuthZ matrix** — open access on/off; roles `admin` / `analyst` / viewer; can a session write webhooks / ingest / create users?  
3. **Bootstrap secret** — brute force / leak / rotation.  
4. **SSRF** — webhook URL to `169.254.169.254`, `127.0.0.1`, DNS rebinding.  
5. **Injection** — SQL via search/query params (parameterized paths in `storage.py` / DB layer).  
6. **Session fixation / token storage** — how SPA stores bearer; XSS into `localStorage` if used.  
7. **Supply chain** — `requirements.txt`, base Docker image, compose images.  
8. **Workers** — can a low-priv user enqueue expensive jobs / DoS egress?  
9. **Data classification** — OSINT public feeds vs any operator secrets in env/logs.  
10. **Logging** — tokens/secrets in access logs? request IDs only?

### Quick smoke (local)

```powershell
# Unauth health
curl.exe -s http://127.0.0.1:8090/api/platform/live
curl.exe -s http://127.0.0.1:8090/api/platform/health

# Open session (only if AURORA_OPEN_ACCESS=1)
curl.exe -s http://127.0.0.1:8090/api/platform/open-session

# Me without token (open access should still return user)
curl.exe -s http://127.0.0.1:8090/api/platform/me
```

For a **locked** audit lab, set in `.env`:

```env
AURORA_OPEN_ACCESS=0
AURORA_FRIEND_PASSWORD=<strong shared password>
AURORA_CORS_ORIGIN=http://127.0.0.1:8090
AURORA_ALLOWED_HOSTS=127.0.0.1,localhost
```

Then recreate: `docker compose up -d --force-recreate`.

---

## What to send your cybersecurity friend

1. **This file** (`docs/SECURITY_AUDIT.md`).  
2. **GitHub invite** or zip of `aurora-live/` (no `.env`, no production secrets).  
3. **Optional:** private screen share of local Docker UI.  
4. **Optional live demo:** `https://aurora-live.onrender.com/platform`  
   - Expect **30–90s cold start** on free tier.  
   - Assume **open access** = anyone with the link has admin-like access.  
   - Prefer they **clone + compose** for real findings.

**Do not send:** Render API keys, bootstrap secrets, DB URLs, `.env`, private keys.

---

## Production path (after audit)

Keep **all features**, tighten exposure:

1. `AURORA_OPEN_ACCESS=0`  
2. Strong `AURORA_FRIEND_PASSWORD` or proper invite-only tokens  
3. Exact CORS + hosts (no `*`)  
4. Host on always-on tier (Render paid / Fly / Railway / Oracle free VM with Docker)  
5. Rotate secrets that ever appeared in chat or screenshots  

---

## Related docs

- `API_HARDENING.md` — intended production settings  
- `IDENTITY.md` — RBAC and audit events  
- `PRODUCTION.md` — ops bootstrap  
- `docs/RENDER_FREE.md` — free public demo limits  
- `docs/OPEN_BETA.md` — friend-share workflow (local tunnel era)  
- `ARCHITECTURE.md` — product/architecture narrative  
