"""Restricted Meta media and messaging client; no provider response bodies escape."""

import hashlib
import logging
import re
from urllib.parse import urlsplit

import httpx

from .config import WhatsAppSettings

logger = logging.getLogger("rahbar.whatsapp")


def log_rejection(response: httpx.Response, operation: str) -> None:
    """Keep useful provider codes without logging credentials or message bodies."""
    code = subcode = None
    try:
        error = response.json().get("error", {})
        if isinstance(error, dict):
            code = error.get("code") if type(error.get("code")) is int else None
            subcode = (
                error.get("error_subcode") if type(error.get("error_subcode")) is int else None
            )
    except (ValueError, AttributeError):
        pass
    logger.warning(
        "whatsapp_meta_rejected operation=%s http=%s code=%s subcode=%s",
        operation,
        response.status_code,
        code,
        subcode,
    )


class ChannelError(Exception):
    pass


class AmbiguousSend(ChannelError):
    """The recipient may have received the message; automatic resend is unsafe."""


class MetaClient:
    def __init__(self, settings: WhatsAppSettings, client: httpx.AsyncClient) -> None:
        self.settings, self.client = settings, client
        self.base = f"https://graph.facebook.com/{settings.api_version}"
        self.headers = {"Authorization": "Bearer " + settings.access_token}

    async def download(self, media_id: str) -> tuple[bytes, str]:
        if not re.fullmatch(r"[0-9]{1,64}", media_id):
            raise ChannelError("Invalid media ID")
        try:
            response = await self.client.get(
                f"{self.base}/{media_id}",
                headers=self.headers,
                params={"phone_number_id": self.settings.phone_number_id},
                timeout=20,
            )
            if response.is_error:
                log_rejection(response, "media_metadata")
                raise ChannelError("Media metadata unavailable")
            data = response.json()
            url = data["url"]
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "lookaside.fbsbx.com"
                or parsed.port not in {None, 443}
                or parsed.username
                or parsed.password
            ):
                raise ChannelError("Untrusted media host")
            mime = data["mime_type"].split(";")[0].strip()
            if mime not in {"audio/ogg", "audio/mpeg", "audio/mp4", "audio/aac", "audio/amr"}:
                raise ChannelError("Unsupported voice format")
            if int(data["file_size"]) > 16 * 1024 * 1024:
                raise ChannelError("Voice file too large")
            body = bytearray()
            async with self.client.stream("GET", url, headers=self.headers, timeout=30) as download:
                if download.status_code != 200:
                    raise ChannelError("Voice download failed")
                async for chunk in download.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 16 * 1024 * 1024:
                        raise ChannelError("Voice file too large")
            digest = data.get("sha256")
            if isinstance(digest, str) and re.fullmatch(r"[a-fA-F0-9]{64}", digest):
                if hashlib.sha256(body).hexdigest() != digest.lower():
                    raise ChannelError("Media checksum mismatch")
            return bytes(body), mime
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ChannelError("Voice download unavailable") from None

    async def upload(self, audio: bytes) -> str:
        try:
            response = await self.client.post(
                f"{self.base}/{self.settings.phone_number_id}/media",
                headers=self.headers,
                data={"messaging_product": "whatsapp", "type": "audio/ogg"},
                files={"file": ("answer.ogg", audio, "audio/ogg")},
                timeout=30,
            )
            if response.is_error:
                log_rejection(response, "media_upload")
                raise ChannelError("Voice upload unavailable")
            value = response.json()["id"]
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,64}", value):
                raise ValueError
            return value
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ChannelError("Voice upload unavailable") from None

    async def send(
        self,
        recipient: str,
        question_id: str,
        *,
        text: str | None = None,
        media_id: str | None = None,
    ) -> str:
        if not re.fullmatch(r"[1-9][0-9]{6,14}", recipient):
            raise ChannelError("Invalid recipient")
        if (text is None) == (media_id is None):
            raise ValueError("Specify one reply type")
        if text is not None and not 1 <= len(text) <= 4000:
            raise ChannelError("Reply exceeds text limit")
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "context": {"message_id": question_id},
            "type": "text" if text is not None else "audio",
        }
        if text is not None:
            payload["text"] = {"body": text, "preview_url": False}
        else:
            payload["audio"] = {"id": media_id, "voice": True}
        try:
            response = await self.client.post(
                f"{self.base}/{self.settings.phone_number_id}/messages",
                headers=self.headers,
                json=payload,
                timeout=30,
            )
        except httpx.HTTPError:
            raise AmbiguousSend("Message delivery outcome unknown") from None
        if response.status_code >= 500:
            raise AmbiguousSend("Message delivery outcome unknown")
        if response.is_error:
            log_rejection(response, "send")
            raise ChannelError("Message rejected by Meta")
        try:
            value = response.json()["messages"][0]["id"]
            if not isinstance(value, str) or not 1 <= len(value) <= 512:
                raise ValueError
            return value
        except (ValueError, KeyError, IndexError, TypeError):
            raise AmbiguousSend("Message delivery outcome unknown") from None
