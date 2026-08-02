# AURORA LIVE on Oracle Cloud Free Tier

Always-on public hosting for friends **without** Cloudflare Error 1033 churn.

## What free tier gives you (2026)

| Resource | Always Free (typical) |
|----------|------------------------|
| **Ampere A1 (ARM)** | **2 OCPU + 12 GB RAM** total (was 4/24 earlier; Oracle cut free tier mid-2026) |
| **AMD micro VMs** | 2× (1/8 OCPU, 1 GB) — **too small** for this stack |
| **Boot volume** | ~200 GB Always Free storage pool |
| **Public IP** | Yes (ephemeral free) |
| **Outbound** | Sufficient for RSS/API pulls |

**Use Ampere A1 Flex**, not the tiny AMD free VMs.

## Architecture on one free VM

```
Internet → :8090 → aurora-platform (Gunicorn)
                 → postgres
                 → aurora-worker (release_worker)
```

Optional later: domain + Caddy profile for HTTPS.

## Step 1 — Create the free account

1. Go to [https://www.oracle.com/cloud/free/](https://www.oracle.com/cloud/free/)
2. Sign up (card often required for verification; Always Free resources stay free if you stay in free limits).
3. Pick a **home region** that still has Ampere capacity (availability varies by region).

## Step 2 — Create the VM

Console → **Compute → Instances → Create instance**

| Setting | Value |
|---------|--------|
| Name | `aurora-live` |
| Image | **Ubuntu 22.04** or Oracle Linux 8/9 |
| Shape | **VM.Standard.A1.Flex** (Ampere) |
| OCPUs | **2** |
| Memory | **12 GB** |
| Boot volume | 50–100 GB |
| Networking | Create VCN or use default; **assign public IPv4** |
| SSH keys | Add your public key |

### Security list / NSG ingress (critical)

Allow inbound:

| Port | Source | Why |
|------|--------|-----|
| 22 | Your IP (or 0.0.0.0/0 carefully) | SSH |
| 8090 | 0.0.0.0/0 | AURORA console (open beta) |
| 80, 443 | 0.0.0.0/0 | Only if using Caddy + domain |

Save the **public IP**.

## Step 3 — SSH in and install Docker

```bash
ssh ubuntu@YOUR_PUBLIC_IP
# or: ssh opc@YOUR_PUBLIC_IP
```

Copy and run the setup script from this repo:

```bash
# From your Windows PC (in the aurora-live folder):
scp -r deploy/oracle ubuntu@YOUR_PUBLIC_IP:~/
ssh ubuntu@YOUR_PUBLIC_IP
bash ~/oracle/setup-vm.sh
# log out and back in if docker group was added
```

Or install Docker with the official script on Ubuntu:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# reconnect SSH
```

## Step 4 — Copy AURORA and deploy

From your **Windows** machine (PowerShell), in the `aurora-live` repo:

```powershell
# Example: use scp/rsync; exclude huge local junk if needed
scp -r `
  Dockerfile requirements.txt release_wsgi.py release_worker.py `
  platform_wsgi.py production_wsgi.py phase*.py storage.py identity.py `
  operations.py database.py worker*.py durable_*.py app.py feeds.py `
  delivery.py observability.py webhook_security.py static deploy `
  ubuntu@YOUR_PUBLIC_IP:/tmp/aurora-upload/
```

Easier: push to GitHub and on the VM:

```bash
sudo mkdir -p /opt/aurora-live
sudo chown $USER:$USER /opt/aurora-live
git clone YOUR_REPO_URL /opt/aurora-live
cd /opt/aurora-live
bash deploy/oracle/deploy.sh
```

Or rsync the whole project (from Windows with Git Bash / WSL):

```bash
rsync -avz --exclude '.git' --exclude 'var' --exclude 'data' --exclude '__pycache__' \
  ./ ubuntu@YOUR_PUBLIC_IP:/opt/aurora-live/
ssh ubuntu@YOUR_PUBLIC_IP 'cd /opt/aurora-live && bash deploy/oracle/deploy.sh'
```

## Step 5 — Open for friends

After deploy succeeds:

```
http://YOUR_PUBLIC_IP:8090/platform
```

With `AURORA_OPEN_ACCESS=1` (default in `.env.oci.example`):

- **No password**
- **No bearer token**
- Console auto-loads

Update Messenger with that fixed IP URL (stable until you stop the VM).

## HTTPS (optional, free with a domain)

1. Point DNS `A` record → VM public IP  
2. In `.env.oci` set `AURORA_DOMAIN=aurora.example.com` and matching CORS/hosts  
3. Start Caddy:

```bash
docker compose -f deploy/oracle/docker-compose.oci.yml --env-file .env.oci --profile https up -d
```

Then: `https://aurora.example.com/platform`

## Day-2 commands

```bash
cd /opt/aurora-live
docker compose -f deploy/oracle/docker-compose.oci.yml --env-file .env.oci ps
docker compose -f deploy/oracle/docker-compose.oci.yml --env-file .env.oci logs -f aurora-platform
docker compose -f deploy/oracle/docker-compose.oci.yml --env-file .env.oci restart
```

## Capacity notes (2 OCPU / 12 GB)

| Service | Approx RAM |
|---------|------------|
| Postgres | ~256–512 MB |
| Platform (1 Gunicorn worker) | ~300–600 MB |
| Worker | ~200–400 MB |
| OS + Docker | ~1 GB |

Fits free Ampere **comfortably**. Do **not** run extra phase38–40 containers unless you have headroom.

## Common failures

| Problem | Fix |
|---------|-----|
| Out of capacity for A1 | Try another region or retry later (common) |
| Connection timeout | Security list missing 8090/tcp |
| Build fails on ARM | Official `python` / `postgres` images support arm64; pull latest |
| Free tier reclaim | Keep account active; don’t exceed Always Free shapes |
| Friends still 1033 | You’re still on Cloudflare quick tunnel — use the **IP:8090** URL instead |

## vs Cloudflare quick tunnel

| | PC + trycloudflare | OCI free |
|--|---------------------|----------|
| Cost | $0 | $0 Always Free |
| Stable URL | No (new URL often) | Yes (public IP) |
| PC must stay on | Yes | No |
| Error 1033 | Yes when tunnel dies | No |

## You still have to do

1. Create the OCI account  
2. Launch the Ampere VM  
3. Open security list port **8090**  
4. SSH + run the scripts  

I cannot create the Oracle account for you. Once the VM has SSH access, run `setup-vm.sh` + `deploy.sh` (or paste the public IP here and we can troubleshoot step-by-step).
