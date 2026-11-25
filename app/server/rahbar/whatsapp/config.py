"""Channel-only configuration. Secret values never appear in repr or logs."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values


@dataclass(frozen=True)
class WhatsAppSettings:
    phone_number_id: str
    business_account_id: str
    access_token: str = field(repr=False)
    app_secret: str = field(repr=False)
    verify_token: str = field(repr=False)
    api_version: str = "v27.0"
    max_webhook_bytes: int = 256_000

    def __post_init__(self) -> None:
        if not all(
            re.fullmatch(r"[0-9]{5,32}", value)
            for value in (self.phone_number_id, self.business_account_id)
        ):
            raise ValueError("WhatsApp phone and business IDs must be numeric")
        if not all((self.access_token.strip(), self.app_secret.strip(), self.verify_token.strip())):
            raise ValueError("WhatsApp access token, app secret and verify token are required")
        if not re.fullmatch(r"v[0-9]{2}\.[0-9]+", self.api_version):
            raise ValueError("Invalid WhatsApp Graph API version")


def load_whatsapp_settings(root: Path) -> WhatsAppSettings:
    values = {**dotenv_values(root / ".env.whatsapp.local"), **os.environ}

    def value(name: str) -> str:
        return str(values.get("RAHBAR_WHATSAPP_" + name) or "").strip()

    return WhatsAppSettings(
        phone_number_id=value("PHONE_NUMBER_ID"),
        business_account_id=value("BUSINESS_ACCOUNT_ID"),
        access_token=value("ACCESS_TOKEN"),
        app_secret=value("APP_SECRET"),
        verify_token=value("VERIFY_TOKEN"),
        api_version=value("API_VERSION") or "v27.0",
    )
