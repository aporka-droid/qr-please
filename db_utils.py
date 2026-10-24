"""Request-scoped Neon/PostgreSQL connections and bounded transactions.

Use Neon's pooled connection string for DATABASE_URL on Vercel.
Do not keep an in-process psycopg_pool in a serverless function.
"""
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

CONNECT_TIMEOUT_SECONDS = 5
QUERY_TIMEOUT_MS = 10_000
LOCK_TIMEOUT_MS = 3_000


def connect_database(database_url):
    """One lazy connection per request; Flask closes it at teardown."""
    return psycopg.connect(
        database_url,
        row_factory=dict_row,
        autocommit=True,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        prepare_threshold=None,  # safe with PgBouncer transaction pooling
        application_name='qr-please',
    )


@contextmanager
def bounded_transaction(db, *, query_timeout_ms=QUERY_TIMEOUT_MS,
                        lock_timeout_ms=LOCK_TIMEOUT_MS):
    """Scope timeouts to this transaction, not the pooled PostgreSQL session.

    Avoid SET SESSION, which can leak across users of a transaction pooler.
    Explicit transactions preserve the atomic quota check and its advisory lock.
    """
    with db.transaction():
        db.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (str(query_timeout_ms),),
        )
        db.execute(
            "SELECT set_config('lock_timeout', %s, true)",
            (str(lock_timeout_ms),),
        )
        yield db
