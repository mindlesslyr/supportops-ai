import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_n8n_workflow_authenticates_before_forwarding():
    workflow = json.loads((ROOT / "n8n" / "supportops-workflow.json").read_text())
    nodes = {node["name"]: node for node in workflow["nodes"]}

    auth_code = nodes["Authenticate Caller"]["parameters"]["jsCode"]
    assert "x-supportops-inbound-token" in auth_code
    assert "SUPPORTOPS_INBOUND_TOKEN" in auth_code
    assert "REPLACE_WITH" not in json.dumps(workflow)

    first_connection = workflow["connections"]["Support Ticket Webhook"]["main"][0][0]
    assert first_connection["node"] == "Authenticate Caller"


def test_compose_is_local_only_and_uses_readiness():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = compose["services"]

    assert services["api"]["ports"] == ["127.0.0.1:8000:8000"]
    assert services["qdrant"]["ports"] == ["127.0.0.1:6333:6333"]
    assert services["n8n"]["ports"] == ["127.0.0.1:5678:5678"]
    assert "/ready" in " ".join(services["api"]["healthcheck"]["test"])
    assert "SUPPORTOPS_INBOUND_TOKEN" in services["n8n"]["environment"]


def test_api_container_runs_as_non_root():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "USER supportops" in dockerfile
