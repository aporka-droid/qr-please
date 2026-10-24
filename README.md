# QR Please

A Flask + JavaScript QR code generator with anonymous, browser-scoped history stored in Neon PostgreSQL.

## Deployment architecture

- **Production**: Vercel Flask Function, production Neon database, Vercel Firewall.
- **Preview**: separate Neon development branch (never point Preview at production).
- `DATABASE_URL` should use Neon's **pooled** connection string (host typically contains `-pooler`). Vercel functions are serverless; an in-process connection pool would be replicated across instances.
- Each API request opens a connection only when it needs the database. Flask closes it at request teardown.
- Connections use a 5-second connect timeout, auto-commit mode, and disabled automatic prepared statements for PgBouncer compatibility.
- Reads and writes use explicit transactions with transaction-local statement (10 seconds) and lock (3 seconds) timeouts. Cleanup uses a 25-second statement timeout.
- Database outages and statement/lock timeouts return JSON HTTP 503, without revealing the connection URL.
- Application startup and static requests **do not connect to PostgreSQL or run migrations**.

## Database migrations

Schema changes are explicit. Run migrations on an isolated Neon branch first, review the data, then apply to production using the **direct (non-pooled)** Neon connection URL.

1. Set `MIGRATION_DATABASE_URL` as a temporary environment variable containing that branch's **direct** Neon connection string. Never commit, log or paste this value into GitHub.
2. Run `python migrate.py --apply`.
3. Verify the expected `codes` table and `expires_at` column, and confirm existing rows remain unchanged.

The migration is additive and idempotent: it creates a missing table, adds `expires_at` if needed, and creates indexes if missing. It does not delete any records.

After migrations, set Vercel `DATABASE_URL` separately for **Preview** and **Production**, using the matching branch's **pooled** Neon URL. Changes to Vercel environment variables require a fresh deployment to affect running functions.

## Local development

Use Python 3.12 or newer.

```bash
python -m venv .venv
# Activate your platform's virtual environment
pip install -r requirements.txt
```

Set `DATABASE_URL` locally to a non-production **pooled** Neon URL, then run:

```bash
python app.py
```

Visit http://127.0.0.1:5000. Use `COOKIE_SECURE=1` only on HTTPS; it is enabled in Vercel Production.

## Testing

```bash
pip install pytest
pytest -q test_db_utils.py
```

These regression tests do not contact Neon and check timeout configuration, transaction-local settings, lazy startup, and database error handling.

For PostgreSQL integration tests, set `TEST_DATABASE_URL` to a **dedicated disposable Neon development branch's direct connection URL**. It must differ from `DATABASE_URL`, and it must never point to production. Then run:

```bash
pytest -q test_app.py
```

The integration suite runs the additive migration and creates/deletes its own test records. It covers visitor isolation, PNG/SVG output, validation, quotas, and expiration visibility.

## History, privacy, and retention

An anonymous, random HttpOnly cookie identifies each browser. Only its SHA-256 hash is stored as the record owner. Saved history does not sync across devices; losing the cookie loses access to the collection.

- Up to 500 saved records per visitor.
- Site-wide capacity limit of 20,000 records.
- New saved records expire after **365 days**. Earlier records retain their NULL expiration and are not removed by automatic cleanup.
- Vercel Cron calls `GET /api/cron/cleanup` at **04:00 UTC daily**. It requires `CRON_SECRET` in the bearer token and removes at most 1,000 expired rows in one run.
- Previously downloaded QR images still work after record expiration because they encode the URL directly. Saved record downloads no longer work once expired.
- The application does not fetch the destination URLs. URLs submitted for generation might themselves contain sensitive query strings; do not use QR Please to store secrets.

## Required Production variables

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Pooled Neon production branch URL |
| `COOKIE_SECURE` | Set to `1` for HTTPS-only cookies |
| `CRON_SECRET` | Strong random secret for scheduled cleanup |

Do not set `MIGRATION_DATABASE_URL` in Vercel: it is only for one-off manual migrations and should use a direct connection.
