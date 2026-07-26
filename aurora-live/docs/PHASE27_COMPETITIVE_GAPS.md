# Phase 27 competitive gap closure

Phase 27 turns the Phase 25 benchmark's `BEHIND` and `NOT_VERIFIED` rows into
an evidence-backed engineering ledger. It does not inflate endpoint counts or
convert implementation claims into proof.

## Qualification policy

- Benchmark rows are synchronized from the current Phase 25 qualifier.
- Evidence is append-only and workspace-scoped.
- The newest evidence controls the current result.
- Evidence expires according to the capability being measured.
- A newer failure overrides an older success.
- Live breadth, usability, forecast history, and uptime have explicit numeric
  thresholds.
- Public uptime and comparative usability require independent evidence.
- MCP parity remains `NOT_VERIFIED` while the competitor target is unknown.
- `AHEAD` and `PARITY` close a row only after its criteria pass.

## API

- `POST /api/platform/competitive/sync`
- `GET /api/platform/competitive/gaps`
- `POST /api/platform/competitive/evidence`
- `GET /api/platform/competitive/evidence?capability=<slug>`
- `GET /.well-known/aurora-competitive-gaps.json`

Mutations require an administrator. Reads remain workspace-isolated. Every
synchronization and evidence record emits an immutable identity audit event.

## Honest limitation

Phase 27 supplies the machinery to close measured gaps. It does not create
external uptime history, independent usability research, resolved forecasts,
or third-party proof. Those rows remain open until current qualifying evidence
is recorded.
