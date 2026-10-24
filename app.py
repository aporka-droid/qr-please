import hashlib
import io
import os
import re
import secrets
import hmac
import psycopg
from datetime import datetime, timezone
from urllib.parse import urlsplit

import qrcode
import qrcode.image.svg
from flask import Flask, g, jsonify, render_template, request, send_file
from db_utils import bounded_transaction, connect_database

# Storage quotas prevent visitors from bypassing per-browser limits by
# discarding their anonymous cookie. Existing records are grandfathered in.
MAX_CODES_PER_VISITOR = 500
MAX_CODES_TOTAL = 20_000
RETENTION_DAYS = 365
CLEANUP_BATCH_SIZE = 1_000
QUOTA_LOCK_ID = 432741901  # stable PostgreSQL transaction advisory lock


def create_app(database=None):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024
    database_url = database or os.environ["DATABASE_URL"]

    # No SQL at import or cold start. Schema updates belong to migrate.py.
    def connection():
        if 'db' not in g:
            g.db = connect_database(database_url)
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
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "script-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'"
        )
        return response

    @app.teardown_appcontext
    def close_db(error):
        db = g.pop('db', None)
        if db is not None:
            db.close()

    @app.errorhandler(psycopg.OperationalError)
    @app.errorhandler(psycopg.errors.QueryCanceled)
    @app.errorhandler(psycopg.errors.LockNotAvailable)
    def database_unavailable(error):
        app.logger.warning('Database temporarily unavailable: %s', type(error).__name__)
        return jsonify(error='Database temporarily unavailable. Please retry.'), 503

    # TEMPORARY preview-only diagnostic; remove before merging PR #2.
    if os.environ.get('VERCEL_ENV') == 'preview':
        @app.get('/__temporary_pooler_check')
        def _temporary_pooler_check():
            from urllib.parse import urlsplit
            hostname = (urlsplit(database_url).hostname or '').lower()
            db = connection()
            with bounded_transaction(db):
                alive = db.execute('SELECT 1 AS ok').fetchone()['ok'] == 1
            return jsonify(
                database_reachable=alive,
                neon_pooled_host=('-pooler.' in hostname or hostname.endswith('-pooler')),
            )

    @app.get('/')
    def index():
        return render_template('index.html')

    @app.get('/api/codes')
    def history():
        db = connection()
        with bounded_transaction(db):
            rows = db.execute(
                'SELECT id, title, url, color, created FROM codes WHERE owner = %s AND (expires_at IS NULL OR expires_at > NOW()) ORDER BY created DESC',
                (g.owner,),
            ).fetchall()
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
        # Serialize the COUNT + INSERT checks across serverless instances.
        # A single PostgreSQL lock covers both visitor and global quotas.
        with bounded_transaction(db):
            db.execute('SELECT pg_advisory_xact_lock(%s)', (QUOTA_LOCK_ID,))
            visitor_count = db.execute(
                'SELECT COUNT(*) AS count FROM codes WHERE owner = %s',
                (g.owner,)
            ).fetchone()['count']
            if visitor_count >= MAX_CODES_PER_VISITOR:
                return jsonify(error='Your history is full. Delete an old code first.'), 400

            total_count = db.execute('SELECT COUNT(*) AS count FROM codes').fetchone()['count']
            if total_count >= MAX_CODES_TOTAL:
                return jsonify(error='QR storage is temporarily full. Please try again later.'), 503

            item = dict(id=secrets.token_hex(16), title=title or parsed.hostname,
                        url=url, color=color, created=datetime.now(timezone.utc).isoformat())
            db.execute(
                """
                INSERT INTO codes (id, owner, title, url, color, created, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW() + %s * INTERVAL '1 day')
                """,
                (item['id'], g.owner, item['title'], url, color, item['created'], RETENTION_DAYS)
            )
        return jsonify(item), 201

    @app.get('/api/codes/<code_id>/<filetype>')
    def download(code_id, filetype):
        db = connection()
        with bounded_transaction(db):
            row = db.execute(
                'SELECT * FROM codes WHERE id = %s AND owner = %s AND (expires_at IS NULL OR expires_at > NOW())',
                (code_id, g.owner),
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
        with bounded_transaction(db):
            cursor = db.execute(
                'DELETE FROM codes WHERE id = %s AND owner = %s',
                (code_id, g.owner),
            )
            removed = cursor.rowcount
        return (jsonify(ok=True), 200) if removed else (jsonify(error='Code not found.'), 404)

    @app.get('/api/cron/cleanup')
    def cleanup_expired_codes():
        # Vercel Cron sends CRON_SECRET in the Authorization bearer header.
        secret = os.environ.get('CRON_SECRET')
        if not secret:
            return jsonify(error='Cleanup is not configured.'), 503
        provided = request.headers.get('Authorization', '')
        if not hmac.compare_digest(provided, f'Bearer {secret}'):
            return jsonify(error='Unauthorized.'), 401

        db = connection()
        with bounded_transaction(db, query_timeout_ms=25_000):
            # Bounded batch prevents a large expiry backlog from timing out.
            deleted = db.execute(
                """
                WITH expired AS (
                    SELECT id FROM codes
                    WHERE expires_at <= NOW()
                    ORDER BY expires_at
                    LIMIT %s
                )
                DELETE FROM codes USING expired WHERE codes.id = expired.id
                """,
                (CLEANUP_BATCH_SIZE,)
            ).rowcount
        return jsonify(ok=True, deleted=deleted)

    return app


app = create_app()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000)
