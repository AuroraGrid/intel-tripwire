# Phase 43 — PWA, public no-paywall controls, caching, abuse, notifications

## Public mode

```text
AURORA_PUBLIC_MODE=1
AURORA_PUBLIC_RATE_LIMIT=120
AURORA_VAPID_PUBLIC_KEY=
```

`GET /api/public/deployment` exposes non-secret public deployment controls.

## PWA shell

- `/static/manifest.webmanifest`
- `/static/sw.js`

The service worker caches the **shell only**. Intelligence APIs always hit the network so stale observations are never presented as current truth without a network refresh.

## Abuse controls

In-memory sliding-window rate limiting applies to public API and public UI paths. Exceeding the limit returns `429` with `Retry-After` and rate-limit headers.

## Notifications

`POST /api/public/notifications/subscribe` stores browser push subscription scaffolding. Delivery remains disabled until VAPID keys are configured.