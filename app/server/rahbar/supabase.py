"""Private Supabase lesson cache and PostgreSQL persistence for the relay demo."""

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
from psycopg2.pool import ThreadedConnectionPool  # type: ignore[import-untyped]

from rahbar.config import SupabaseSettings
from rahbar.contracts.models import (
    AudioFormat,
    Capabilities,
    LessonCursor,
    PreparedLesson,
    SessionState,
    TeachingContext,
)
from rahbar.engine.sessions import SessionStore


class StorageError(RuntimeError):
    """Safe public error; never carries driver diagnostics or credentials."""


class PostgresSessionStore(SessionStore):
    def __init__(self, settings: SupabaseSettings) -> None:
        try:
            self.pool = ThreadedConnectionPool(
                1, 4, settings.database_url, sslmode="require", connect_timeout=10,
                application_name="rahbar-lecture", keepalives=1, keepalives_idle=15,
                keepalives_interval=5, keepalives_count=3, tcp_user_timeout=30000,
                options="-c statement_timeout=15000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=30000")
        except Exception:
            raise StorageError("Supabase database unavailable") from None

    def close(self) -> None:
        self.pool.closeall()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        db = None
        broken = False
        try:
            db = self.pool.getconn()
            with db:
                yield db
        except (KeyError, StorageError):
            raise
        except Exception:
            broken = True
            raise StorageError("Supabase database operation failed") from None
        finally:
            if db is not None:
                self.pool.putconn(db, close=broken or bool(db.closed))

    def release(self) -> tuple[str, str]:
        with self.connection() as db, db.cursor() as cur:
            cur.execute("SELECT manifest_ref, encode(manifest_sha256,'hex') FROM rahbar.course_releases "
                        "WHERE course_key=%s AND content_version=%s", ("relay-demo", "relay-demo-v1"))
            row = cur.fetchone()
            if row is None:
                raise StorageError("Relay lesson has not been imported")
            return str(row[0]), str(row[1])

    def bind(self, lesson: PreparedLesson) -> None:
        self.lesson = lesson
        with self.connection() as db, db.cursor() as cur:
            cur.execute("SELECT l.lesson_version_id::text,u.unit_version_id::text FROM rahbar.lesson_versions l "
                        "JOIN rahbar.unit_versions u USING(unit_version_id) "
                        "JOIN rahbar.course_releases r USING(release_id) WHERE r.course_key=%s "
                        "AND r.content_version=%s AND u.unit_key=%s AND l.lesson_key=%s AND l.lesson_version=%s",
                        ("relay-demo", lesson.content_version, lesson.unit_id, lesson.lesson_id, lesson.lesson_version))
            row = cur.fetchone()
            if row is None:
                raise StorageError("Imported lesson version is missing")
            self.lesson_version_id, self.unit_version_id = row

    def save(self, state: SessionState) -> None:
        lesson = self.lesson
        if (state.cursor.lesson_version != lesson.lesson_version or state.context.mode != "tutor"
                or state.active_attempt_id is not None):
            raise StorageError("Unsupported session version or mode")
        sid, cid, epoch = (str(UUID(value)) for value in
                           (state.session_id, state.cursor.cursor_id, state.connection_epoch))
        with self.connection() as db, db.cursor() as cur:
            cur.execute("SELECT generation,connection_epoch::text,cursor_id::text,lesson_version_id::text "
                        "FROM rahbar.engine_sessions WHERE session_id=%s FOR UPDATE", (sid,))
            old = cur.fetchone()
            if old:
                if (old[0] > state.generation or (old[0] == state.generation and old[1] != epoch)
                        or old[2] != cid or old[3] != self.lesson_version_id):
                    raise StorageError("Stale session update rejected")
                cur.execute("SELECT revision FROM rahbar.lesson_cursors WHERE cursor_id=%s FOR UPDATE", (cid,))
                if cur.fetchone()[0] > state.cursor.revision:
                    raise StorageError("Stale cursor update rejected")
            else:
                principal = str(uuid5(NAMESPACE_URL, "rahbar:web-demo:" + sid))
                cur.execute("INSERT INTO rahbar.learning_principals(principal_id,host_key,subject_key) "
                            "VALUES(%s,'rahbar-web-demo',%s)", (principal, sid))
                cur.execute("INSERT INTO rahbar.lesson_cursors(cursor_id,principal_id,lesson_version_id,"
                            "restart_segment_key) VALUES(%s,%s,%s,%s)",
                            (cid, principal, self.lesson_version_id, state.cursor.restart_sentence_id))
                cur.execute("INSERT INTO rahbar.engine_sessions(session_id,principal_id,unit_version_id,"
                            "lesson_version_id,cursor_id,mode,idempotency_key,request_digest) "
                            "VALUES(%s,%s,%s,%s,%s,'tutor',%s,%s)",
                            (sid, principal, self.unit_version_id, self.lesson_version_id, cid, sid,
                             hashlib.sha256(sid.encode()).digest()))
            cur.execute("UPDATE rahbar.engine_sessions SET confirmed_segment_key=%s,connection_epoch=%s,"
                        "generation=%s,paused=%s,visuals_enabled=%s WHERE session_id=%s",
                        (state.context.confirmed_sentence_id, epoch, state.generation, state.paused,
                         state.capabilities.visuals, sid))
            cur.execute("UPDATE rahbar.lesson_cursors SET last_completed_segment_key=%s,restart_segment_key=%s,"
                        "revision=%s,updated_at=now() WHERE cursor_id=%s",
                        (state.cursor.last_completed_sentence_id, state.cursor.restart_sentence_id,
                         state.cursor.revision, cid))

    def get(self, session_id: str) -> SessionState:
        try:
            sid = str(UUID(session_id))
        except ValueError:
            raise KeyError(session_id) from None
        with self.connection() as db, db.cursor() as cur:
            cur.execute("SELECT e.session_id::text,e.connection_epoch::text,e.generation,e.paused,"
                        "e.visuals_enabled,e.confirmed_segment_key,c.cursor_id::text,c.revision,"
                        "c.last_completed_segment_key,c.restart_segment_key,l.lesson_version,l.lesson_key,"
                        "u.unit_key,r.content_version,r.course_key FROM rahbar.engine_sessions e "
                        "JOIN rahbar.lesson_cursors c USING(cursor_id) "
                        "JOIN rahbar.lesson_versions l ON l.lesson_version_id=e.lesson_version_id "
                        "JOIN rahbar.unit_versions u ON u.unit_version_id=e.unit_version_id "
                        "JOIN rahbar.course_releases r USING(release_id) WHERE e.session_id=%s "
                        "AND e.mode='tutor' AND e.ended_at IS NULL", (sid,))
            row = cur.fetchone()
            if row is None:
                raise KeyError(session_id)
        target = next((s.visual_target for s in self.lesson.sentences if s.sentence_id == row[5]), None)
        return SessionState(
            session_id=UUID(row[0]).hex, connection_epoch=UUID(row[1]).hex, generation=row[2], paused=row[3],
            context=TeachingContext(course_id=row[14], content_version=row[13], unit_id=row[12],
                lesson_id=row[11], mode="tutor", confirmed_sentence_id=row[5],
                visual_context=target if row[4] else None),
            cursor=LessonCursor(cursor_id=UUID(row[6]).hex, revision=row[7],
                last_completed_sentence_id=row[8], restart_sentence_id=row[9], lesson_version=row[10],
                lesson_id=row[11], unit_id=row[12], content_version=row[13]),
            capabilities=Capabilities(input_audio=AudioFormat(sample_rate=16000), max_utterance_ms=30000,
                max_queue_chunks=300, stt_streaming=False, visuals=row[4], profile="real"))


