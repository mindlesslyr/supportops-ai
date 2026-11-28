# SupportOps AI

A local-first support automation MVP for document ingestion, Qdrant retrieval, grounded answers, feedback, and signed idempotent support webhooks.
(pleasee note this is still a MVP)

## Local setup

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
# Replace every REPLACE_WITH... value.
uvicorn app.main:app --reload
```

`/health` is public. Every other API route requires `X-API-Key`; when `API_KEY` is unset, protected routes are disabled with HTTP 503. The webhook is additionally disabled unless `WEBHOOK_SECRET` is configured. `ALLOW_UNSIGNED_WEBHOOKS_DEV=true` exists only for isolated development/tests and defaults to false.

## API examples

```bash
export SUPPORTOPS_API_KEY='the value configured in API_KEY'
curl --fail-with-body http://127.0.0.1:8000/health
curl --fail-with-body \
  -H "X-API-Key: $SUPPORTOPS_API_KEY" \
  http://127.0.0.1:8000/documents
curl --fail-with-body -X POST http://127.0.0.1:8000/documents \
  -H "X-API-Key: $SUPPORTOPS_API_KEY" \
  -H 'Idempotency-Key: refunds-policy-v1' \
  -H 'Content-Type: application/json' \
  -d '{"title":"Refunds","content":"Refunds are available within 30 days.","source":"kb://refunds"}'
curl --fail-with-body -X POST http://127.0.0.1:8000/query \
  -H "X-API-Key: $SUPPORTOPS_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"question":"How can I get a refund?","top_k":4}'
```

`Idempotency-Key` is optional for backward compatibility but strongly recommended for
`POST /documents`. Use one non-blank key of at most 256 characters per logical document
version. Retrying the same key with the same validated payload returns the original
`201` response and document ID without creating another SQLite row or Qdrant point set.
Reusing the key with a different payload returns `409 idempotency_conflict`; a request
that is still processing returns `409 request_in_progress`. Handled storage failures retain
the original document ID, and the next retry removes any uncertain Qdrant points before
rewriting the document. Without the header, every successful request creates a new
document. Keep the key stable after client timeouts and change it only when intentionally
ingesting a new document version.

### Recovering a crashed document ingestion

A document claim is deliberately not stolen on a timer because Qdrant cannot reject a
late write from an expired worker. If the API process crashes and a key remains
`processing`, first stop every API worker and back up the SQLite database. For the Docker
Compose stack:

```bash
docker compose stop api
docker compose run --rm --no-deps --entrypoint python api -c \
  'import sqlite3; src=sqlite3.connect("/app/data/supportops.db"); dst=sqlite3.connect("/app/data/supportops.db.bak"); src.backup(dst); dst.close(); src.close()'
```

Inspect the exact claim before changing it:

```bash
docker compose run --rm --no-deps --entrypoint python \
  -e RECOVERY_KEY='refunds-policy-v1' api -c \
  'import os,sqlite3; c=sqlite3.connect("/app/data/supportops.db"); print(c.execute("SELECT idempotency_key,document_id,status,created_at FROM document_ingestions WHERE idempotency_key=?",(os.environ["RECOVERY_KEY"],)).fetchone())'
```

Only after confirming that no worker can still be running, transition that one claim to
`retryable`:

```bash
docker compose run --rm --no-deps --entrypoint python \
  -e RECOVERY_KEY='refunds-policy-v1' api -c \
  'import os,sqlite3; c=sqlite3.connect("/app/data/supportops.db"); r=c.execute("UPDATE document_ingestions SET status=\"retryable\",lease_token=NULL WHERE idempotency_key=? AND status=\"processing\"",(os.environ["RECOVERY_KEY"],)); c.commit(); print("updated",r.rowcount,"claim")'
docker compose start api
```

Retry the exact original payload with the same key. The service keeps the original
document ID, removes any uncertain Qdrant points for that document, and writes a clean
point set. Never run this recovery while an API worker may still own the claim.

Webhook callers must send `X-API-Key`, `Idempotency-Key`, and `X-Signature-256: sha256=<HMAC-SHA256 of the exact raw body>`. The same idempotency key and payload replays the completed response; a changed payload returns 409; failed processing can be retried.

## Docker development stack

```bash
cp .env.example .env
# Generate unique values, e.g. openssl rand -hex 32, then edit .env.
docker compose config -q
docker compose up --build
```

After n8n starts, open `http://127.0.0.1:5678` and complete the local owner setup. Then import the versioned workflow:

```bash
docker compose exec n8n n8n import:workflow \
  --input=/workflows/supportops-workflow.json
```

