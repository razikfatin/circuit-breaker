# circuit-breaker

A production-style resilience demo on **Kubernetes** implementing the **Circuit Breaker pattern** with exponential-backoff retries, half-open concurrency control, and automated **chaos engineering** experiments via Chaos Toolkit + Toxiproxy.

---

## What Problem This Solves

In distributed systems, a downstream service that becomes slow or unavailable can cascade failures across the entire system — threads block waiting for responses, queues fill up, and eventually everything fails together.

The **Circuit Breaker pattern** prevents this: after a threshold of failures, the circuit "opens" and requests fail immediately (fast-fail) rather than waiting for a timeout. After a cooldown, it enters **half-open** state, allows a limited number of probe requests through, and closes again if they succeed.

---

## Architecture

```
                    ┌──────────────────────────────────────────┐
                    │            Kubernetes Cluster            │
                    │                                          │
  HTTP Client ────► │  Frontend (client)  ──────────────────►  │──► Backend Service
                    │   pybreaker + tenacity                   │    (simulates failures)
                    │        │                                 │
                    │        │ (chaos experiments)             │
                    │   Toxiproxy ◄──── Chaos Toolkit          │
                    │   (network fault injection)              │
                    └──────────────────────────────────────────┘
```

**Components:**

| Service | Role |
|---------|------|
| `frontend` | Client service; wraps all outbound calls with a circuit breaker + retry logic |
| `backend` | Upstream service; randomly simulates failures, timeouts, and latency |
| `toxiproxy` | Network proxy that injects faults (latency, packet loss) on demand |
| Chaos Toolkit | Orchestrates chaos experiments and validates system behaviour |

---

## Circuit Breaker State Machine

```
             failures >= fail_max
  CLOSED ──────────────────────────► OPEN
    ▲                                  │
    │                                  │ reset_timeout elapsed
    │    probe succeeds                ▼
    └──────────────────────────── HALF-OPEN
                                       │
                           probe fails │
                                       ▼
                                      OPEN
```

| State | Behaviour |
|-------|-----------|
| **CLOSED** | All requests pass through normally |
| **OPEN** | Requests fast-fail immediately (no network call made) |
| **HALF-OPEN** | Limited probe requests allowed; semaphore limits concurrency to `HALF_OPEN_MAX_CONCURRENCY` |

---

## Resilience Stack

