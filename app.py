import hashlib
import io
import os
import re
import secrets
import psycopg
from psycopg.rows import dict_row
from datetime import datetime, timezone
from urllib.parse import urlsplit

import qrcode
import qrcode.image.svg
from flask import Flask, g, jsonify, render_template, request, send_file


def create_app(database=None):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024
    database_url = os.environ["DATABASE_URL"]

    with psycopg.connect(database_url) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS codes (
                id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                color TEXT NOT NULL,
                created TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS codes_owner
            ON codes(owner, created)
        """)

    def connection():
        if 'db' not in g:
            g.db = psycopg.connect(
                database_url,
                row_factory=dict_row
            )
        return g.db

    @app.before_request
    def identify():
        token = request.cookies.get('qr_visitor', '')
        if not re.fullmatch(r'[a-f0-9]{64}', token):
            token = secrets.token_hex(32)
        g.visitor = token
        g.owner = hashlib.sha256(token.encode()).hexdigest()
        if request.method in ('POST', 'DELETE') and request.headers.get('X-QR-Request') != '1':
            return jsonify(error='Invalid request.'), 403

    @app.after_request
    def headers(response):
        response.set_cookie('qr_visitor', g.visitor, max_age=60 * 60 * 24 * 365,
                            httponly=True, secure=os.environ.get('COOKIE_SECURE') == '1',
                            samesite='Lax')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        return response

    @app.teardown_appcontext
    def close_db(error):
        db = g.pop('db', None)
        if db:
            db.close()

    @app.get('/')
    def index():
        return render_template('index.html')

    @app.get('/api/codes')
    def history():
        rows = connection().execute(
            'SELECT id, title, url, color, created FROM codes WHERE owner = %s ORDER BY created DESC',
            (g.owner,)
        )
        return jsonify([dict(row) for row in rows])

    @app.post('/api/codes')
    def create():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error='Send a valid URL.'), 400
        url = data.get('url', '')
        title = data.get('title', '')
        color = data.get('color', '#172329')
        if not all(isinstance(value, str) for value in (url, title, color)):
            return jsonify(error='Invalid input.'), 400
        url, title = url.strip(), title.strip()
        if not url or len(url.encode('utf-8')) > 1500 or any(c.isspace() or ord(c) < 32 for c in url):
            return jsonify(error='Enter a URL without spaces, up to 1,500 bytes.'), 400
        if '://' not in url:
            url = 'https://' + url
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError()
            parsed.port
        except ValueError:
            return jsonify(error='Enter a valid http or https URL without login details.'), 400
        if color not in ('#172329', '#174f43', '#203d80', '#793442') or len(title) > 80:
            return jsonify(error='Choose a supported color and a title under 81 characters.'), 400
        db = connection()
        if db.execute(
            'SELECT COUNT(*) FROM codes WHERE owner = %s',
            (g.owner,)
        ).fetchone()['count'] >= 500:
            return jsonify(error='Your history is full. Delete an old code first.'), 400
        item = dict(id=secrets.token_hex(16), title=title or parsed.hostname,
                    url=url, color=color, created=datetime.now(timezone.utc).isoformat())
        db.execute('INSERT INTO codes VALUES (%s, %s, %s, %s, %s, %s)',
                   (item['id'], g.owner, item['title'], url, color, item['created']))
        db.commit()
        return jsonify(item), 201

    @app.get('/api/codes/<code_id>/<filetype>')
    def download(code_id, filetype):
        row = connection().execute(
            'SELECT * FROM codes WHERE id = %s AND owner = %s',
            (code_id, g.owner)
        ).fetchone()
        if row is None or filetype not in ('png', 'svg'):
            return jsonify(error='Code not found.'), 404
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=16, border=4)
        qr.add_data(row['url'])
        qr.make(fit=True)
        output = io.BytesIO()
        if filetype == 'svg':
            # SvgPathFillImage retains an opaque white quiet zone.
            image = qr.make_image(image_factory=qrcode.image.svg.SvgPathFillImage)
            image.save(output)
            svg = output.getvalue().replace(b'fill="#000000"', ('fill="' + row['color'] + '"').encode())
            output = io.BytesIO(svg)
        else:
            qr.make_image(fill_color=row['color'], back_color='white').save(output, format='PNG')
        output.seek(0)
        return send_file(output, mimetype='image/svg+xml' if filetype == 'svg' else 'image/png',
                         as_attachment=request.args.get('download') == '1', download_name=f'qr-{code_id[:8]}.{filetype}')

    @app.delete('/api/codes/<code_id>')
    def delete(code_id):
        db = connection()
        cursor = db.execute(
            'DELETE FROM codes WHERE id = %s AND owner = %s',
            (code_id, g.owner)
        )
        db.commit()
        return (jsonify(ok=True), 200) if cursor.rowcount else (jsonify(error='Code not found.'), 404)

    return app


app = create_app()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000)
