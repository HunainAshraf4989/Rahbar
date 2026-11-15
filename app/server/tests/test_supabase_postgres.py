"""Run against an empty disposable DB with RAHBAR_TEST_POSTGRES_DSN set."""

import os
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest
from fastapi.testclient import TestClient
from psycopg2.pool import ThreadedConnectionPool
from rahbar.api import create_app
from rahbar.config import SupabaseSettings
from rahbar.supabase import PostgresSessionStore, StorageError
from test_sessions import Player, session
from test_sessions import prepared as prepared

from tools.import_supabase import import_records

DSN = os.environ.get('RAHBAR_TEST_POSTGRES_DSN')
pytestmark = pytest.mark.skipif(not DSN, reason='requires disposable PostgreSQL')
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def cloud(prepared, tmp_path, monkeypatch):
    pipeline, lesson, directory, calls, _ = prepared
    with closing(psycopg2.connect(DSN)) as db:
        with db.cursor() as cur:
            cur.execute('SELECT current_database()')
            assert cur.fetchone()[0] == 'rahbar_runtime_test'
            cur.execute((ROOT / 'rahbar-postgresql-schema.sql').read_text())
        db.commit()
        import_records(db, lesson, {'manifest.json': b'fixture'}, [], 'fixture')
    def pool(minimum, maximum, dsn, **kwargs):
        kwargs['sslmode'] = 'disable'
        return ThreadedConnectionPool(minimum, maximum, dsn, **kwargs)
    monkeypatch.setattr('rahbar.supabase.ThreadedConnectionPool', pool)
    root = tmp_path / 'cloud'
    (root / '.cache').mkdir(parents=True)
    (root / '.cache/relay-lecture').symlink_to(directory, target_is_directory=True)
    (root / 'asset_gen').symlink_to(ROOT / 'asset_gen', target_is_directory=True)
    (root / 'content').symlink_to(ROOT / 'content', target_is_directory=True)
    monkeypatch.setattr('rahbar.api.prepare_cloud_lesson', lambda *args: root)
    yield pipeline, lesson, root, SupabaseSettings(DSN, 'https://fake.test', 'fake'), calls
    with closing(psycopg2.connect(DSN)) as db, db:
        with db.cursor() as cur:
            cur.execute('DROP SCHEMA rahbar CASCADE')


def test_cloud_playback_interrupt_restart_and_fencing(cloud):
    pipeline, lesson, root, settings, calls = cloud
    def app():
        return create_app(pipeline, project_root=root, storage_settings=settings)
    with TestClient(app()) as client:
        assert client.get('/health/live').json()['storage_backend'] == 'supabase'
        sid = session(client)
        with client.websocket_connect(f'/dev/lecture/sessions/{sid}/events') as ws:
            player = Player(ws)
            player.send('lesson.control', {'action': 'play', 'sentence_id': None})
            end = player.audio()[-1]
            player.ack(end, 'playback.started')
            player.ack(end)
            second = player.audio()[-1]
            player.ack(second, 'playback.started')
            player.question()
            answer = player.audio()[-1]
            assert player.read()['type'] == 'turn.completed'
            player.ack(answer, 'playback.started')
            player.ack(answer)
            assert player.audio()[-1]['payload']['sentence_id'] == lesson.sentences[1].sentence_id
    # New application and new pool recover the durable cursor, not process memory.
    with TestClient(app()) as client:
        state = client.get(f'/dev/lecture/sessions/{sid}').json()
        assert state['cursor']['revision'] == 1
        assert state['cursor']['restart_sentence_id'] == lesson.sentences[1].sentence_id
        with client.websocket_connect(f'/dev/lecture/sessions/{sid}/events') as ws:
            assert Player(ws).state['paused']
        assert client.get(f'/dev/lecture/sessions/{"not-a-uuid"}').status_code == 404
    store = PostgresSessionStore(settings)
    store.bind(lesson)
    try:
        old = store.get(sid)
        store.save(old.model_copy(update={'generation': old.generation + 1}))
        with pytest.raises(StorageError, match='Stale'):
            store.save(old)
        current = store.get(sid)
        assert current.generation == old.generation + 1
        with pytest.raises(StorageError, match='Stale'):
            store.save(current.model_copy(update={'connection_epoch': uuid4().hex}))
        with pytest.raises(StorageError, match='Stale cursor'):
            store.save(current.model_copy(update={'cursor': current.cursor.model_copy(update={'revision': 0})}))
    finally:
        store.close()
    assert len(calls) == 3
