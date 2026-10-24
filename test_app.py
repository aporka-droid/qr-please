"""Integration tests. Run only with a dedicated Neon TEST_DATABASE_URL.

These tests intentionally refuse to run without an explicit test database.
Never point TEST_DATABASE_URL at the production branch.
"""
import importlib
import os

import psycopg
import pytest

HEADERS = {'X-QR-Request': '1'}


@pytest.fixture(scope='module')
def test_url():
    value = os.environ.get('TEST_DATABASE_URL')
    if not value:
        pytest.skip('Set TEST_DATABASE_URL to an isolated Neon development branch')
    if value == os.environ.get('DATABASE_URL'):
        pytest.fail('TEST_DATABASE_URL must not equal DATABASE_URL')
    if not value.startswith(('postgresql://', 'postgres://')):
        pytest.fail('TEST_DATABASE_URL must be a PostgreSQL connection string')
    return value


@pytest.fixture(scope='module')
def app_module(test_url):
    # Migrations run explicitly against this isolated test branch.
    # Importing Flask no longer performs any schema DDL.
    from migrate import migrate_schema
    migrate_schema(test_url)
    os.environ['DATABASE_URL'] = test_url
    return importlib.import_module('app')


@pytest.fixture
def app(app_module, test_url):
    application = app_module.create_app(test_url)
    application.config['TESTING'] = True
    return application


def _new_code(client, url='example.com'):
    response = client.post('/api/codes', json={'url': url}, headers=HEADERS)
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()


def _remove(client, code_id):
    response = client.delete(f'/api/codes/{code_id}', headers=HEADERS)
    assert response.status_code == 200


def test_persistence_downloads_and_isolation(app):
    owner = app.test_client()
    stranger = app.test_client()
    item = _new_code(owner)
    code_id = item['id']
    try:
        assert item['url'] == 'https://example.com'
        assert owner.get(f'/api/codes/{code_id}/png').data.startswith(b'\\x89PNG')
        svg = owner.get(f'/api/codes/{code_id}/svg?download=1')
        assert b'<svg' in svg.data
        assert 'attachment' in svg.headers['Content-Disposition']
        assert all(record['id'] != code_id for record in stranger.get('/api/codes').get_json())
        assert stranger.get(f'/api/codes/{code_id}/png').status_code == 404
        assert stranger.delete(f'/api/codes/{code_id}', headers=HEADERS).status_code == 404
        returning = app.test_client()
        returning.set_cookie('qr_visitor', owner.get_cookie('qr_visitor').value)
        assert any(record['id'] == code_id for record in returning.get('/api/codes').get_json())
    finally:
        _remove(owner, code_id)


def test_input_validation(app):
    client = app.test_client()
    for url in ('javascript://alert(1)', 'https://',
                'https://user:pass@example.com', 'https://bad host.com',
                'https://example.com:wrong'):
        response = client.post('/api/codes', json={'url': url}, headers=HEADERS)
        assert response.status_code == 400
    assert client.post('/api/codes', json={'url': 'example.com'}).status_code == 403
    assert client.post('/api/codes', json=['bad'], headers=HEADERS).status_code == 400
    assert client.get('/').status_code == 200


def test_atomic_visitor_quota(app, app_module, monkeypatch):
    client = app.test_client()
    monkeypatch.setattr(app_module, 'MAX_CODES_PER_VISITOR', 2)
    created = []
    try:
        created.append(_new_code(client)['id'])
        created.append(_new_code(client)['id'])
        response = client.post('/api/codes', json={'url': 'third.example'}, headers=HEADERS)
        assert response.status_code == 400
        assert len([r for r in client.get('/api/codes').get_json()
                    if r['id'] in created]) == 2
    finally:
        for code_id in created:
            _remove(client, code_id)


def test_global_quota(app, app_module, monkeypatch, test_url):
    # Set the temporary quota equal to the current count. No existing row changes.
    with psycopg.connect(test_url) as db:
        current = db.execute('SELECT COUNT(*) FROM codes').fetchone()[0]
    monkeypatch.setattr(app_module, 'MAX_CODES_TOTAL', current)
    client = app.test_client()
    response = client.post('/api/codes', json={'url': 'example.com'}, headers=HEADERS)
    assert response.status_code == 503


def test_expired_codes_hidden_and_legacy_codes_preserved(app, test_url):
    client = app.test_client()
    expired = _new_code(client, 'expired.example')
    legacy = _new_code(client, 'legacy.example')
    try:
        # Update only rows created by this test, never any existing records.
        with psycopg.connect(test_url) as db:
            db.execute("UPDATE codes SET expires_at = NOW() - INTERVAL '1 day' WHERE id = %s",
                       (expired['id'],))
            db.execute('UPDATE codes SET expires_at = NULL WHERE id = %s', (legacy['id'],))
        ids = {r['id'] for r in client.get('/api/codes').get_json()}
        assert expired['id'] not in ids
        assert legacy['id'] in ids
        assert client.get(f"/api/codes/{expired['id']}/png").status_code == 404
        assert client.get(f"/api/codes/{legacy['id']}/png").status_code == 200
    finally:
        _remove(client, expired['id'])
        _remove(client, legacy['id'])


def test_cleanup_requires_secret(app, monkeypatch):
    monkeypatch.setenv('CRON_SECRET', 'test-cleanup-secret')
    client = app.test_client()
    assert client.get('/api/cron/cleanup').status_code == 401
    assert client.get('/api/cron/cleanup',
                      headers={'Authorization': 'Bearer wrong'}).status_code == 401
