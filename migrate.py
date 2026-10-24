"""Run additive schema migrations explicitly, never from Flask cold starts.

Requires MIGRATION_DATABASE_URL pointing to the target Neon *direct*
(unpooled) connection string. Run: python migrate.py --apply
Existing rows, including records without an expiration, are preserved.
"""
import argparse
import os

import psycopg

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS codes (
        id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        color TEXT NOT NULL,
        created TEXT NOT NULL,
        expires_at TIMESTAMPTZ
    )
    """,
    "ALTER TABLE codes ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS codes_owner ON codes(owner, created)",
    """CREATE INDEX IF NOT EXISTS codes_expires_at
       ON codes(expires_at) WHERE expires_at IS NOT NULL""",
)


def migrate_schema(database_url):
    # DDL uses Neon's direct connection, not its transaction pooler.
    with psycopg.connect(
        database_url,
        connect_timeout=10,
        prepare_threshold=None,
        autocommit=True,
        application_name='qr-please-migrate',
    ) as db:
        with db.transaction():
            db.execute("SELECT set_config('lock_timeout', %s, true)", ('5000',))
            for statement in SCHEMA_STATEMENTS:
                db.execute(statement)


def main():
    parser = argparse.ArgumentParser(description='Apply safe QR Please schema migrations')
    parser.add_argument('--apply', action='store_true', help='required before any DDL')
    args = parser.parse_args()
    if not args.apply:
        parser.error('No changes made. Specify --apply to run the migrations.')
    url = os.environ.get('MIGRATION_DATABASE_URL')
    if not url:
        parser.error('MIGRATION_DATABASE_URL must be set to the intended Neon direct URL.')
    migrate_schema(url)
    print('QR Please schema migration completed; existing QR rows were preserved.')


if __name__ == '__main__':
    main()
