"""Meta ingress contract tests: no external providers or database writes."""

import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rahbar.whatsapp.config import WhatsAppSettings, load_whatsapp_settings
from rahbar.whatsapp.webhook import create_webhook_router, parse_messages


@pytest.fixture
def settings():
    return WhatsAppSettings("12345678", "87654321", "access-test", "secret-test", "verify-test")


def payload():
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "87654321",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "12345678"},
                            "messages": [
                                {
                                    "id": "wamid.question",
                                    "from": "923001234567",
                                    "timestamp": "1795500000",
                                    "type": "text",
                                    "text": {"body": "What does a relay do?"},
                                    "context": {"id": "wamid.previous"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def client_for(settings):
    accepted = []

    async def accept(messages):
        accepted.extend(messages)

    app = FastAPI()
    app.include_router(create_webhook_router(settings, accept))
    return TestClient(app), accepted


def post(client, settings, data):
    raw = json.dumps(data).encode()
    signature = "sha256=" + hmac.new(settings.app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/whatsapp",
        content=raw,
        headers={"x-hub-signature-256": signature, "content-type": "application/json"},
    )


def test_verification_echoes_challenge_only_with_correct_token(settings):
    client, _ = client_for(settings)
    query = {"hub.mode": "subscribe", "hub.verify_token": "verify-test", "hub.challenge": "12345"}
    response = client.get("/webhooks/whatsapp", params=query)
    assert response.status_code == 200
    assert response.text == "12345"
    query["hub.verify_token"] = "wrong"
    assert client.get("/webhooks/whatsapp", params=query).status_code == 403


def test_valid_message_preserves_provider_reference(settings):
    client, accepted = client_for(settings)
    assert post(client, settings, payload()).status_code == 200
    assert len(accepted) == 1
    message = accepted[0]
    assert message.message_id == "wamid.question"
    assert message.reference_id == "wamid.previous"
    assert message.sender == "923001234567"


def test_tampered_payload_is_not_accepted(settings):
    client, accepted = client_for(settings)
    assert (
        client.post(
            "/webhooks/whatsapp", json=payload(), headers={"x-hub-signature-256": "sha256=invalid"}
        ).status_code
        == 401
    )
    assert not accepted


def test_oversize_webhook_is_rejected_before_parsing(settings):
    client, accepted = client_for(settings)
    assert client.post("/webhooks/whatsapp", content=b"x" * 256_001).status_code == 413
    assert not accepted


@pytest.mark.parametrize("mismatch", ["business", "phone", "sender", "status"])
def test_unrelated_or_unidentifiable_event_is_ignored(settings, mismatch):
    data = payload()
    entry = data["entry"][0]
    value = entry["changes"][0]["value"]
    if mismatch == "business":
        entry["id"] = "unrelated"
    elif mismatch == "phone":
        value["metadata"]["phone_number_id"] = "unrelated"
    elif mismatch == "sender":
        value["messages"][0].pop("from")
    else:
        value.pop("messages")
        value["statuses"] = [{"status": "delivered"}]
    client, accepted = client_for(settings)
    assert post(client, settings, data).status_code == 200
    assert not accepted


def test_invalid_batch_does_not_partially_accept(settings):
    data = payload()
    messages = data["entry"][0]["changes"][0]["value"]["messages"]
    messages.append({**messages[0], "timestamp": "invalid"})
    client, accepted = client_for(settings)
    assert post(client, settings, data).status_code == 400
    assert not accepted


def test_audio_parser_carries_media_id_not_remote_url(settings):
    data = payload()
    message = data["entry"][0]["changes"][0]["value"]["messages"][0]
    message["type"] = "audio"
    message["audio"] = {"id": "987654", "mime_type": "audio/ogg; codecs=opus"}
    parsed = parse_messages(data, settings)
    assert parsed[0].kind == "audio"
    assert parsed[0].content == "987654"


def test_missing_secret_fails_closed_and_secrets_are_not_in_repr(settings, tmp_path):
    assert "secret-test" not in repr(settings)
    assert "access-test" not in repr(settings)
    with pytest.raises(ValueError, match="numeric"):
        WhatsAppSettings("bad", "87654321", "token", "secret", "verify")
    (tmp_path / ".env.whatsapp.local").write_text(
        "RAHBAR_WHATSAPP_PHONE_NUMBER_ID=12345678\n"
        "RAHBAR_WHATSAPP_BUSINESS_ACCOUNT_ID=87654321\n"
        "RAHBAR_WHATSAPP_ACCESS_TOKEN=test\nRAHBAR_WHATSAPP_VERIFY_TOKEN=test\n"
    )
    with pytest.raises(ValueError, match="required"):
        load_whatsapp_settings(tmp_path)
