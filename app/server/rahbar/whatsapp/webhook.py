"""Signed Meta Cloud API ingress; parsing never calls speech or model providers."""

import hashlib
import hmac
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .config import WhatsAppSettings


@dataclass(frozen=True)
class IncomingMessage:
    message_id: str
    sender: str
    timestamp: int
    kind: Literal["text", "audio"]
    content: str
    reference_id: str | None = None


def parse_messages(payload: object, settings: WhatsAppSettings) -> list[IncomingMessage]:
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return []
    messages = []
    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        raise ValueError("Invalid entries")
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("id") != settings.business_account_id:
            continue
        changes = entry.get("changes", [])
        if not isinstance(changes, list):
            raise ValueError("Invalid changes")
        for change in changes:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value")
            if not isinstance(value, dict) or value.get("messaging_product") != "whatsapp":
                continue
            metadata = value.get("metadata")
            if (
                not isinstance(metadata, dict)
                or metadata.get("phone_number_id") != settings.phone_number_id
            ):
                continue
            incoming = value.get("messages", [])
            if not isinstance(incoming, list):
                raise ValueError("Invalid messages")
            for item in incoming:
                if not isinstance(item, dict) or item.get("type") not in {"text", "audio"}:
                    continue
                sender, message_id = item.get("from"), item.get("id")
                # A sender whose number is withheld cannot pass the phone allowlist.
                if not isinstance(sender, str) or not re.fullmatch(r"[1-9][0-9]{6,14}", sender):
                    continue
                if not isinstance(message_id, str) or not 1 <= len(message_id) <= 512:
                    raise ValueError("Invalid message identity")
                kind = item["type"]
                detail = item.get(kind)
                content = (
                    detail.get("body" if kind == "text" else "id")
                    if isinstance(detail, dict)
                    else None
                )
                limit = 4000 if kind == "text" else 512
                if not isinstance(content, str) or not 1 <= len(content.strip()) <= limit:
                    raise ValueError("Invalid message content")
                raw_timestamp = item.get("timestamp")
                if not isinstance(raw_timestamp, str) or not re.fullmatch(
                    r"[0-9]{1,12}", raw_timestamp
                ):
                    raise ValueError("Invalid timestamp")
                reference = item.get("context", {})
                if not isinstance(reference, dict):
                    raise ValueError("Invalid reference")
                reference_id = reference.get("id")
                if reference_id is not None and (
                    not isinstance(reference_id, str) or not 1 <= len(reference_id) <= 512
                ):
                    raise ValueError("Invalid reference identity")
                messages.append(
                    IncomingMessage(
                        message_id, sender, int(raw_timestamp), kind, content, reference_id
                    )
                )
                if len(messages) > 32:
                    raise ValueError("Message batch exceeds limit")
    return messages


def create_webhook_router(
    settings: WhatsAppSettings, accept: Callable[[list[IncomingMessage]], Awaitable[None]]
) -> APIRouter:
    router = APIRouter()

    @router.get("/webhooks/whatsapp", response_class=PlainTextResponse)
    async def verify(request: Request) -> PlainTextResponse:
        query = request.query_params
        if query.get("hub.mode") != "subscribe" or not hmac.compare_digest(
            query.get("hub.verify_token", "").encode(), settings.verify_token.encode()
        ):
            raise HTTPException(403, "Webhook verification failed")
        challenge = query.get("hub.challenge", "")
        if not challenge or len(challenge) > 1024:
            raise HTTPException(400, "Invalid challenge")
        return PlainTextResponse(challenge)

    @router.post("/webhooks/whatsapp")
    async def receive(request: Request) -> dict[str, str]:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > settings.max_webhook_bytes:
                raise HTTPException(413, "Webhook too large")
        signature = request.headers.get("x-hub-signature-256", "")
        expected = (
            "sha256=" + hmac.new(settings.app_secret.encode(), body, hashlib.sha256).hexdigest()
        )
        if not re.fullmatch(r"sha256=[a-f0-9]{64}", signature) or not hmac.compare_digest(
            signature, expected
        ):
            raise HTTPException(401, "Invalid webhook signature")
        try:
            messages = parse_messages(json.loads(body), settings)
        except (ValueError, TypeError, RecursionError):
            raise HTTPException(400, "Invalid webhook payload") from None
        if messages:
            # The implementation must durably accept the batch before returning success.
            await accept(messages)
        return {"status": "accepted"}

    return router
