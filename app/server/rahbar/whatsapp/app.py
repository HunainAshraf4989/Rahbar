"""Optional composition root. The existing platform application is not modified."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException

from rahbar.config import load_settings, load_supabase_settings
from rahbar.platform_api import create_platform_app

from .audio import AudioCodec
from .config import load_whatsapp_settings
from .meta import MetaClient
from .registration_notice import RegistrationNotices
from .store import QueueFull, WhatsAppStore
from .tutor import WhatsAppTutor
from .webhook import IncomingMessage, create_webhook_router
from .worker import WhatsAppWorker


def create_whatsapp_app(*, project_root: Path | None = None) -> FastAPI:
    root = (project_root or Path.cwd()).resolve()
    settings = load_whatsapp_settings(root)
    providers = load_settings(root / ".env")
    if providers.answer_mode != "gemini":
        raise ValueError("WhatsApp requires RAHBAR_ANSWER_MODE=gemini; dummy answers are disabled")
    dsn = load_supabase_settings(root).database_url
    if not dsn:
        raise ValueError("WhatsApp requires a PostgreSQL database")
    app = create_platform_app(project_root=root)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        import imageio_ffmpeg  # type: ignore[import-untyped]

        codec = AudioCodec(imageio_ffmpeg.get_ffmpeg_exe())
        async with original_lifespan(application):
            store = await asyncio.to_thread(WhatsAppStore, dsn, settings.phone_number_id)
            application.state.whatsapp_store = store
            tasks: list[asyncio.Task[None]] = []
            try:
                async with httpx.AsyncClient(follow_redirects=False) as client:
                    tutor = WhatsAppTutor(
                        application.state.pipeline, application.state.teaching, providers, client
                    )
                    meta = MetaClient(settings, client)
                    worker = WhatsAppWorker(store, meta, tutor, codec)
                    tasks = [asyncio.create_task(worker.run()) for _ in range(2)]
                    notices = RegistrationNotices(store.pool, settings.phone_number_id, meta)
                    tasks.append(asyncio.create_task(notices.run()))
                    try:
                        yield
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                await asyncio.to_thread(store.close)

    app.router.lifespan_context = lifespan

    async def accept(messages: list[IncomingMessage]) -> None:
        try:
            await asyncio.to_thread(app.state.whatsapp_store.accept, messages)
        except QueueFull:
            raise HTTPException(
                503, "Message queue is busy", headers={"Retry-After": "30"}
            ) from None
        except Exception:
            raise HTTPException(503, "Message storage unavailable") from None

    app.include_router(create_webhook_router(settings, accept))
    return app
