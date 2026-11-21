"""Bounded Gemini routing. Caller supplies a verified free-tier model allowlist."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx


class QuotaExhausted(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelResponse:
    data: dict[str, Any]
    model: str
    attempted: tuple[str, ...]


class GeminiRouter:
    def __init__(self, client: httpx.AsyncClient, key: str, models: list[str]) -> None:
        if not models or len(set(models)) != len(models):
            raise ValueError('A nonempty unique free-tier model allowlist is required')
        import re
        if any(not re.fullmatch(r'[a-zA-Z0-9._-]+', model) for model in models):
            raise ValueError('Invalid model identifier')
        self.client, self.key, self.models = client, key, tuple(models)
        self.cooldown: dict[str, float] = {}
        self.lock = asyncio.Lock()

    async def generate(self, instruction: str, payload: dict[str, Any],
                       schema: dict[str, Any] | None, *, max_output_tokens: int = 2048) -> ModelResponse:
        # Serialize quota accounting across concurrent turns/evaluation requests.
        async with self.lock:
            attempted: list[str] = []
            for model in self.models:
                if self.cooldown.get(model, 0) > time.monotonic():
                    continue
                attempted.append(model)
                try:
                    response = await self.client.post(
                        f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                        headers={'x-goog-api-key': self.key},
                        json={'systemInstruction': {'parts': [{'text': instruction}]},
                              'contents': [{'role': 'user', 'parts': [
                                  {'text': json.dumps(payload, ensure_ascii=False)}]}],
                              'generationConfig': {'temperature': 0, 'maxOutputTokens': max_output_tokens,
                                  'responseMimeType': 'application/json',
                                  **({'responseJsonSchema': schema} if schema is not None else {})}}, timeout=45)
                except httpx.RequestError:
                    raise RuntimeError('Gemini transport failure') from None
                if response.status_code == 404:
                    self.cooldown[model] = float('inf')
                    continue
                if response.status_code in {429, 503}:
                    delay = 60.
                    try:
                        delay = max(delay, float(response.headers.get('retry-after', '60')))
                    except ValueError:
                        pass
                    self.cooldown[model] = time.monotonic() + delay
                    continue
                if response.is_error:
                    # Never include response bodies, URLs with secrets, or headers in errors.
                    raise RuntimeError(f'Gemini request failed ({response.status_code})')
                try:
                    candidate = response.json()['candidates'][0]
                    if candidate['finishReason'] != 'STOP':
                        raise ValueError
                    raw = ''.join(p.get('text', '') for p in candidate['content']['parts']
                                  if not p.get('thought', False))
                    result = json.loads(raw)
                    if not isinstance(result, dict):
                        raise ValueError
                except (KeyError, IndexError, TypeError, ValueError):
                    raise RuntimeError('Invalid Gemini structured response') from None
                return ModelResponse(result, model, tuple(attempted))
            raise QuotaExhausted('No configured model currently has available capacity')
