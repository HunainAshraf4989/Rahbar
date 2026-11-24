"""Durable inbox, per-student serialization and five-pair history in PostgreSQL."""

from dataclasses import dataclass
from typing import Any

from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from .registration_notice import enqueue_notice
from .webhook import IncomingMessage


class QueueFull(Exception):
    pass


@dataclass
class Lease:
    connection: Any
    job: dict[str, Any]
    key: str


class WhatsAppStore:
    def __init__(self, dsn: str, business_phone_id: str, workers: int = 2) -> None:
        self.phone_id = business_phone_id
        self.pool = ThreadedConnectionPool(
            1,
            workers + 4,
            dsn,
            connect_timeout=5,
            sslmode="require",
            options="-c statement_timeout=10000",
        )

    def close(self) -> None:
        self.pool.closeall()

    def accept(self, messages: list[IncomingMessage]) -> None:
        conn = self.pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                # Serialize capacity decisions only; no network/model work occurs in this transaction.
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    ("wa-ingress:" + self.phone_id,),
                )
                for message in messages:
                    cur.execute(
                        "SELECT principal_id FROM rahbar.learner_accounts "
                        "WHERE phone_number=%s AND disabled_at IS NULL",
                        ("+" + message.sender,),
                    )
                    account = cur.fetchone()
                    if not account:
                        enqueue_notice(cur, self.phone_id, message)
                        continue
                    cur.execute(
                        "SELECT 1 FROM rahbar.wa_jobs WHERE business_phone_id=%s "
                        "AND provider_message_id=%s",
                        (self.phone_id, message.message_id),
                    )
                    if cur.fetchone():
                        continue
                    # Old events cannot be answered inside WhatsApp's 24-hour service window.
                    cur.execute(
                        "SELECT %s BETWEEN extract(epoch FROM now())-86400 "
                        "AND extract(epoch FROM now())+300",
                        (message.timestamp,),
                    )
                    window = cur.fetchone()
                    if not window or not window[0]:
                        continue
                    cur.execute(
                        "SELECT count(*), count(*) FILTER (WHERE sender=%s) "
                        "FROM rahbar.wa_jobs WHERE business_phone_id=%s "
                        "AND status IN ('queued','processing','ready')",
                        (message.sender, self.phone_id),
                    )
                    counts = cur.fetchone()
                    if counts is None:
                        raise RuntimeError("Queue count unavailable")
                    total, own = counts
                    if total >= 500 or own >= 10:
                        raise QueueFull()
                    cur.execute(
                        "INSERT INTO rahbar.wa_jobs (principal_id,business_phone_id,sender,"
                        "provider_message_id,source_timestamp,message_kind,input_content,"
                        "quoted_message_id,created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp()) "
                        "ON CONFLICT (business_phone_id,provider_message_id) DO NOTHING",
                        (
                            account[0],
                            self.phone_id,
                            message.sender,
                            message.message_id,
                            message.timestamp,
                            message.kind,
                            message.content,
                            message.reference_id,
                        ),
                    )
        finally:
            self.pool.putconn(conn)

    def claim(self) -> Lease | None:
        conn = self.pool.getconn()
        key = None
        try:
            with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT sender FROM (SELECT DISTINCT ON (sender) "
                    "sender,created_at,job_id,available_at FROM rahbar.wa_jobs "
                    "WHERE business_phone_id=%s AND status IN ('queued','processing','ready') "
                    "ORDER BY sender,created_at,job_id) heads WHERE available_at<=now() "
                    "ORDER BY created_at,job_id LIMIT 32",
                    (self.phone_id,),
                )
                candidates = cur.fetchall()
                for row in candidates:
                    key = "wa-worker:" + self.phone_id + ":" + row["sender"]
                    cur.execute(
                        "SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked", (key,)
                    )
                    locked = cur.fetchone()
                    if not locked or not locked["locked"]:
                        key = None
                        continue
                    cur.execute(
                        "SELECT * FROM rahbar.wa_jobs WHERE business_phone_id=%s AND sender=%s "
                        "AND status IN ('queued','processing','ready') AND available_at<=now() "
                        "ORDER BY created_at,job_id LIMIT 1 FOR UPDATE",
                        (self.phone_id, row["sender"]),
                    )
                    job = cur.fetchone()
                    if job:
                        cur.execute(
                            "UPDATE rahbar.wa_jobs SET status='processing',attempts=attempts+1,"
                            "updated_at=now() WHERE job_id=%s",
                            (job["job_id"],),
                        )
                        job["attempts"] += 1
                        return Lease(conn, dict(job), key)
                    cur.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (key,))
                    key = None
            return None
        except Exception:
            key = "discarded"
            self.pool.putconn(conn, close=True)
            raise
        finally:
            if key is None:
                self.pool.putconn(conn)

    def release(self, lease: Lease) -> None:
        conn = lease.connection
        discard = False
        try:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (lease.key,))
            conn.commit()
        except Exception:
            discard = True
            raise
        finally:
            self.pool.putconn(conn, close=discard)

    def authorized(self, lease: Lease) -> bool:
        with lease.connection, lease.connection.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM rahbar.learner_accounts WHERE principal_id=%s "
                "AND phone_number=%s AND disabled_at IS NULL",
                (lease.job["principal_id"], "+" + lease.job["sender"]),
            )
            return cur.fetchone() is not None

    def history(self, lease: Lease) -> list[dict[str, Any]]:
        with lease.connection, lease.connection.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT question_message_id,text_message_id,audio_message_id,question,answer "
                "FROM rahbar.wa_history WHERE principal_id=%s AND business_phone_id=%s "
                "ORDER BY completed_at DESC,job_id DESC LIMIT 5",
                (lease.job["principal_id"], self.phone_id),
            )
            return list(reversed([dict(row) for row in cur.fetchall()]))

    def update(self, lease: Lease, **fields: object) -> None:
        allowed = {
            "status",
            "question_text",
            "answer_text",
            "reply_media_id",
            "text_state",
            "audio_state",
            "text_message_id",
            "audio_message_id",
        }
        if not fields or set(fields) - allowed:
            raise ValueError("Unsupported job update")
        assignments = ",".join(name + "=%s" for name in fields)
        with lease.connection, lease.connection.cursor() as cur:
            cur.execute(
                f"UPDATE rahbar.wa_jobs SET {assignments},updated_at=now() WHERE job_id=%s",
                (*fields.values(), lease.job["job_id"]),
            )
        lease.job.update(fields)

    def finish(self, lease: Lease, status: str = "done") -> None:
        if status not in {"done", "failed", "unknown"}:
            raise ValueError("Invalid terminal status")
        job = lease.job
        with lease.connection, lease.connection.cursor() as cur:
            if job.get("text_message_id") and job.get("question_text") and job.get("answer_text"):
                cur.execute(
                    "INSERT INTO rahbar.wa_history (job_id,principal_id,business_phone_id,"
                    "question_message_id,text_message_id,audio_message_id,question,answer) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (job_id) DO NOTHING",
                    (
                        job["job_id"],
                        job["principal_id"],
                        self.phone_id,
                        job["provider_message_id"],
                        job["text_message_id"],
                        job.get("audio_message_id"),
                        job["question_text"],
                        job["answer_text"],
                    ),
                )
            cur.execute(
                "UPDATE rahbar.wa_jobs SET status=%s,input_content=NULL,quoted_message_id=NULL,"
                "question_text=NULL,answer_text=NULL,reply_media_id=NULL,updated_at=now() "
                "WHERE job_id=%s",
                (status, job["job_id"]),
            )

    def retry(self, lease: Lease) -> None:
        with lease.connection, lease.connection.cursor() as cur:
            cur.execute(
                "UPDATE rahbar.wa_jobs SET status='queued',available_at=now()+"
                "make_interval(secs=>%s),updated_at=now() WHERE job_id=%s",
                (min(300, 5 * 2 ** min(lease.job["attempts"], 5)), lease.job["job_id"]),
            )

    def prune(self) -> None:
        conn = self.pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM rahbar.wa_jobs WHERE status IN ('done','failed','unknown') "
                    "AND created_at<now()-interval '8 days'"
                )
        finally:
            self.pool.putconn(conn)