Open **SupportOps AI - Support Webhook** in n8n, review its nodes, and activate it. The public n8n webhook requires both `X-SupportOps-Inbound-Token` and `Idempotency-Key`; retries must reuse the same idempotency key.

### Disposable n8n contract test

Run the opt-in integration test with one command:

```bash
./scripts/run-n8n-contract-test.sh
```

The script starts an isolated Compose project on ports `18000`, `16333`, and `15678`, imports and publishes the versioned workflow, waits for the production webhook to register, and verifies:

- the happy path travels through n8n and reaches the API;
- replaying the same idempotency key returns the same response;
- missing inbound tokens and idempotency keys fail;
- a body changed after signing is rejected by the API.

It uses disposable placeholder secrets and removes its containers and volumes when finished. Set `KEEP_CONTRACT_STACK=1` to retain the isolated stack after a failed run. The normal pytest suite skips this integration module unless `RUN_N8N_CONTRACT=1` is set.

Development ports are bound only to `127.0.0.1`: API 8000, Qdrant 6333, and n8n 5678. Compose refuses to start without API, webhook, inbound n8n, database, and n8n encryption secrets. Healthchecks and dependency conditions make Qdrant/Postgres/API startup ordered. The imported `n8n/supportops-workflow.json` first requires `X-SupportOps-Inbound-Token`, then signs the exact forwarded body and sends the API key. The inbound token is independent from the API key and webhook-signing secret.

`N8N_SECURE_COOKIE=false` is explicit because this development stack uses localhost HTTP. **Production must use TLS and set secure cookies true**, avoid publishing Qdrant/Postgres, place the API behind an authenticated reverse proxy/firewall, rotate high-entropy secrets, restrict n8n access, configure backups and audit logging, and apply rate/body-size limits. Do not expose this development compose file directly to the internet.

## Configuration

- `API_KEY`: required for all routes except `/health`.
- `WEBHOOK_SECRET`: required to enable signed webhooks.
- `N8N_INBOUND_TOKEN`: required by the n8n public webhook before it forwards anything to the privileged API.
- `ALLOW_UNSIGNED_WEBHOOKS_DEV=false`: unmistakable development-only bypass.
- `APP_ENVIRONMENT=production` disables `/docs`, `/redoc`, and `/openapi.json`; the Compose API uses this mode.
- `MAX_REQUEST_BODY_BYTES` and schema field limits bound resource usage.
- `WEBHOOK_LEASE_SECONDS` allows abandoned webhook claims to be reclaimed safely.
- `EMBEDDING_BACKEND=hash|fastembed`; `EMBEDDING_DIMENSIONS` must be positive.
- `ANSWER_PROVIDER=context|openai`; OpenAI mode fails at startup without `OPENAI_API_KEY`.
- `OPENAI_TIMEOUT_SECONDS`, `OPENAI_MAX_RETRIES`, and `OPENAI_MAX_OUTPUT_TOKENS` bound provider latency, retry amplification, and response cost.
- `QDRANT_URL` for server mode, otherwise embedded `QDRANT_PATH` is used.
- `QDRANT_STARTUP_ATTEMPTS`, `QDRANT_RETRY_SECONDS`: startup retry/backoff.
- `CHUNK_SIZE`, `CHUNK_OVERLAP`: positive size and overlap smaller than size.
- `LOW_CONFIDENCE_THRESHOLD`: 0 through 1. Retrieval below it returns an insufficient-context answer without citations.

An existing Qdrant collection must use the configured embedding dimension and cosine distance; startup fails with a clear mismatch error otherwise.

## Verification

```bash
source .venv/bin/activate
python -m pytest -q
python -m compileall -q app tests
ruff check .
mypy app --ignore-missing-imports
bandit -q -r app
python -m json.tool n8n/supportops-workflow.json >/dev/null
```

Tests use temporary embedded Qdrant and SQLite and need no paid service. Treat ingested text as untrusted. Never commit `.env`, databases, vector data, API keys, webhook secrets, customer content, or n8n credentials.

## Limitations

Hash embeddings are deterministic but less semantically accurate than production embeddings. Confidence is retrieval similarity, not calibrated probability. SQLite idempotency supports concurrent claims on one shared SQLite database, but a distributed production deployment should move this coordination to a server database or queue. Document-ingestion claims are not automatically stolen on a timer because Qdrant cannot fence a stale writer; a process crash while a claim is `processing` currently requires operator recovery. Multi-tenancy, background ingestion, document updates/deletes, reranking, and evaluation dashboards remain out of scope.
