"""Offline regression tests for database connection behavior (no Neon required)."""
from contextlib import nullcontext
import importlib

import psycopg

import db_utils


def test_connect_database_uses_pooler_safe_settings(monkeypatch):
    calls = {}

    def fake_connect(database_url, **kwargs):
        calls['url'] = database_url
        calls['opts'] = kwargs
        return object()

    monkeypatch.setattr(db_utils.psycopg, 'connect', fake_connect)
    db_utils.connect_database('postgresql://example.invalid/test')
    assert calls['url'] == 'postgresql://example.invalid/test'
    assert calls['opts']['connect_timeout'] == 5
    assert calls['opts']['autocommit'] is True
    assert calls['opts']['prepare_threshold'] is None


def test_timeout_settings_are_transaction_local():
    class FakeDB:
        def __init__(self):
            self.statements = []

        def transaction(self):
            self.statements.append('BEGIN')
            return nullcontext()

        def execute(self, sql, values=None):
            self.statements.append((sql, values))

    db = FakeDB()
    with db_utils.bounded_transaction(db, query_timeout_ms=12000,
                                      lock_timeout_ms=2000):
        db.execute('SELECT 42')
    assert db.statements[0] == 'BEGIN'
    assert db.statements[1][0] == (
        "SELECT set_config('statement_timeout', %s, true)"
    )
    assert db.statements[1][1] == ('12000',)
    assert db.statements[2][0] == (
        "SELECT set_config('lock_timeout', %s, true)"
    )
    assert db.statements[2][1] == ('2000',)
    assert db.statements[3][0] == 'SELECT 42'


def test_app_does_not_connect_during_import_or_startup(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://example.invalid/test')

    def should_not_connect(*args, **kwargs):
        raise AssertionError('Flask startup must not connect to the database')

    monkeypatch.setattr(psycopg, 'connect', should_not_connect)
    app_module = importlib.import_module('app')
    instance = app_module.create_app('postgresql://example.invalid/test')
    response = instance.test_client().get('/')
    assert response.status_code == 200


def test_unavailable_database_returns_nonrevealing_503(monkeypatch):
    app_module = importlib.import_module('app')

    def fail_connection(url):
        raise psycopg.OperationalError('Sensitive upstream connection details')

    monkeypatch.setattr(app_module, 'connect_database', fail_connection)
    app = app_module.create_app('postgresql://example.invalid/test')
    response = app.test_client().get('/api/codes')
    assert response.status_code == 503
    assert response.get_json() == {
        'error': 'Database temporarily unavailable. Please retry.'
    }
    assert b'Sensitive upstream' not in response.data
