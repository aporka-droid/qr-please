from app import create_app

HEADERS = {'X-QR-Request': '1'}


def test_persistence_downloads_and_isolation(tmp_path):
    path = tmp_path / 'test.sqlite3'
    app = create_app(path)
    owner = app.test_client()
    stranger = app.test_client()
    response = owner.post('/api/codes', json={'url': 'example.com', 'color': '#174f43'}, headers=HEADERS)
    assert response.status_code == 201
    item = response.get_json()
    assert item['url'] == 'https://example.com'
    key = item['id']
    png = owner.get(f'/api/codes/{key}/png')
    assert png.data.startswith(b'\x89PNG')
    svg = owner.get(f'/api/codes/{key}/svg?download=1')
    assert b'<svg' in svg.data and b'#174f43' in svg.data
    assert 'attachment' in svg.headers['Content-Disposition']
    assert stranger.get('/api/codes').get_json() == []
    assert stranger.get(f'/api/codes/{key}/png').status_code == 404
    assert stranger.delete(f'/api/codes/{key}', headers=HEADERS).status_code == 404
    returning = create_app(path).test_client()
    returning.set_cookie('qr_visitor', owner.get_cookie('qr_visitor').value)
    assert returning.get('/api/codes').get_json()[0]['id'] == key
    assert returning.delete(f'/api/codes/{key}', headers=HEADERS).status_code == 200
    assert returning.get('/api/codes').get_json() == []


def test_validation(tmp_path):
    client = create_app(tmp_path / 'validation.sqlite3').test_client()
    for url in ('javascript://alert(1)', 'https://', 'https://user:pass@example.com', 'https://bad host.com', 'https://example.com:wrong'):
        assert client.post('/api/codes', json={'url': url}, headers=HEADERS).status_code == 400
    assert client.post('/api/codes', json={'url': 'example.com'}).status_code == 403
    assert client.post('/api/codes', json=['bad'], headers=HEADERS).status_code == 400
    assert client.get('/').status_code == 200
