import os
import sqlite3
import json
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)
DB_PATH = '/data/metadata.db'
os.makedirs('/data', exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')   # WAL mode allows concurrent reads
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS files (
            file_id       TEXT PRIMARY KEY,
            filename      TEXT NOT NULL,
            original_size INTEGER DEFAULT 0,
            content_type  TEXT DEFAULT 'application/octet-stream',
            created_at    TEXT,
            shard_count   INTEGER DEFAULT 3,
            chunk_sizes   TEXT DEFAULT '[]',
            owner_id      TEXT DEFAULT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS shards (
            shard_id         TEXT PRIMARY KEY,
            file_id          TEXT,
            shard_index      INTEGER,
            node_url         TEXT,
            node_name        TEXT,
            hash             TEXT,
            is_parity        INTEGER DEFAULT 0,
            parity_for_index INTEGER,
            FOREIGN KEY(file_id) REFERENCES files(file_id)
        )
    ''')
    # Migration: add chunk_sizes column if it doesn't exist (backwards compat)
    try:
        conn.execute('ALTER TABLE files ADD COLUMN chunk_sizes TEXT DEFAULT "[]"')
    except Exception:
        pass
    # Migration: add owner_id column if it doesn't exist (backwards compat)
    try:
        conn.execute('ALTER TABLE files ADD COLUMN owner_id TEXT DEFAULT NULL')
    except Exception:
        pass
    conn.commit()
    conn.close()


init_db()


def row_to_shard(s):
    d = dict(s)
    d['is_parity'] = bool(d['is_parity'])
    return d


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'metadata_db'})


@app.route('/files', methods=['POST'])
def create_file():
    data = request.get_json()
    conn = get_db()
    chunk_sizes_json = json.dumps(data.get('chunk_sizes', []))
    owner_id = data.get('owner_id')   # NEW: persist owner
    conn.execute(
        'INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?)',
        (data['file_id'], data['filename'], data.get('original_size', 0),
         data.get('content_type', 'application/octet-stream'),
         datetime.utcnow().isoformat(), data.get('shard_count', 3),
         chunk_sizes_json, owner_id)
    )
    for shard in data.get('shards', []):
        conn.execute(
            'INSERT OR REPLACE INTO shards VALUES (?,?,?,?,?,?,?,?)',
            (shard['shard_id'], data['file_id'], shard['shard_index'],
             shard['node_url'], shard['node_name'], shard['hash'],
             1 if shard.get('is_parity') else 0,
             shard.get('parity_for_index'))
        )
    conn.commit()
    conn.close()
    return jsonify({'status': 'created', 'file_id': data['file_id']})


@app.route('/files', methods=['GET'])
def list_files():
    """
    Return files list.
    - ?owner=<user_id>  → only that user's files
    - no param          → all files (admin view)
    """
    owner = request.args.get('owner')
    conn  = get_db()
    if owner:
        files = conn.execute(
            'SELECT * FROM files WHERE owner_id=? ORDER BY created_at DESC',
            (owner,)
        ).fetchall()
    else:
        files = conn.execute(
            'SELECT * FROM files ORDER BY created_at DESC'
        ).fetchall()

    result = []
    for f in files:
        fd = dict(f)
        fd['chunk_sizes'] = json.loads(fd.get('chunk_sizes') or '[]')
        shards = conn.execute(
            'SELECT * FROM shards WHERE file_id=?', (f['file_id'],)
        ).fetchall()
        fd['shards'] = [row_to_shard(s) for s in shards]
        result.append(fd)
    conn.close()
    return jsonify(result)


@app.route('/files/<file_id>', methods=['GET'])
def get_file(file_id):
    conn = get_db()
    f = conn.execute('SELECT * FROM files WHERE file_id=?', (file_id,)).fetchone()
    if not f:
        conn.close()
        return jsonify({'error': 'File not found'}), 404
    fd = dict(f)
    fd['chunk_sizes'] = json.loads(fd.get('chunk_sizes') or '[]')
    shards = conn.execute('SELECT * FROM shards WHERE file_id=?', (file_id,)).fetchall()
    fd['shards'] = [row_to_shard(s) for s in shards]
    conn.close()
    return jsonify(fd)


@app.route('/files/<file_id>', methods=['DELETE'])
def delete_file(file_id):
    conn = get_db()
    conn.execute('DELETE FROM shards WHERE file_id=?', (file_id,))
    conn.execute('DELETE FROM files WHERE file_id=?', (file_id,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'deleted'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5005)
