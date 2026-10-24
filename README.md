# Ink — QR Studio

A responsive QR code maker with a Flask backend, Python QR generation and Neon PostgreSQL history. The frontend is plain HTML, CSS and JavaScript.

## Run locally

Requires Python 3.10 or newer.

```sh
python -m venv .venv
```

Activate the environment:

- macOS / Linux: `source .venv/bin/activate`
- Windows PowerShell: `.venv\Scripts\Activate.ps1`

```sh
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000 in your browser.

## Features

- URL input, optional name and four scan-friendly ink colors.
- Python-generated PNG and SVG downloads with a white quiet zone.
- Automatic saved history, reopening and deletion.
- Responsive layout, keyboard controls, error and empty states.
- Parameterized database queries and visitor-scoped access to every saved code.

## How history works

Records are stored in Neon PostgreSQL. A random, HTTP-only browser cookie identifies the visitor and lasts one year, refreshed on visits. Only its SHA-256 hash is stored with records. Returning with the same browser cookie restores history, including after a server restart. Different browser profiles get separate collections.

This is anonymous browser-based history, not an account system. Clearing cookies loses access to the previous collection. It does not sync across devices. Treat the cookie as a private access key; URLs may contain sensitive information. The site never fetches submitted URLs. Codes are static: the encoded URL cannot be changed after download.

## Production deployment

Use a Python-capable host with a persistent disk. On Linux:

```sh
DATA_DIR=/var/lib/qr-studio COOKIE_SECURE=1 gunicorn --workers 2 --bind 0.0.0.0:8000 app:app
```

Create the data directory and give the application user write access. Terminate HTTPS at the hosting platform or reverse proxy. `COOKIE_SECURE=1` requires HTTPS; leave it unset for local HTTP development. Back up the SQLite database. Keep all workers on the same persistent disk; ephemeral/serverless disks will lose history. For a large public deployment, add request rate limiting at the proxy and move to an account system and managed database as needed. The included Flask development server is for local use.

## Checks

```sh
pip install pytest
pytest -q
```

Tests cover generation, persistence across application restarts, visitor isolation, downloads, validation and deletion.


## Storage quotas and retention

- Up to 500 saved QR records per visitor.
- A site-wide cap of 20,000 saved records.
- New QR records expire after 365 days; existing records have no expiry date and are not deleted automatically.
- A daily Vercel Cron job calls `/api/cron/cleanup` at 04:00 UTC. Vercel must supply `CRON_SECRET` in the Authorization header. The job deletes at most 1,000 expired rows per run.
- Once a saved record expires, it disappears from the user's collection and cannot be downloaded again from the application. Previously downloaded QR image files continue to work because they embed the destination URL directly.

## Database configuration and tests

Set `DATABASE_URL` for Production to the production Neon branch. Set a *separate* Preview `DATABASE_URL` to the Neon development branch; never point Preview at Production.

Tests are integration tests against PostgreSQL, not SQLite. Set `TEST_DATABASE_URL` to an isolated disposable Neon development branch and run `python -m pytest -q`. Do not use the Production branch for `TEST_DATABASE_URL`; the tests create and delete their own temporary QR records.

The `app.py` import initializes the schema, so ensure that the database credentials point to the intended branch before running tests or deploying.

## Hosting

Deploy on Vercel with Python/Flask and a Neon PostgreSQL database. Configure `DATABASE_URL`, `CRON_SECRET`, and `COOKIE_SECURE=1` in production. For Preview deployments, use a non-production database.
