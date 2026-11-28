#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PROJECT_NAME=${CONTRACT_PROJECT_NAME:-supportops-contract}
API_PORT=${CONTRACT_API_PORT:-18000}
QDRANT_PORT=${CONTRACT_QDRANT_PORT:-16333}
N8N_PORT=${CONTRACT_N8N_PORT:-15678}

export API_KEY=contract-api-key-placeholder
export WEBHOOK_SECRET=contract-webhook-secret-placeholder
export N8N_INBOUND_TOKEN=contract-inbound-token-placeholder
export N8N_DB_PASSWORD=contract-db-password-placeholder
export N8N_ENCRYPTION_KEY=contract-encryption-key-placeholder-32-bytes
export API_PORT QDRANT_PORT N8N_PORT

compose() {
  docker compose --project-name "$PROJECT_NAME" "$@"
}

cleanup() {
  status=$?
  trap - EXIT INT TERM
  if [[ ${KEEP_CONTRACT_STACK:-0} == "1" ]]; then
    printf 'Contract stack kept as project %s for inspection.\n' "$PROJECT_NAME"
  else
    compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

wait_for_url() {
  local url=$1
  local expected=$2
  local label=$3
  local code

  for _ in $(seq 1 90); do
    code=$(curl --silent --output /dev/null --write-out '%{http_code}' "$url" || true)
    if [[ $code == "$expected" ]]; then
      printf '%s is ready.\n' "$label"
      return 0
    fi
    sleep 2
  done

  printf '%s did not become ready at %s.\n' "$label" "$url" >&2
  compose ps >&2 || true
  compose logs --no-color --tail=100 >&2 || true
  return 1
}

wait_for_webhook() {
  local code

  for _ in $(seq 1 90); do
    code=$(curl --silent --output /dev/null --write-out '%{http_code}' \
      --request POST "http://127.0.0.1:${N8N_PORT}/webhook/support-ticket" \
      --header 'Content-Type: application/json' \
      --header 'Idempotency-Key: contract-readiness-probe' \
      --data '{"ticket_id":"CONTRACT-READY","message":"readiness probe"}' || true)
    if [[ $code == "500" ]]; then
      printf 'n8n production webhook is registered.\n'
      return 0
    fi
    sleep 2
  done

  printf 'n8n production webhook was not registered.\n' >&2
  compose exec -T postgres psql -U n8n -d n8n -c 'select * from webhook_entity;' >&2 || true
  compose logs --no-color --tail=100 n8n >&2 || true
  return 1
}

cd "$ROOT_DIR"
compose down --volumes --remove-orphans >/dev/null 2>&1 || true
compose up --detach --build

wait_for_url "http://127.0.0.1:${API_PORT}/ready" 200 "SupportOps API"
wait_for_url "http://127.0.0.1:${N8N_PORT}/healthz" 200 "n8n"

compose exec -T n8n n8n import:workflow --input=/workflows/supportops-workflow.json
workflow_id=$(compose exec -T postgres psql -U n8n -d n8n -Atc \
  "select id from workflow_entity where name='SupportOps AI - Support Webhook' order by \"createdAt\" desc limit 1")

if [[ -z $workflow_id ]]; then
  printf 'Imported workflow ID was not found.\n' >&2
  exit 1
fi

compose exec -T n8n n8n publish:workflow --id="$workflow_id"
compose restart n8n
wait_for_url "http://127.0.0.1:${N8N_PORT}/healthz" 200 "published n8n workflow"
wait_for_webhook

RUN_N8N_CONTRACT=1 \
N8N_URL="http://127.0.0.1:${N8N_PORT}" \
API_URL="http://127.0.0.1:${API_PORT}" \
CONTRACT_API_KEY="$API_KEY" \
CONTRACT_WEBHOOK_SECRET="$WEBHOOK_SECRET" \
N8N_INBOUND_TOKEN="$N8N_INBOUND_TOKEN" \
.venv/bin/python -m pytest tests/test_n8n_contract.py -v
