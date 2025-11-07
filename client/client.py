from flask import Flask, jsonify, request
import requests
import os
import logging
import threading
import time

import pybreaker
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception_type,
)

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("client")

BACKEND = os.getenv("BACKEND_BASE_URL", "http://backend:5000")

CB_FAIL_MAX = int(os.getenv("CB_FAIL_MAX", "5"))
CB_RESET_TIMEOUT = int(os.getenv("CB_RESET_TIMEOUT", "5"))
HALF_OPEN_MAX_CONCURRENCY = int(os.getenv("HALF_OPEN_MAX_CONCURRENCY", "2"))

RETRY_ATTEMPTS = int(os.getenv("RETRY_ATTEMPTS", "3"))
RETRY_BACKOFF_MULT = float(os.getenv("RETRY_BACKOFF_MULT", "1.0"))
RETRY_MAX = float(os.getenv("RETRY_MAX", "10"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "3"))

circuit_state = {"state": "UNKNOWN", "last_changed": None, "fail_count": 0}
half_open_semaphore = threading.BoundedSemaphore(HALF_OPEN_MAX_CONCURRENCY)
# initial circuit state: reflect pybreaker default (CLOSED)
circuit_state = {"state": "CLOSED", "last_changed": time.time(), "fail_count": 0}
half_open_semaphore = threading.BoundedSemaphore(HALF_OPEN_MAX_CONCURRENCY)

class CBListener(pybreaker.CircuitBreakerListener):
    def state_change(self, cb, old_state, new_state):
        circuit_state["state"] = new_state.name
        circuit_state["last_changed"] = time.time()
        logger.info("CircuitBreaker state change: %s -> %s", old_state.name, new_state.name)
        if new_state.name == "HALF_OPEN":
            global half_open_semaphore
            half_open_semaphore = threading.BoundedSemaphore(HALF_OPEN_MAX_CONCURRENCY)

    def failure(self, cb, exc):
        # called when a call fails (counts towards fail_max)
        circuit_state["fail_count"] = getattr(cb, "_failure_count", circuit_state.get("fail_count", 0)) 
        logger.info("CB failure recorded, exception=%s, fail_count=%s", exc, circuit_state["fail_count"])

    def success(self, cb):
        # successful call: reset fail count info
        circuit_state["fail_count"] = 0
        logger.info("CB success recorded, fail_count reset")


breaker = pybreaker.CircuitBreaker(
    fail_max=CB_FAIL_MAX,
    reset_timeout=CB_RESET_TIMEOUT,
    listeners=[CBListener()],
)

retry_decorator = retry(
    reraise=True,
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    wait=wait_random_exponential(multiplier=RETRY_BACKOFF_MULT, max=RETRY_MAX),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
)

@retry_decorator
def call_backend_endpoint(endpoint: str, timeout=REQUEST_TIMEOUT):
    url = f"{BACKEND}{endpoint}"
    logger.info("Attempting backend call: %s", url)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp

@app.route("/")
def root():
    return jsonify({"status": "client running", "backend": BACKEND})

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok"}), 200

@app.route("/breaker-status", methods=["GET"])
def breaker_status():
    return jsonify({
        "state": circuit_state.get("state"),
        "last_changed": circuit_state.get("last_changed"),
        "fail_max": CB_FAIL_MAX,
        "reset_timeout": CB_RESET_TIMEOUT,
        "half_open_max_concurrency": HALF_OPEN_MAX_CONCURRENCY,
    }), 200

@app.route("/call/users", methods=["GET"])
def call_users():
    try:
        if circuit_state.get("state") == "HALF_OPEN":
            acquired = half_open_semaphore.acquire(blocking=False)
            if not acquired:
                logger.warning("Half-open concurrency limit reached -> fast-fail")
                return jsonify({"error": "circuit half-open (too busy), fallback"}), 503
            try:
                resp = breaker.call(lambda: call_backend_endpoint("/api/users"))
            finally:
                half_open_semaphore.release()
        else:
            resp = breaker.call(lambda: call_backend_endpoint("/api/users"))
        return (resp.text, resp.status_code, dict(resp.headers))
    except pybreaker.CircuitBreakerError as cb_err:
        logger.warning("Circuit open -> fast-failing: %s", str(cb_err))
        return jsonify({"error": "circuit open, fallback"}), 503
    except requests.exceptions.RequestException as e:
        logger.exception("RequestException after retries")
        return jsonify({"error": "request failed", "detail": str(e)}), 502
    except Exception as e:
        logger.exception("Unexpected error")
        return jsonify({"error": "internal error", "detail": str(e)}), 500

@app.route("/call/delayed", methods=["GET"])
def call_delayed():
    try:
        resp = breaker.call(lambda: call_backend_endpoint("/delayed/user", timeout=5))
        return (resp.text, resp.status_code, dict(resp.headers))
    except pybreaker.CircuitBreakerError as cb_err:
        return jsonify({"error": "circuit open, fallback"}), 503
    except requests.exceptions.RequestException as e:
        return jsonify({"error": "request failed", "detail": str(e)}), 502

if __name__ == "__main__":
    logger.info("Starting client on 0.0.0.0:8080, backend=%s", BACKEND)
    app.run(host="0.0.0.0", port=8080)
