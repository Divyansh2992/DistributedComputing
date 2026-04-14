"""
ShardVault LAN — Metadata DB Service
======================================
Run on Laptop 1 (MASTER):
  python metadata_db.py

Stores file metadata, shard locations, hashes, parity info in SQLite.
Data path: ./data/metadata.db  (or META_DB_PATH env var)
"""

import os
import sys
import sqlite3
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime

DB_PATH = os.environ.get('META_DB_PATH', './data/metadata.db')
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

app = Flask(__name__)
CORS(app)


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
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
            chunk_sizes   TEXT DEFAULT '[]'
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
    try:
        conn.execute('ALTER TABLE files ADD COLUMN chunk_sizes TEXT DEFAULT "[]"')
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
    conn.execute(
        'INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)',
        (data['file_id'], data['filename'], data.get('original_size', 0),
         data.get('content_type', 'application/octet-stream'),
         datetime.utcnow().isoformat(), data.get('shard_count', 3),
         json.dumps(data.get('chunk_sizes', [])))
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
    conn   = get_db()
    files  = conn.execute('SELECT * FROM files ORDER BY created_at DESC').fetchall()
    result = []
    for f in files:
        fd = dict(f)
        fd['chunk_sizes'] = json.loads(fd.get('chunk_sizes') or '[]')
        shards = conn.execute('SELECT * FROM shards WHERE file_id=?', (f['file_id'],)).fetchall()
        fd['shards'] = [row_to_shard(s) for s in shards]
        result.append(fd)
    conn.close()
    return jsonify(result)


@app.route('/files/<file_id>', methods=['GET'])
def get_file(file_id):
    conn = get_db()
    f    = conn.execute('SELECT * FROM files WHERE file_id=?', (file_id,)).fetchone()
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
    conn.execute('DELETE FROM files  WHERE file_id=?', (file_id,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'deleted'})


if __name__ == '__main__':
    port = int(os.environ.get('META_PORT', 5005))
    print(f'[Metadata DB] Listening on http://0.0.0.0:{port}  |  DB: {DB_PATH}')
    app.run(host='0.0.0.0', port=port, debug=False)
