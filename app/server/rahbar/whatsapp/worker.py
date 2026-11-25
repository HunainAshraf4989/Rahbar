"""Background job runner. Conversation history is updated after delivery, off the webhook."""

import asyncio
import logging
import time
from typing import Any

from .audio import AudioCodec
from .meta import AmbiguousSend, ChannelError, MetaClient
from .store import Lease, WhatsAppStore
from .tutor import WhatsAppTutor

logger = logging.getLogger("rahbar.whatsapp")
EXPIRED_REFERENCE = (
    "I can only reference your last five question–answer exchanges. "
    "That message is no longer available; please resend your question with the details.\n"
    "صرف آخری پانچ سوالات اور جوابات دستیاب ہیں۔ براہ کرم سوال کی تفصیل دوبارہ بھیجیں۔"
)
UNAVAILABLE = (
    "The tutor could not prepare an answer right now. Please try again shortly.\n"
    "اس وقت جواب تیار نہیں ہو سکا۔ براہ کرم تھوڑی دیر بعد دوبارہ کوشش کریں۔"
)


def resolve_reference(
    history: list[dict[str, Any]], message_id: str | None
) -> dict[str, Any] | None:
    if message_id is None:
        return None
    return next(
        (
            pair
            for pair in history
            if message_id
            in {
                pair["question_message_id"],
                pair["text_message_id"],
                pair["audio_message_id"],
            }
        ),
        None,
    )


class WhatsAppWorker:
    def __init__(
        self, store: WhatsAppStore, meta: MetaClient, tutor: WhatsAppTutor, codec: AudioCodec
    ) -> None:
        self.store, self.meta, self.tutor, self.codec = store, meta, tutor, codec

    async def send_part(self, lease: Lease, kind: str) -> None:
        if lease.job[kind + "_state"] == "sent":
            return
        if not await asyncio.to_thread(self.store.authorized, lease):
            raise PermissionError("Account is no longer authorized")
        await asyncio.to_thread(self.store.update, lease, **{kind + "_state": "sending"})
        try:
            args = (
                {"text": lease.job["answer_text"]}
                if kind == "text"
                else {"media_id": lease.job["reply_media_id"]}
            )
            message_id = await self.meta.send(
                lease.job["sender"], lease.job["provider_message_id"], **args
            )
        except AmbiguousSend:
            raise
        except ChannelError:
            await asyncio.to_thread(self.store.update, lease, **{kind + "_state": "pending"})
            raise
        await asyncio.to_thread(
            self.store.update,
            lease,
            **{
                kind + "_state": "sent",
                kind + "_message_id": message_id,
            },
        )

    async def process(self, lease: Lease) -> None:
        job = lease.job
        if not await asyncio.to_thread(self.store.authorized, lease):
            await asyncio.to_thread(self.store.finish, lease, "failed")
            return
        if job["text_state"] == "sending" or job["audio_state"] == "sending":
            # The previous process may have sent this. Never silently duplicate an uncertain send.
            await asyncio.to_thread(self.store.finish, lease, "unknown")
            return
        if int(job["source_timestamp"]) < time.time() - 86_000:
            await asyncio.to_thread(self.store.finish, lease, "failed")
            return
        if not job.get("answer_text"):
            history = await asyncio.to_thread(self.store.history, lease)
            reference = resolve_reference(history, job.get("quoted_message_id"))
            if job.get("quoted_message_id") and reference is None:
                await asyncio.to_thread(
                    self.store.update,
                    lease,
                    answer_text=EXPIRED_REFERENCE,
                    question_text=job["input_content"] if job["message_kind"] == "text" else None,
                )
            else:
                question = job.get("question_text")
                if not question:
                    if job["message_kind"] == "audio":
                        media, mime = await self.meta.download(job["input_content"])
                        wav = await self.codec.incoming(media, mime)
                        question = await self.tutor.transcribe(wav)
                    else:
                        question = job["input_content"]
                    await asyncio.to_thread(self.store.update, lease, question_text=question)
                answer, wav = await self.tutor.answer(question, history, reference)
                ogg = await self.codec.outgoing(wav)
                media_id = await self.meta.upload(ogg)
                await asyncio.to_thread(
                    self.store.update,
                    lease,
                    answer_text=answer,
                    reply_media_id=media_id,
                    status="ready",
                )
        await self.send_part(lease, "text")
        if lease.job.get("reply_media_id"):
            await self.send_part(lease, "audio")
        await asyncio.to_thread(self.store.finish, lease)

    async def run_once(self) -> bool:
        lease = await asyncio.to_thread(self.store.claim)
        if lease is None:
            return False
        try:
            await self.process(lease)
        except asyncio.CancelledError:
            # The durable row is recovered when the connection lock is released.
            raise
        except AmbiguousSend:
            await asyncio.to_thread(self.store.finish, lease, "unknown")
            logger.warning("whatsapp_delivery_unknown")
        except PermissionError:
            await asyncio.to_thread(self.store.finish, lease, "failed")
        except Exception:
            logger.warning(
                "whatsapp_job_failed"
            )  # Never log payloads, phone numbers or provider errors.
            if lease.job["text_state"] == "sending" or lease.job["audio_state"] == "sending":
                await asyncio.to_thread(self.store.finish, lease, "unknown")
            elif lease.job["attempts"] >= 3:
                try:
                    if not lease.job.get("text_message_id"):
                        await asyncio.to_thread(
                            self.store.update, lease, answer_text=UNAVAILABLE, reply_media_id=None
                        )
                        await self.send_part(lease, "text")
                    await asyncio.to_thread(self.store.finish, lease, "failed")
                except Exception:
                    await asyncio.to_thread(self.store.finish, lease, "unknown")
            else:
                await asyncio.to_thread(self.store.retry, lease)
        finally:
            await asyncio.to_thread(self.store.release, lease)
        return True

    async def run(self) -> None:
        cycles = 0
        while True:
            try:
                worked = await self.run_once()
                cycles += 1
                if cycles % 60 == 0:
                    await asyncio.to_thread(self.store.prune)
                if not worked:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("whatsapp_worker_unavailable")
                await asyncio.sleep(5)
