# AURORA LIVE Developer Access

Phase 24 exposes a versioned, workspace-isolated developer surface without requiring an AI provider.

## Discovery

- OpenAPI 3.1: `GET /api/v1/openapi.json`
- Agent manifest: `GET /.well-known/aurora-agent.json`
- MCP manifest: `GET /mcp/manifest.json`
- MCP Streamable HTTP endpoint: `POST /mcp`

## Authentication

Human tokens use `Authorization: Bearer <token>`.

Administrators can create a scoped service credential with:

```http
POST /api/platform/developer/clients
Authorization: Bearer <admin-token>
Content-Type: application/json

{"name":"operations-bot","scopes":["read"]}
```

The returned `aurora_sk_...` secret is displayed once. Send it through either:

```http
X-AURORA-API-KEY: aurora_sk_...
```

or as a bearer credential. Credentials are workspace-bound, scoped, expirable, auditable, and revocable.

## API v1

Read endpoints cover detections, route plans, forecast candidates, durable events, search, source health, and contradictions. Collections use opaque cursor pagination.

Forecast approval is intentionally the only v1 write in Phase 24. It requires the `forecasts:write` scope, an analyst rationale, and an `Idempotency-Key` header. Automation cannot bypass the Phase 22 analyst-approval gate.

## MCP

The MCP server implements JSON-RPC 2.0 initialization, tool listing, and tool calls. All Phase 24 tools are read-only and workspace-isolated. Tools expose search, detections, routes, forecast candidates, contradictions, source health, event replay, and the unified command center.

## Clients

- Python: `phase24_sdk.AuroraClient`
- TypeScript: `sdk/typescript/aurora.ts`
- CLI: `python aurora_cli.py --help`

The clients use platform-native HTTP libraries and do not depend on OpenAI, xAI, Gemini, Groq, or any other model API.
