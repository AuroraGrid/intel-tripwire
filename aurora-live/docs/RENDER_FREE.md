# AURORA LIVE on Render Free

Public HTTPS URL with **no password** for friends. Free tier sleeps when idle.

## Free tier reality (2026)

| Piece | Free? | Limit |
|--------|--------|--------|
| Web service | Yes | ~512 MB RAM, **spins down after ~15 min idle**, ~1 min cold start |
| Postgres | Yes | ~1 GB, **expires ~30 days** after create (then upgrade or lose data) |
| Background Worker service | **No** | We **embed** `release_worker` inside the web container |
| Custom domain | Yes (limits) | Included on free/hobby with caps |
| Credit card | Usually **not** required for free |

## Files in this repo

| File | Role |
|------|------|
| `render.yaml` | Blueprint: free web + free Postgres |
| `Dockerfile.render` | Image for Render (`PORT` + embedded worker) |
| `deploy/render/entrypoint.sh` | Starts worker + Gunicorn |

## Deploy (recommended: Blueprint)

1. Push this repo to **GitHub** (public or private).
2. Go to [https://dashboard.render.com](https://dashboard.render.com) → sign up.
3. **New → Blueprint** → select the repo.
4. Render reads `render.yaml` → creates **aurora-db** (Postgres free) + **aurora-live** (web free).
5. Click apply / deploy.
6. Wait until deploy is **Live** (first build can take 5–15 minutes).
7. Open:

```
https://aurora-live-XXXX.onrender.com/platform
```

(exact URL is on the service page)

**No password. No token.** (`AURORA_OPEN_ACCESS=1`)

## Deploy (manual UI)

1. **New → PostgreSQL** → plan **Free** → create `aurora-db`.
2. **New → Web Service** → connect repo  
   - Runtime: **Docker**  
   - Dockerfile path: `Dockerfile.render`  
   - Instance: **Free**  
   - Health check path: `/api/platform/live`
3. Environment:

| Key | Value |
|-----|--------|
| `DATABASE_URL` | From Postgres → **Internal Database URL** |
| `AURORA_OPEN_ACCESS` | `1` |
| `AURORA_REQUIRE_WORKER` | `0` |
| `AURORA_EMBED_WORKER` | `1` |
| `AURORA_CORS_ORIGIN` | `*` |
| `AURORA_ALLOWED_HOSTS` | `*` |
| `AURORA_TRUSTED_PROXIES` | `0.0.0.0/0` |
| `GUNICORN_WORKERS` | `1` |
| `AURORA_BOOTSTRAP_SECRET` | random long string |
| `AURORA_WEBHOOK_SECRET` | random long string |

4. Deploy.

## After deploy

Friends open:

```
https://YOUR-SERVICE.onrender.com/platform
```

Hard refresh once if you cached an old local page.

### Cold starts

If nobody used the app for ~15 minutes, the first request wakes the service (~30–90s). That’s free tier, not a bug.

### Postgres expiry

Free DB is temporary. Before day 30, either:

- Upgrade Postgres to paid, or  
- Dump/export and recreate free DB (data reset)

## Local test of the Render image

```bash
docker build -f Dockerfile.render -t aurora-render .
docker run --rm -p 10000:10000 -e DATABASE_URL=sqlite:////data/x.db -e AURORA_OPEN_ACCESS=1 aurora-render
# open http://127.0.0.1:10000/platform
```

(For a real local DB, point `DATABASE_URL` at Postgres.)

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Deploy fails OOM | Free RAM is tiny; keep `GUNICORN_WORKERS=1`, no extra services |
| Health check fails | Path must be `/api/platform/live` (not `/ready`) |
| 401 / CORS | Set `AURORA_OPEN_ACCESS=1` and `AURORA_ALLOWED_HOSTS=*` |
| App sleeps | Normal free tier; open URL again and wait |
| No data refreshing | Embedded worker may be slow on 512 MB; wait or upgrade |

## Free vs Oracle vs Cloudflare tunnel

| | Render free | Oracle free | PC + trycloudflare |
|--|-------------|-------------|---------------------|
| Cost | $0 | $0 Always Free | $0 |
| HTTPS URL | Yes (stable) | IP or domain | Random URL |
| Sleeps when idle | Yes | No | If PC off |
| DB | Free 30 days | You run Postgres | Local Docker |
| Best for | Quick friend demo | Always-on free VM | Local beta |

## You still need to

1. Create a Render account  
2. Connect a GitHub repo with this code  
3. Deploy the blueprint  

I can’t log into your Render account. After the service is live, paste the `*.onrender.com` URL if anything breaks and we’ll fix env/health checks.
