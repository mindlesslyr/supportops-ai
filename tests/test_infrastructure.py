import json
from pathlib import Path

import yaml

from app.config import AnswerProviderName

ROOT = Path(__file__).resolve().parents[1]


def test_env_example_uses_supported_answer_provider():
    values = dict(
        line.split("=", 1)
        for line in (ROOT / ".env.example").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert values["ANSWER_PROVIDER"] in {provider.value for provider in AnswerProviderName}


def test_n8n_workflow_authenticates_before_forwarding():
    workflow = json.loads((ROOT / "n8n" / "supportops-workflow.json").read_text())
    nodes = {node["name"]: node for node in workflow["nodes"]}

    webhook = nodes["Support Ticket Webhook"]
    assert webhook["webhookId"] == "d76b2ed2-1db5-4cff-bc86-70b3d96d9734"
    assert webhook["parameters"]["path"] == "support-ticket"

    auth_code = nodes["Authenticate Caller"]["parameters"]["jsCode"]
    assert "x-supportops-inbound-token" in auth_code
    assert "SUPPORTOPS_INBOUND_TOKEN" in auth_code
    assert "REPLACE_WITH" not in json.dumps(workflow)

    first_connection = workflow["connections"]["Support Ticket Webhook"]["main"][0][0]
    assert first_connection["node"] == "Authenticate Caller"

    sign_code = nodes["Sign Request"]["parameters"]["jsCode"]
    assert "idempotency-key" in sign_code
    assert "$execution.id" not in sign_code
    assert "Missing Idempotency-Key" in sign_code


def test_compose_is_local_only_and_uses_readiness():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = compose["services"]

    assert services["n8n"]["image"] == "n8nio/n8n:2.47.4"
    assert services["postgres"]["image"] == "postgres:17-alpine"
    assert services["api"]["ports"] == ["127.0.0.1:${API_PORT:-8000}:8000"]
    assert "env_file" not in services["api"]
    assert not any(name.startswith("N8N_") for name in services["api"]["environment"])
    assert services["qdrant"]["ports"] == ["127.0.0.1:${QDRANT_PORT:-6333}:6333"]
    assert services["n8n"]["ports"] == ["127.0.0.1:${N8N_PORT:-5678}:5678"]
    assert "./n8n:/workflows:ro" in services["n8n"]["volumes"]
    assert "/ready" in " ".join(services["api"]["healthcheck"]["test"])
    assert "SUPPORTOPS_INBOUND_TOKEN" in services["n8n"]["environment"]
    assert services["n8n"]["environment"]["NODE_FUNCTION_ALLOW_BUILTIN"] == "crypto"
    assert services["n8n"]["environment"]["N8N_BLOCK_ENV_ACCESS_IN_NODE"] == "false"
    assert services["n8n"]["environment"]["N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS"] == "true"


def test_api_container_runs_as_non_root():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "USER supportops" in dockerfile
