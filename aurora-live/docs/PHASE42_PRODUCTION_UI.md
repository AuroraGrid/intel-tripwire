# Phase 42 — Production GOP, Incident Room, Source Health UI

Phase 42 ships a public, evidence-bound operations UI.

## Routes

| Path | Surface |
|------|---------|
| `/`, `/gop` | Global Operating Picture |
| `/incident-room` | Public observation stream |
| `/source-health` | Source health panels |
| `/static/gop.html` | Static shell |
| `/static/aurora-live.js` | Client |
| `/api/public/ui/bootstrap` | UI bootstrap contract |

## Binding rule

UI badges and status chips must come from runtime product APIs:

- `/api/public/product/capabilities`
- `/api/public/product/gaps`
- `/api/public/global-operating-picture`

Hardcoded LIVE labels are forbidden.

## Auth boundary

Public views are read-only. Analyst write paths remain on the authenticated operations console (`static/platform.html`).