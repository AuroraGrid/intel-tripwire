# PostgreSQL deployment and migration

AURORA LIVE supports SQLite for local or controlled single-host use and PostgreSQL for durable production deployments. `DATABASE_URL` takes precedence over `DATABASE_PATH`.

## New PostgreSQL deployment

```bash
cd aurora-live
cp .env.example .env
# Replace every placeholder with a strong secret.
docker compose up --build -d
```

The default Compose stack starts PostgreSQL 16 and the AURORA service. Open `http://localhost:8090/platform` after both health checks pass.

## SQLite development mode

```bash
docker compose -f docker-compose.sqlite.yml up --build -d
```

Or run directly:

```bash
DATABASE_PATH=data/aurora-live.db python platform_api.py --host 127.0.0.1 --port 8090
```

## SQLite to PostgreSQL cutover

1. Stop writes to the SQLite deployment.
2. Back up the SQLite file and verify the backup can be opened.
3. Start an empty PostgreSQL database.
4. Install dependencies with `python -m pip install -r requirements.txt`.
5. Run the migration command:

```bash
DATABASE_URL='postgresql://aurora:password@127.0.0.1:5432/aurora' \
python scripts/migrate_sqlite_to_postgres.py --source /path/to/aurora-live.db
```

The command refuses to copy into a populated target. `--truncate-target` is available only for an intentional destructive retry after a PostgreSQL backup has been taken.

The migration copies users, token hashes, watchlists, incidents, evidence, timeline entries, alerts, notes, cases, case links, case notes, webhooks, and delivery queues. It verifies row counts before returning success.

## Cutover verification

```bash
curl --fail http://127.0.0.1:8090/api/platform/health
```

Then verify with an existing bearer token:

```bash
curl --fail http://127.0.0.1:8090/api/platform/me \
  -H 'Authorization: Bearer REPLACE_WITH_EXISTING_TOKEN'
```

Also verify incident counts, one case, one watchlist, and one pending or delivered webhook record through the dashboard.

## Rollback

Do not delete the SQLite database during cutover. To roll back:

1. Stop the PostgreSQL-backed application.
2. Restore the previous deployment configuration using `DATABASE_PATH`.
3. Start the SQLite deployment from the preserved database file.
4. Reconcile any writes accepted after the PostgreSQL cutover manually before attempting another migration.

There is no automatic reverse migration from PostgreSQL to SQLite.

## PostgreSQL backup

Use the database platform's managed backups or `pg_dump`:

```bash
docker compose exec -T postgres pg_dump -U aurora -d aurora -Fc > aurora-postgres.dump
```

Test restoration into a separate database. A backup is not verified until restoration succeeds.