def prepare_cloud_lesson(settings: SupabaseSettings, cache: Path,
                         store: PostgresSessionStore) -> Path:
    """Materialize immutable, checksum-verified private content for realtime playback."""
    try:
        reference, checksum = store.release()
        if not reference.startswith("supabase://rahbar-content/") or not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise StorageError("Invalid lesson manifest reference")
        key = reference.removeprefix("supabase://rahbar-content/")
        if ".." in PurePosixPath(key).parts or not key.endswith("/manifest.json"):
            raise StorageError("Invalid lesson manifest path")
        prefix = key.removesuffix("/manifest.json")
        headers = {"apikey": settings.secret_key}
        if settings.secret_key.startswith("eyJ"):
            headers["Authorization"] = "Bearer " + settings.secret_key
        with httpx.Client(base_url=settings.url.rstrip("/") + "/storage/v1/object/authenticated/rahbar-content/",
                          headers=headers, timeout=30, follow_redirects=False) as client:
            def download(remote: str, path: Path, expected: str) -> bytes:
                if path.exists():
                    data = path.read_bytes()
                    if hashlib.sha256(data).hexdigest() == expected:
                        return data
                response = client.get(remote)
                if response.status_code != 200 or hashlib.sha256(response.content).hexdigest() != expected:
                    raise StorageError("Lesson download or checksum verification failed")
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
                temp.write_bytes(response.content)
                temp.replace(path)
                return response.content

            root = cache / checksum
            manifest = json.loads(download(key, root / "manifest.json", checksum))
            for name, metadata in manifest["files"].items():
                parts = PurePosixPath(name).parts
                if ".." in parts or PurePosixPath(name).is_absolute() or "\\" in name:
                    raise StorageError("Invalid content path")
                if name.startswith("audio/"):
                    destination = root / ".cache/relay-lecture" / name.removeprefix("audio/")
                elif name.startswith("assets/"):
                    destination = root / "asset_gen/out" / name.removeprefix("assets/")
                elif name == "lesson.json":
                    destination = root / ".cache/relay-lecture/lesson.json"
                elif name == "lesson-source.json":
                    destination = root / "content/relay/lesson-source.json"
                elif name == "asset.json":
                    destination = root / name
                else:
                    raise StorageError("Unexpected content file")
                download(prefix + "/" + name, destination, metadata["sha256"])
            return root
    except StorageError:
        raise
    except Exception:
        raise StorageError("Supabase lesson preparation failed") from None
