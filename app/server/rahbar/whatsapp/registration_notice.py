"""Durable, fixed registration reminders; no question text or tutor calls."""

import asyncio
import logging
from typing import Any

from psycopg2.extensions import cursor
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from .meta import AmbiguousSend, ChannelError, MetaClient
from .webhook import IncomingMessage

REGISTRATION_REQUIRED = (
    "You're not registered with Rahbar yet. Please sign up on Rahbar using this "
    "WhatsApp number to use the tutor."
)
logger = logging.getLogger("rahbar.whatsapp")


def enqueue_notice(cur: cursor, phone_id: str, message: IncomingMessage) -> None:
    """Called under the ingress transaction lock, after the account lookup fails."""
    cur.execute(
        "INSERT INTO rahbar.wa_registration_notices "
        "(business_phone_id,sender,provider_message_id,source_timestamp) "
        "SELECT %s,%s,%s,%s WHERE %s BETWEEN extract(epoch FROM now())-86400 "
        "AND extract(epoch FROM now())+300 AND NOT EXISTS "
        "(SELECT 1 FROM rahbar.learner_accounts WHERE phone_number=%s) AND NOT EXISTS "
        "(SELECT 1 FROM rahbar.wa_registration_notices WHERE business_phone_id=%s "
        "AND sender=%s AND created_at>=now()-interval '1 minute') "
        "AND (SELECT count(*) FROM rahbar.wa_registration_notices "
        "WHERE business_phone_id=%s AND status IN ('queued','sending'))<500 "
        "ON CONFLICT (business_phone_id,provider_message_id) DO NOTHING",
        (
            phone_id,
            message.sender,
            message.message_id,
            message.timestamp,
            message.timestamp,
            "+" + message.sender,
            phone_id,
            message.sender,
            phone_id,
        ),
    )


class RegistrationNotices:
    def __init__(self, pool: ThreadedConnectionPool, phone_id: str, meta: MetaClient) -> None:
        self.pool, self.phone_id, self.meta = pool, phone_id, meta

    def claim(self) -> dict[str, Any] | None:
        conn = self.pool.getconn()
        try:
            with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "WITH next AS (SELECT notice_id FROM rahbar.wa_registration_notices "
                    "WHERE business_phone_id=%s AND status='queued' "
                    "ORDER BY created_at,notice_id LIMIT 1 FOR UPDATE SKIP LOCKED) "
                    "UPDATE rahbar.wa_registration_notices n SET status='sending',updated_at=now() "
                    "FROM next WHERE n.notice_id=next.notice_id RETURNING n.*",
                    (self.phone_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            self.pool.putconn(conn)

    def eligible(self, notice: dict[str, Any]) -> bool:
        conn = self.pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT %s BETWEEN extract(epoch FROM now())-86000 "
                    "AND extract(epoch FROM now())+300 AND NOT EXISTS "
                    "(SELECT 1 FROM rahbar.learner_accounts WHERE phone_number=%s)",
                    (notice["source_timestamp"], "+" + notice["sender"]),
                )
                row = cur.fetchone()
                return bool(row and row[0])
        finally:
            self.pool.putconn(conn)

    def finish(self, notice: dict[str, Any], status: str) -> None:
        conn = self.pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE rahbar.wa_registration_notices SET status=%s,updated_at=now() "
                    "WHERE notice_id=%s AND status='sending'",
                    (status, notice["notice_id"]),
                )
        finally:
            self.pool.putconn(conn)

    def prune(self) -> None:
        conn = self.pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE rahbar.wa_registration_notices SET status='unknown',updated_at=now() "
                    "WHERE business_phone_id=%s AND status='sending' "
                    "AND updated_at<now()-interval '2 minutes'",
                    (self.phone_id,),
                )
                cur.execute(
                    "DELETE FROM rahbar.wa_registration_notices WHERE business_phone_id=%s "
                    "AND created_at<now()-interval '8 days'",
                    (self.phone_id,),
                )
        finally:
            self.pool.putconn(conn)

    async def run_once(self) -> bool:
        notice = await asyncio.to_thread(self.claim)
        if notice is None:
            return False
        try:
            if not await asyncio.to_thread(self.eligible, notice):
                await asyncio.to_thread(self.finish, notice, "skipped")
                return True
            await self.meta.send(
                notice["sender"], notice["provider_message_id"], text=REGISTRATION_REQUIRED
            )
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.to_thread(self.finish, notice, "unknown"))
            raise
        except AmbiguousSend:
            await asyncio.to_thread(self.finish, notice, "unknown")
        except ChannelError:
            await asyncio.to_thread(self.finish, notice, "failed")
        except Exception:
            await asyncio.to_thread(self.finish, notice, "unknown")
            logger.warning("whatsapp_registration_notice_failed")
        else:
            await asyncio.to_thread(self.finish, notice, "done")
        return True

    async def run(self) -> None:
        cycles = 0
        while True:
            try:
                worked = await self.run_once()
                cycles += 1
                if cycles % 60 == 0:
                    await asyncio.to_thread(self.prune)
                if not worked:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("whatsapp_registration_notices_unavailable")
                await asyncio.sleep(5)
