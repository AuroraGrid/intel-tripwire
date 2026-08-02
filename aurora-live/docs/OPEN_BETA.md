# AURORA LIVE — Open Beta (friends)

## What you share

1. Open `Desktop\AURORA-Messenger\LIVE-LINK.txt` — that file is **auto-updated**.
2. Optional: attach `Desktop\AURORA-share\aurora-og-1200x630.jpg` in Messenger.
3. Friends open the link. **No password. No token.**

## Start everything (you)

```powershell
cd "C:\Users\Asif Computer\aurora-live-work\intel-tripwire-main\aurora-live"
.\start-beta.ps1
```

Or double-run after reboot: Docker Desktop first, then `start-beta.ps1`.

## What stays running

| Piece | Role |
|--------|------|
| Docker: postgres + platform + worker | App |
| `keep-tunnel-alive.ps1` (watchdog) | Restarts Cloudflare tunnel |
| Startup entry `AURORA-Cloudflare-Tunnel.cmd` | Starts watchdog at login |

## If friends see Cloudflare Error 1033

The free `trycloudflare.com` tunnel died. Within ~10–20 seconds the watchdog should:

1. Restart the tunnel  
2. Write a **new** URL to `LIVE-LINK.txt`  

Send them the **new** link from that file. Old URLs never come back to life.

## Local only (no tunnel)

http://127.0.0.1:8090/platform

## Turn off open access later

In `.env` set:

```
AURORA_OPEN_ACCESS=0
```

Then recreate: `docker compose up -d --force-recreate aurora-platform`

## Permanent public URL (no 1033 churn)

Use a **named** Cloudflare Tunnel + your domain, or host Docker on Railway/Fly/VPS. Quick tunnels are temporary by design.
