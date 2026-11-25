from fastapi import FastAPI
from fastapi.testclient import TestClient
from rahbar.whatsapp.public import WebhookOnly


def test_public_surface_exposes_only_webhook():
    app = FastAPI()

    @app.api_route("/webhooks/whatsapp", methods=["GET", "POST"])
    def webhook():
        return {"ok": True}

    @app.get("/dev/private")
    def private():
        raise AssertionError("Development route must not be called")

    with TestClient(WebhookOnly(app)) as client:
        assert client.get("/webhooks/whatsapp").status_code == 200
        assert client.post("/webhooks/whatsapp").status_code == 200
        for path in ("/dev/private", "/docs", "/openapi.json", "/api/v1/me"):
            assert client.get(path).status_code == 404
        assert client.put("/webhooks/whatsapp").status_code == 404
