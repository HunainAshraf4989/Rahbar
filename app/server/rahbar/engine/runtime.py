"""Provider-neutral cancellation and redacted failures."""

import asyncio

from rahbar.contracts.models import ProviderFailure
from rahbar.contracts.ports import TurnContext


class ProviderError(Exception):
    def __init__(self, code: str = "unavailable", provider: str = "elevenlabs") -> None:
        self.failure = ProviderFailure.model_validate({
            "code": code, "recoverable": code in {"timeout", "throttled", "unavailable"},
            "fallback_ready": False, "provider": provider,
        })
        super().__init__(f"{provider}: {code}; fallback unavailable")


def check_cancelled(context: TurnContext) -> None:
    if context.cancelled.is_set():
        raise asyncio.CancelledError


