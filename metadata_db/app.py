import os
import sqlite3
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)
DB_PATH = '/data/metadata.db'
os.makedirs('/data', exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS files (
            file_id      TEXT PRIMARY KEY,
            filename     TEXT NOT NULL,
            original_size INTEGER DEFAULT 0,
            content_type TEXT DEFAULT 'application/octet-stream',
            created_at   TEXT,
            shard_count  INTEGER DEFAULT 3
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
        'INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)',
        (data['file_id'], data['filename'], data.get('original_size', 0),
         data.get('content_type', 'application/octet-stream'),
         datetime.utcnow().isoformat(), data.get('shard_count', 3))
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
    conn = get_db()
    files = conn.execute('SELECT * FROM files ORDER BY created_at DESC').fetchall()
    result = []
    for f in files:
        fd = dict(f)
        shards = conn.execute('SELECT * FROM shards WHERE file_id=?', (f['file_id'],)).fetchall()
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
