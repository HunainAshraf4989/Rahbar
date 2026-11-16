"""Copy the prepared relay lesson and anonymous demo progress to private Supabase storage."""

import argparse
import hashlib
import json
import mimetypes
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
from rahbar.config import load_supabase_settings
from rahbar.contracts.models import PreparedLesson, SessionState
from rahbar.engine.bake import cached_pcm
from rahbar.engine.lesson import relay_asset, relay_lesson

BUCKET = "rahbar-content"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode()


def inventory(root):
    directory = root / ".cache/relay-lecture"
    lesson = PreparedLesson.model_validate_json((directory / "lesson.json").read_text())
    asset = relay_asset(root)
    if lesson.lesson_version != relay_lesson(asset, root).lesson_version:
        raise ValueError("Prepared lesson is stale; stop before importing")
    files = {}
    for sentence in lesson.sentences:
        if sentence.audio is None:
            raise ValueError("Missing prepared audio")
        path = directory / Path(sentence.audio.path).name
        cached_pcm(path)
        data = path.read_bytes()
        if digest(data) != path.with_suffix(".sha256").read_text():
            raise ValueError("Audio checksum mismatch")
        files["audio/" + path.name] = data
        files["audio/" + path.with_suffix(".sha256").name] = path.with_suffix(".sha256").read_bytes()
    for view in asset.views:
        for name in (view.image, view.overlay):
            path = root / "asset_gen/out" / Path(name).name
            files["assets/" + path.name] = path.read_bytes()
    for name in ("relay_4pin.parts.json", "relay_4pin_views.json"):
        files["assets/" + name] = (root / "asset_gen/out" / name).read_bytes()
    files["lesson.json"] = (directory / "lesson.json").read_bytes()
    files["lesson-source.json"] = (root / "content/relay/lesson-source.json").read_bytes()
    files["asset.json"] = json_bytes(asset.model_dump())
    manifest = {"format": "rahbar-relay-export-v1", "review_status": "draft",
                "course_key": "relay-demo", "content_version": lesson.content_version,
                "lesson_version": lesson.lesson_version,
                "files": {name: {"sha256": digest(data), "bytes": len(data)}
                          for name, data in sorted(files.items())},
                "route_map": {**{s.audio.path: "audio/" + Path(s.audio.path).name
                                 for s in lesson.sentences},
                              **{path: "assets/" + Path(path).name
                                 for view in asset.views for path in (view.image, view.overlay)}}}
    files["manifest.json"] = json_bytes(manifest)
    sessions = []
    db_path = directory / "sessions.sqlite3"
    if db_path.exists():
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
            sessions = [SessionState.model_validate_json(row[0])
                        for row in db.execute("SELECT state FROM sessions ORDER BY id")]
    for state in sessions:
        if state.cursor.lesson_version != lesson.lesson_version or state.context.mode != "tutor":
            raise ValueError("Unsupported session version or mode; stop before importing")
    prefix = "relay-demo/" + digest(files["manifest.json"])
    return lesson, files, sessions, prefix


def ensure_schema(connection, schema_path):
    with connection.cursor() as cur:
        cur.execute("SELECT to_regnamespace('rahbar')")
        exists = cur.fetchone()[0] is not None
    connection.commit()
    if not exists:
        # Reviewed schema is transactional. It creates only the application schema.
        with connection.cursor() as cur:
            cur.execute(schema_path.read_text())
        connection.commit()
    with connection.cursor() as cur:
        cur.execute("SELECT to_regclass('rahbar.course_releases'), to_regclass('rahbar.engine_sessions')")
        if not all(cur.fetchone()):
            raise ValueError("Existing rahbar schema is incomplete; refusing to overwrite it")
    connection.commit()


def upload(client, key, data):
    path = "object/authenticated/" + BUCKET + "/" + key
    response = client.get(path)
    if response.is_success:
        if digest(response.content) != digest(data):
            raise ValueError("Existing storage object differs; refusing overwrite")
        return
    if response.status_code not in (400, 404):
        raise ValueError("Storage lookup failed")
    response = client.post("object/" + BUCKET + "/" + key, content=data,
                           headers={"Content-Type": mimetypes.guess_type(key)[0] or "application/octet-stream",
                                    "x-upsert": "false"})
    if not response.is_success:
        raise ValueError("Storage upload failed")
    response = client.get(path)
    if not response.is_success or digest(response.content) != digest(data):
        raise ValueError("Storage download verification failed")


