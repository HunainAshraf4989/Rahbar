"""Export schemas only; this does not register or implement HTTP endpoints."""

import argparse
import json
import re
from pathlib import Path

from pydantic import BaseModel
from rahbar.contracts import events, http, models

ROOT = Path(__file__).resolve().parents[1]


def export(destination: Path) -> None:
    classes = {
        obj.__name__: obj
        for module in (models, events, http)
        for obj in vars(module).values()
        if isinstance(obj, type)
        and issubclass(obj, BaseModel)
        and obj not in (BaseModel, models.Model)
    }
    classes.update({obj.__name__: obj for obj in events.EVENT_MODELS.values()})
    definitions = {}
    for name, cls in sorted(classes.items()):
        schema = cls.model_json_schema(ref_template="#/$defs/{model}")
        definitions.update(schema.pop("$defs", {}))
        definitions[name] = schema
    schemas = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Contract",
        "$defs": dict(sorted(definitions.items())),
        "anyOf": [{"$ref": f"#/$defs/{name}"} for name in sorted(classes)],
    }
    components = json.loads(json.dumps(definitions).replace("#/$defs/", "#/components/schemas/"))
    paths = {}

    def content(cls):
        return {"application/json": {"schema": {"$ref": f"#/components/schemas/{cls.__name__}"}}}

    for method, path, request, response, status, idempotent in http.OPERATIONS:
        operation = {
            "operationId": method + re.sub(r"[^a-zA-Z0-9]", "_", path),
            "responses": {str(status): {"description": "Success"}},
        }
        if response:
            operation["responses"][str(status)]["content"] = content(response)
        for code in (400, 401, 403, 404, 409, 422, 429, 503):
            operation["responses"][str(code)] = {
                "description": "Safe error",
                "content": content(http.HttpError),
            }
        if request:
            operation["requestBody"] = {"required": True, "content": content(request)}
        parameters = [
            {
                "in": "path",
                "name": name,
                "required": True,
                "schema": {"type": "string", "minLength": 1},
            }
            for name in re.findall(r"{(.*?)}", path)
        ]
        if idempotent:
            parameters.append(
                {
                    "in": "header",
                    "name": "Idempotency-Key",
                    "required": True,
                    "schema": {"type": "string", "minLength": 1},
                }
            )
        if parameters:
            operation["parameters"] = parameters
        if path.startswith("/api/v1") and path not in (
            "/api/v1/auth/login",
            "/api/v1/auth/register",
        ):
            operation["security"] = [{"sessionCookie": []}]
            if "/engine/" in path or "/viva-attempts" in path:
                operation["security"].append({"hostBearer": []})
        if path == "/webhooks/whatsapp":
            operation["security"] = [{"webhookSignature": []}]
        paths.setdefault(path, {})[method] = operation
    paths["/webhooks/whatsapp"]["get"] = {
        "operationId": "verifyWhatsAppSubscription",
        "parameters": [
            {"in": "query", "name": name, "required": True, "schema": {"type": "string"}}
            for name in ("hub.mode", "hub.verify_token", "hub.challenge")
        ],
        "responses": {
            "200": {
                "description": "Verified challenge",
                "content": {"text/plain": {"schema": {"type": "string"}}},
            },
            "403": {"description": "Verification failed"},
        },
    }
    openapi = {
        "openapi": "3.1.0",
        "info": {
            "title": "Rahbar contract specification",
            "version": "1.0.0",
            "description": "Design contract only; endpoints are not yet implemented.",
        },
        "paths": paths,
        "components": {
            "schemas": components,
            "securitySchemes": {
                "sessionCookie": {"type": "apiKey", "in": "cookie", "name": "rahbar_session"},
                "hostBearer": {"type": "http", "scheme": "bearer"},
                "webhookSignature": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-Hub-Signature-256",
                },
            },
        },
    }
    destination.mkdir(exist_ok=True)
    (destination / "openapi.json").write_text(json.dumps(openapi, indent=2) + "\n")
    (destination / "contracts.schema.json").write_text(json.dumps(schemas, indent=2) + "\n")
    catalog = {
        direction: {
            name: cls.__name__ for name, cls in events.EVENT_MODELS.items() if name in payloads
        }
        for direction, payloads in (
            ("client", events.CLIENT_PAYLOADS),
            ("server", events.SERVER_PAYLOADS),
        )
    }
    (destination / "events.json").write_text(json.dumps(catalog, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "contracts")
    export(parser.parse_args().output)
