# Identity, workspaces, RBAC, and audit

AURORA authenticates every API request with a named token bound to one user and one workspace. The token determines the active workspace; clients cannot select a different workspace with a request header or query parameter.

## Roles

- `viewer`: read workspace data.
- `analyst`: read and modify analyst-owned workspace data.
- `admin`: analyst permissions plus ingest, membership, token, audit, and worker administration.
- `owner`: admin permissions plus workspace creation and owner-role assignment.

The first administrator created in a new installation becomes the owner of the default workspace. Later administrators receive the `admin` workspace role.

Creating any user via `POST /api/platform/users` requires a valid `X-Bootstrap-Secret` matching `AURORA_BOOTSTRAP_SECRET`, including the first administrator.

## Tokens

Tokens are stored as SHA-256 digests. The cleartext secret is returned only when a token is created or rotated.

Tokens support a name, optional ISO-8601 expiration timestamp, revocation, rotation, and last-used tracking. Authentication rejects expired or revoked tokens. Rotation revokes the old token before issuing the replacement.

Administrative routes:

```text
GET  /api/platform/workspaces
POST /api/platform/workspaces
GET  /api/platform/memberships
POST /api/platform/memberships
GET  /api/platform/tokens
POST /api/platform/tokens
POST /api/platform/tokens/{token_id}/revoke
POST /api/platform/tokens/{token_id}/rotate
GET  /api/platform/audit
GET  /api/platform/workers
```

## Isolation

Watchlists, incidents, evidence, timelines, alerts, notes, cases, case notes, case links, webhooks, and deliveries carry a `workspace_id`. Repository and operation methods include the authenticated workspace in reads, writes, updates, and deletes. Incident and evidence identifiers are namespaced by workspace.

The background worker collects a source batch once, then ingests it separately into every workspace. Alert matching and webhook queueing remain workspace-scoped.

## Audit records

Authentication, denied authorization, user creation, workspace creation, membership changes, token lifecycle events, ingest, and principal analyst changes create audit records. SQLite triggers and PostgreSQL rules prevent update and deletion of audit rows.

## Existing installations

Startup performs an additive migration:

1. Create the default organization and workspace.
2. Add legacy users as members of the default workspace.
3. Import existing token digests as named legacy tokens.
4. Add `workspace_id` columns to operational tables.
5. Assign existing operational rows to the default workspace.

Back up the database before deploying the migration. Existing cleartext API tokens continue to authenticate against their migrated digest.

The SQLite-to-PostgreSQL migration command copies memberships, tokens, workspace-scoped columns, worker state, and audit events in addition to operational data.
