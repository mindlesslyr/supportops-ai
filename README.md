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
that is still processing returns `409 request_in_progress`. Without the header, every
successful request creates a new document. Keep the key stable after client timeouts and
change it only when intentionally ingesting a new document version.

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

Development ports are bound only to `127.0.0.1`: API 8000, Qdrant 6333, and n8n 5678. Compose refuses to start without API, webhook, inbound n8n, database, and n8n encryption secrets. Healthchecks and dependency conditions make Qdrant/Postgres/API startup ordered. The imported `n8n/supportops-workflow.json` first requires `X-SupportOps-Inbound-Token`, then signs the exact forwarded body and sends the API key. The inbound token is independent from the API key and webhook-signing secret.

`N8N_SECURE_COOKIE=false` is explicit because this development stack uses localhost HTTP. **Production must use TLS and set secure cookies true**, avoid publishing Qdrant/Postgres, place the API behind an authenticated reverse proxy/firewall, rotate high-entropy secrets, restrict n8n access, configure backups and audit logging, and apply rate/body-size limits. Do not expose this development compose file directly to the internet.

## Configuration

- `API_KEY`: required for all routes except `/health`.
- `WEBHOOK_SECRET`: required to enable signed webhooks.
- `N8N_INBOUND_TOKEN`: required by the n8n public webhook before it forwards anything to the privileged API.
- `ALLOW_UNSIGNED_WEBHOOKS_DEV=false`: unmistakable development-only bypass.
- `APP_ENVIRONMENT=production` disables `/docs`, `/redoc`, and `/openapi.json`; the Compose API uses this mode.
- `MAX_REQUEST_BODY_BYTES` and schema field limits bound resource usage.
- `INGESTION_LEASE_SECONDS` controls when an abandoned document-ingestion claim can be safely reclaimed.
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

Hash embeddings are deterministic but less semantically accurate than production embeddings. Confidence is retrieval similarity, not calibrated probability. SQLite idempotency supports concurrent claims on one shared SQLite database, but a distributed production deployment should move this coordination to a server database or queue. Multi-tenancy, background ingestion, document updates/deletes, reranking, and evaluation dashboards remain out of scope.
