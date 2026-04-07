import os
import jwt
import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)
SECRET_KEY = os.environ.get('JWT_SECRET', 'shard_secret_key_2024_distributed')


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'auth_service'})


@app.route('/token', methods=['POST'])
def issue_token():
    payload = {
        'sub': 'orchestrator',
        'iat': datetime.datetime.utcnow(),
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm='HS256')
    return jsonify({'token': token})


@app.route('/verify', methods=['POST'])
def verify_token():
    data = request.get_json()
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