- **[pybreaker](https://github.com/danielfm/pybreaker)** — circuit breaker implementation with state change listeners
- **[tenacity](https://github.com/jd/tenacity)** — retry with randomised exponential backoff (`wait_random_exponential`)
- **`threading.BoundedSemaphore`** — limits concurrent requests in HALF_OPEN state to prevent re-tripping the breaker under load
- **[Toxiproxy](https://github.com/Shopify/toxiproxy)** — network fault injection (latency, jitter, timeouts) for local chaos testing
- **[Chaos Toolkit](https://chaostoolkit.org/)** — declarative chaos experiments with steady-state hypothesis validation and automatic rollback

---

## Configuration

All parameters are environment-variable driven — no code changes needed to tune behaviour.

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_BASE_URL` | `http://backend:5000` | Upstream service URL |
| `CB_FAIL_MAX` | `5` | Failures before circuit opens |
| `CB_RESET_TIMEOUT` | `5` | Seconds before OPEN → HALF_OPEN |
| `HALF_OPEN_MAX_CONCURRENCY` | `2` | Max concurrent probe requests in HALF_OPEN |
| `RETRY_ATTEMPTS` | `3` | Max retry attempts per request |
| `RETRY_BACKOFF_MULT` | `1.0` | Exponential backoff multiplier |
| `RETRY_MAX` | `10` | Max backoff wait in seconds |
| `REQUEST_TIMEOUT` | `3` | Per-request timeout in seconds |

---

## Getting Started

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- A local Kubernetes cluster ([Minikube](https://minikube.sigs.k8s.io/) or [Kind](https://kind.sigs.k8s.io/))
- Python 3.9+ (for running chaos experiments locally)

### 1. Build Docker images

```bash
docker build -t frontend:latest ./frontend
docker build -t backend:latest ./backend
```

If using Minikube:
```bash
minikube image load frontend:latest
minikube image load backend:latest
```

### 2. Deploy to Kubernetes

```bash
kubectl create namespace resilience-demo
kubectl apply -f k8s/backend-deployment.yaml
kubectl apply -f k8s/frontend-deployment.yaml
kubectl apply -f k8s/toxiproxy-deployment.yaml
```

### 3. Verify everything is running

```bash
kubectl get pods -n resilience-demo
```

### 4. Port-forward the frontend

```bash
kubectl port-forward -n resilience-demo svc/frontend 8080:8080
```

---

## Usage

### Check circuit breaker status

```bash
curl http://localhost:8080/breaker-status
```

```json
{
  "state": "CLOSED",
  "fail_max": 5,
  "reset_timeout": 5,
  "half_open_max_concurrency": 2,
  "last_changed": 1718000000.0
}
```

### Call the backend through the circuit breaker

```bash
# Normal call (routes through breaker + retry)
curl http://localhost:8080/call/users

# Call a chaos-enabled endpoint (random failures/delays on the backend)
curl http://localhost:8080/call/delayed
```

### Trigger the circuit breaker manually

```bash
# Send repeated failing requests to trip the circuit open
for i in {1..10}; do curl -s http://localhost:8080/call/delayed; echo; done

# Watch the circuit state change
curl http://localhost:8080/breaker-status
```

---

## Chaos Engineering

Chaos experiments are defined declaratively in `chaos/`. Each experiment:
1. **Validates a steady-state hypothesis** (system is healthy before chaos)
2. **Injects a fault** via Toxiproxy's HTTP API
3. **Probes the system** during the fault
4. **Rolls back** the fault automatically

### Run the Toxiproxy latency experiment

```bash
pip install chaostoolkit
chaos run experiment-toxiproxy.json
```

This experiment:
- Creates a Toxiproxy proxy forwarding to the backend
- Injects **1500ms latency + 200ms jitter** on all responses
- Checks the circuit breaker status during the fault
- Automatically removes the toxic and proxy on completion (even if the experiment fails)

### What to observe

| Scenario | Expected behaviour |
|----------|--------------------|
| Backend healthy | Circuit CLOSED, requests succeed |
| 5+ failures in a row | Circuit OPEN, subsequent requests fast-fail with `503` |
| After `reset_timeout` seconds | Circuit moves to HALF_OPEN |
| Probe requests succeed | Circuit closes again |
| Probe requests fail | Circuit re-opens |
| HALF_OPEN under load | Semaphore limits concurrency, excess requests get `503` immediately |

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/breaker-status` | GET | Current circuit breaker state and config |
| `/call/users` | GET | Proxied call to backend `/api/users` (with breaker + retry) |
| `/call/delayed` | GET | Proxied call to chaos endpoint (random failures/timeouts/delays) |

---

## Project Structure

```
circuit-breaker/
├── backend/
│   ├── app.py                        # Failure-simulating upstream service
│   ├── models.py
│   ├── Dockerfile
│   └── requirements.txt
├── client/
│   ├── client.py                     # Circuit breaker + retry logic (pybreaker + tenacity)
│   ├── Dockerfile
│   ├── chaostoolkit.log
│   └── requirements.txt
├── k8s/
│   ├── namespace.yaml
│   ├── backend-deployment.yaml
│   ├── backend-service.yaml
│   ├── frontend-deployment.yaml
│   ├── frontend-service.yaml
│   ├── frontend-configmap.yaml       # Environment config for circuit breaker tuning
│   └── toxiproxy-deployment.yaml
├── experiment-toxiproxy.json         # Chaos Toolkit experiment definition
├── docker-compose.yml
└── README.md
```

---

## Key Concepts Demonstrated

- **Circuit Breaker pattern** — prevents cascading failures by fast-failing when a downstream service is degraded
- **Exponential backoff with jitter** — retries with randomised wait times to avoid thundering herd on recovery
- **Half-open concurrency control** — `BoundedSemaphore` limits probe traffic so a recovering service isn't immediately overwhelmed
- **Chaos engineering** — controlled fault injection with automated hypothesis validation and rollback
- **Observability hooks** — `CBListener` logs every state transition and failure count for monitoring integration
- **Environment-driven tuning** — all resilience parameters configurable without code changes

---

## Future Improvements

- [ ] Prometheus metrics endpoint (export circuit state, failure counts, latency)
- [ ] Grafana dashboard for real-time circuit breaker visualisation
- [ ] Additional chaos experiments: packet loss, DNS failure, pod kill
- [ ] Bulkhead pattern alongside circuit breaker (thread pool isolation)
- [ ] gRPC support alongside REST
