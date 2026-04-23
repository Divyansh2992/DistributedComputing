import os
import uuid
import sqlite3
import bcrypt
import jwt
import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)
SECRET_KEY = os.environ.get('JWT_SECRET', 'shard_secret_key_2024_distributed')
DB_PATH = '/data/users.db'
os.makedirs('/data', exist_ok=True)

TOKEN_TTL_HOURS = 24


# ─── Database ─────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id       TEXT PRIMARY KEY,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'user',
            created_at    TEXT
        )
    ''')
    conn.commit()

    # Seed default admin account if not already present
    existing = conn.execute(
        'SELECT user_id FROM users WHERE username=?', ('admin',)
    ).fetchone()
    if not existing:
        pw_hash = bcrypt.hashpw(b'admin123', bcrypt.gensalt()).decode('utf-8')
        conn.execute(
            'INSERT INTO users VALUES (?,?,?,?,?)',
            (str(uuid.uuid4()), 'admin', pw_hash, 'admin',
             datetime.datetime.utcnow().isoformat())
        )
        conn.commit()
        print('[AUTH] Default admin account created (admin / admin123)')

    conn.close()


init_db()


# ─── JWT helpers ──────────────────────────────────────────────────────────────

def make_token(user_id, username, role):
    payload = {
        'sub':      user_id,
        'username': username,
        'role':     role,
        'iat':      datetime.datetime.utcnow(),
        'exp':      datetime.datetime.utcnow() + datetime.timedelta(hours=TOKEN_TTL_HOURS)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm='HS256')


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'auth_service'})


# ── Internal service-to-service token (used by orchestrator internals) ─────────
@app.route('/token', methods=['POST'])
def issue_token():
    """Legacy endpoint: issues a short-lived system token for inter-service calls."""
    payload = {
        'sub':      'orchestrator',
        'username': 'orchestrator',
        'role':     'admin',
        'iat':      datetime.datetime.utcnow(),
        'exp':      datetime.datetime.utcnow() + datetime.timedelta(hours=24)
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm='HS256')
    return jsonify({'token': token})


# ── Register ──────────────────────────────────────────────────────────────────
@app.route('/auth/register', methods=['POST'])
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()

    if not username or not password:
        return jsonify({'error': 'username and password are required'}), 400
    if len(username) < 3:
        return jsonify({'error': 'username must be at least 3 characters'}), 400
    if len(password) < 6:
        return jsonify({'error': 'password must be at least 6 characters'}), 400

    pw_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    user_id = str(uuid.uuid4())

    conn = get_db()
    try:
        conn.execute(
            'INSERT INTO users VALUES (?,?,?,?,?)',
            (user_id, username, pw_hash, 'user',
             datetime.datetime.utcnow().isoformat())
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'Username already taken'}), 409
    conn.close()

    token = make_token(user_id, username, 'user')
    print(f'[AUTH] Registered: {username} ({user_id})')
    return jsonify({'token': token, 'user_id': user_id, 'username': username, 'role': 'user'}), 201


# ── Login ─────────────────────────────────────────────────────────────────────
@app.route('/auth/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()

    if not username or not password:
        return jsonify({'error': 'username and password are required'}), 400

    conn = get_db()
    row = conn.execute(
        'SELECT * FROM users WHERE username=?', (username,)
    ).fetchone()
    conn.close()

    if not row:
        return jsonify({'error': 'Invalid username or password'}), 401

    if not bcrypt.checkpw(password.encode('utf-8'), row['password_hash'].encode('utf-8')):
        return jsonify({'error': 'Invalid username or password'}), 401

    token = make_token(row['user_id'], row['username'], row['role'])
    print(f'[AUTH] Login: {username} role={row["role"]}')
    return jsonify({
        'token':    token,
        'user_id':  row['user_id'],
        'username': row['username'],
        'role':     row['role']
    })


# ── Verify ────────────────────────────────────────────────────────────────────
@app.route('/verify', methods=['POST'])
def verify_token():
    data  = request.get_json(silent=True) or {}
    token = data.get('token', '')
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        return jsonify({'valid': True, 'payload': payload})
    except jwt.ExpiredSignatureError:
        return jsonify({'valid': False, 'error': 'Token expired'}), 401
    except jwt.InvalidTokenError as e:
        return jsonify({'valid': False, 'error': str(e)}), 401


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
