import hashlib
import hmac
import json
import os
import uuid

import httpx
import pytest

if os.getenv("RUN_N8N_CONTRACT") != "1":
    pytest.skip(
        "set RUN_N8N_CONTRACT=1 to run the disposable n8n contract stack",
        allow_module_level=True,
    )

N8N_URL = os.getenv("N8N_URL", "http://127.0.0.1:5678").rstrip("/")
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000").rstrip("/")


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.fail(f"{name} is required for the n8n contract test")
    assert value is not None
    return value


def synthetic_ticket() -> dict[str, str]:
    suffix = uuid.uuid4().hex
    return {
        "ticket_id": f"CONTRACT-{suffix}",
        "message": "I need help resetting my password",
    }


def test_n8n_forwards_a_signed_idempotent_request():
    payload = synthetic_ticket()
    headers = {
        "X-SupportOps-Inbound-Token": required_env("N8N_INBOUND_TOKEN"),
        "Idempotency-Key": f"contract-{uuid.uuid4().hex}",
    }

    first = httpx.post(
        f"{N8N_URL}/webhook/support-ticket",
        json=payload,
        headers=headers,
        timeout=30,
    )
    assert first.status_code == 200, first.text
    response = first.json()
    assert response["ticket_id"] == payload["ticket_id"]
    assert response["status"] in {"answered", "human_review"}
    assert response["human_review"] is (response["status"] == "human_review")

    replay = httpx.post(
        f"{N8N_URL}/webhook/support-ticket",
        json=payload,
        headers=headers,
        timeout=30,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == response


def test_n8n_rejects_a_missing_inbound_token():
    response = httpx.post(
        f"{N8N_URL}/webhook/support-ticket",
        json=synthetic_ticket(),
        headers={"Idempotency-Key": f"contract-{uuid.uuid4().hex}"},
        timeout=30,
    )

    assert response.status_code == 500


def test_n8n_rejects_a_missing_idempotency_key():
    response = httpx.post(
        f"{N8N_URL}/webhook/support-ticket",
        json=synthetic_ticket(),
        headers={"X-SupportOps-Inbound-Token": required_env("N8N_INBOUND_TOKEN")},
        timeout=30,
    )

    assert response.status_code == 500


def test_api_rejects_a_body_changed_after_signing():
    original = synthetic_ticket()
    raw = json.dumps(original, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(
        required_env("CONTRACT_WEBHOOK_SECRET").encode(), raw, hashlib.sha256
    ).hexdigest()
    tampered = json.dumps(
        {**original, "message": "tampered after signing"}, separators=(",", ":")
    ).encode()

    response = httpx.post(
        f"{API_URL}/webhooks/support",
        content=tampered,
        headers={
            "X-API-Key": required_env("CONTRACT_API_KEY"),
            "X-Signature-256": signature,
            "Idempotency-Key": f"contract-{uuid.uuid4().hex}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_signature"