def import_records(connection, lesson, files, sessions, prefix):
    def uid(name):
        return str(uuid5(NAMESPACE_URL, "rahbar:" + name))
    release = uid("relay-demo:" + lesson.content_version)
    unit = uid(release + ":" + lesson.unit_id)
    version = uid(unit + ":" + lesson.lesson_id + ":" + lesson.lesson_version)
    manifest_ref = f"supabase://{BUCKET}/{prefix}/manifest.json"
    with connection:
        with connection.cursor() as cur:
            statements = []

            def add(sql, parameters):
                statements.append(cur.mogrify(sql, parameters))

            cur.execute("SET LOCAL search_path = rahbar, public")
            cur.execute("SELECT release_id::text, manifest_ref, encode(manifest_sha256, 'hex') "
                        "FROM course_releases WHERE course_key=%s AND content_version=%s",
                        ("relay-demo", lesson.content_version))
            existing = cur.fetchone()
            if existing and existing != (release, manifest_ref, digest(files["manifest.json"])):
                raise ValueError("Conflicting course release; refusing overwrite")
            add("INSERT INTO course_releases (release_id,course_key,content_version,manifest_ref,manifest_sha256) "
                        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (release, "relay-demo", lesson.content_version, manifest_ref,
                         bytes.fromhex(digest(files["manifest.json"]))))
            add("INSERT INTO unit_versions (unit_version_id,release_id,unit_key) VALUES (%s,%s,%s) "
                        "ON CONFLICT DO NOTHING", (unit, release, lesson.unit_id))
            add("INSERT INTO lesson_versions (lesson_version_id,unit_version_id,lesson_key,lesson_version) "
                        "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (version, unit, lesson.lesson_id, lesson.lesson_version))
            for position, sentence in enumerate(lesson.sentences, 1):
                add("INSERT INTO lesson_segments (lesson_version_id,segment_key,position) "
                            "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (version, sentence.sentence_id, position))
            for state in sessions:
                principal = uid("local-demo:" + state.session_id)
                add("INSERT INTO learning_principals (principal_id,host_key,subject_key) "
                            "VALUES (%s,'rahbar-local-demo',%s) ON CONFLICT DO NOTHING", (principal, state.session_id))
                add("INSERT INTO lesson_cursors (cursor_id,principal_id,lesson_version_id,"
                            "last_completed_segment_key,restart_segment_key,revision) VALUES (%s,%s,%s,%s,%s,%s) "
                            "ON CONFLICT DO NOTHING", (str(UUID(state.cursor.cursor_id)), principal, version,
                            state.cursor.last_completed_sentence_id, state.cursor.restart_sentence_id, state.cursor.revision))
                add("INSERT INTO engine_sessions (session_id,principal_id,unit_version_id,lesson_version_id,"
                            "cursor_id,confirmed_segment_key,mode,idempotency_key,request_digest,connection_epoch,"
                            "generation,paused,visuals_enabled) VALUES (%s,%s,%s,%s,%s,%s,'tutor',%s,%s,%s,%s,true,%s) "
                            "ON CONFLICT DO NOTHING", (str(UUID(state.session_id)), principal, unit, version,
                            str(UUID(state.cursor.cursor_id)), state.context.confirmed_sentence_id,
                            "local-import:" + state.session_id, bytes.fromhex(digest(state.model_dump_json().encode())),
                            str(uuid4()), state.generation + 1, state.capabilities.visuals))
            cur.execute(b";".join(statements))
            cur.execute("SELECT count(*) FROM lesson_segments WHERE lesson_version_id=%s", (version,))
            if cur.fetchone()[0] != len(lesson.sentences):
                raise ValueError("Segment verification failed")
    return manifest_ref


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    lesson, files, sessions, prefix = inventory(root)
    report = {"audio_files": len(lesson.sentences), "content_files": len(files),
              "content_bytes": sum(map(len, files.values())), "demo_sessions": len(sessions),
              "bucket": BUCKET, "prefix": prefix, "applied": False}
    if args.apply:
        import psycopg2
        settings = load_supabase_settings(root)
        if not all((settings.database_url, settings.url, settings.secret_key)):
            raise ValueError("Missing DATABASE_URL, SUPABASE_URL, or SUPABASE_SECRET_KEY")
        if not settings.url.startswith("https://"):
            raise ValueError("Supabase URL must use HTTPS")
        headers = {"apikey": settings.secret_key}
        if settings.secret_key.startswith("eyJ"):
            headers["Authorization"] = "Bearer " + settings.secret_key
        def connect():
            return psycopg2.connect(
                settings.database_url, sslmode="require", connect_timeout=15,
                application_name="rahbar-content-import",
                keepalives=1, keepalives_idle=15, keepalives_interval=5, keepalives_count=3,
                tcp_user_timeout=30000,
                options="-c statement_timeout=30000 -c lock_timeout=10000 -c idle_in_transaction_session_timeout=60000")

        with closing(connect()) as connection:
            ensure_schema(connection, Path(__file__).resolve().parents[1] / "rahbar-postgresql-schema.sql")
        with httpx.Client(base_url=settings.url.rstrip("/") + "/storage/v1/", headers=headers,
                          timeout=60, follow_redirects=False) as client:
            bucket = client.get("bucket/" + BUCKET)
            if bucket.status_code in (400, 404):
                bucket = client.post("bucket", json={"id": BUCKET, "name": BUCKET, "public": False})
                if not bucket.is_success:
                    raise ValueError("Cannot create private Storage bucket")
                bucket = client.get("bucket/" + BUCKET)
            if not bucket.is_success or bucket.json().get("public") is not False:
                raise ValueError("Expected a private Storage bucket")
            for name, data in files.items():
                upload(client, prefix + "/" + name, data)
            snapshot = json_bytes([state.model_dump() for state in sessions])
            upload(client, "demo-snapshots/" + digest(snapshot) + ".json", snapshot)
        print("Storage objects verified; importing database records.", flush=True)
        with closing(connect()) as connection:
            report["manifest_ref"] = import_records(connection, lesson, files, sessions, prefix)
        report["applied"] = True
    output = root / ".cache/supabase-migration"
    output.mkdir(parents=True, exist_ok=True)
    (output / ("applied.json" if args.apply else "inventory.json")).write_bytes(json_bytes(report))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Provider and DB errors can contain credentials or private content.
        print("Import stopped (" + type(exc).__name__ + "). No credentials logged. Local files retained.", file=sys.stderr)
        sys.exit(1)
