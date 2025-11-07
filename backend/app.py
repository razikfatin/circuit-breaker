from flask import Flask, jsonify, request, make_response
from models import User
import random
import time

app = Flask(__name__)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route('/api/users', methods=['GET'])
def get_users():
    return jsonify(User), 200

@app.route('/api/users/<id>', methods=['GET'])
def get_user(id):
    if id in User:
        user = User.get(id)
        return jsonify(user), 200
    else:
        return jsonify({'error': 'User not found'}), 404

@app.route('/api/users', methods=['POST'])
def create_user():
    try:
        data = request.get_json()
        if not data:
            raise ValueError("No JSON data provided")
    except Exception as e:
        print(f"Error parsing JSON: {e}")
        return jsonify({'error': 'Invalid JSON'}), 400
    print(data)
    user_id = str(data["id"])
    if user_id in User:
        return jsonify({'error': 'User already exists'}), 400
    User[user_id] = {"id": user_id, "name": data["name"], "email": data["email"]}
    return jsonify(User[user_id]), 201

@app.route('/api/users/<id>', methods=['PUT'])
def update_user(id):
    data = request.get_json()
    if(id in User):
        user = User.get(id)
        user.update(data)
        return jsonify(user), 200
    else:
        return jsonify({'error': 'User not found'}), 404
    
@app.route('/api/users/<id>', methods=['DELETE'])
def delete_user(id):
    if id in User:
        User.pop(id)
        return jsonify({'message': 'User deleted'}), 200
    else:
        return jsonify({'error': 'User not found'}), 404  
    
@app.route("/delayed/user", methods=["GET"])
def test_endpoint():
    FAILURE_RATE = 0.5
    TIMEOUT_RATE = 0.3
    DELAY_RATE   = 0.5
    MIN_DELAY_MS = 50
    MAX_DELAY_MS = 1200
    SIM_HEADER   = "X-Simulated-Behavior"

    if random.random() < TIMEOUT_RATE:
        resp = make_response("Simulated timeout", 408)
        resp.headers[SIM_HEADER] = "timeout"
        return resp

    if random.random() < FAILURE_RATE:
        resp = make_response("Simulated internal server error", 500)
        resp.headers[SIM_HEADER] = "error"
        return resp

    simulated = None
    if random.random() < DELAY_RATE:
        ms = random.randint(MIN_DELAY_MS, MAX_DELAY_MS)
        time.sleep(ms / 1000.0)
        simulated = {"type": "delay", "ms": ms}
    try:
        payload = User
    except NameError:
        payload = [{"id": 1, "name": "Example User"}]

    resp = make_response(jsonify(payload), 200)
    resp.headers[SIM_HEADER] = "success" if simulated is None else "delay"
    return resp  

if __name__ == '__main__':
    print("Starting Flask server...")
    app.run(host="0.0.0.0", debug=True, port=5000)
