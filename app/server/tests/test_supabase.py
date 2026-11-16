"""Cloud content validation without real credentials or provider calls."""

import hashlib
import json
from unittest.mock import patch

import httpx
import pytest
from rahbar.config import SupabaseSettings, load_supabase_settings
from rahbar.supabase import StorageError, prepare_cloud_lesson


def test_supabase_settings_encoding_and_override(tmp_path, monkeypatch):
    monkeypatch.setattr('rahbar.config.os.environ', {})
    (tmp_path / '.env').write_text('SUPABASE_DB_PASSWORD="a:b@c/#"\n')
    (tmp_path / '.env.local').write_text('NEXT_PUBLIC_SUPABASE_URL=https://example.supabase.co\nSUPABASE_SECRET_KEY=fake\n')
    settings = load_supabase_settings(tmp_path)
    assert settings.database_url == 'postgresql://postgres:a%3Ab%40c%2F%23@db.example.supabase.co:5432/postgres'
    assert 'fake' not in repr(settings)
    with (tmp_path / '.env.local').open('a') as file:
        file.write('DATABASE_URL=postgresql://postgres:[YOUR-PASSWORD]@pooler.test:5432/postgres\n')
    assert '@pooler.test:' in load_supabase_settings(tmp_path).database_url


@pytest.mark.parametrize('corrupt', [False, True])
def test_cloud_cache_checksums_reuse_and_repair(tmp_path, corrupt):
    data = b'prepared-content'
    manifest = json.dumps({'files': {'lesson.json': {'sha256': hashlib.sha256(data).hexdigest()}}}).encode()
    checksum = hashlib.sha256(manifest).hexdigest()
    class Store:
        def release(self):
            return 'supabase://rahbar-content/relay/manifest.json', checksum
    calls = []
    def handler(request):
        calls.append(request.url.path)
        body = manifest if request.url.path.endswith('manifest.json') else data
        return httpx.Response(200, content=body if not corrupt else b'bad')
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url='https://example.test/')
    settings = SupabaseSettings('unused', 'https://example.test', 'fake')
    with patch('rahbar.supabase.httpx.Client', return_value=client):
        if corrupt:
            with pytest.raises(StorageError, match='checksum'):
                prepare_cloud_lesson(settings, tmp_path, Store())
            assert not (tmp_path / checksum / 'manifest.json').exists()
            return
        root = prepare_cloud_lesson(settings, tmp_path, Store())
    assert (root / '.cache/relay-lecture/lesson.json').read_bytes() == data
    assert len(calls) == 2
    # A warm start must not redownload immutable content; corruption must be repaired.
    for broken in [False, True]:
        if broken:
            (root / '.cache/relay-lecture/lesson.json').write_bytes(b'corrupted')
        calls.clear()
        client = httpx.Client(transport=httpx.MockTransport(handler), base_url='https://example.test/')
        with patch('rahbar.supabase.httpx.Client', return_value=client):
            prepare_cloud_lesson(settings, tmp_path, Store())
        assert len(calls) == int(broken)
        assert (root / '.cache/relay-lecture/lesson.json').read_bytes() == data


def test_cloud_cache_rejects_path_escape(tmp_path):
    manifest = json.dumps({'files': {'audio/../../escape': {'sha256': 'unused'}}}).encode()
    class Store:
        def release(self):
            return 'supabase://rahbar-content/relay/manifest.json', hashlib.sha256(manifest).hexdigest()
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=manifest)),
                         base_url='https://example.test/')
    with patch('rahbar.supabase.httpx.Client', return_value=client), pytest.raises(StorageError, match='path'):
        prepare_cloud_lesson(SupabaseSettings('unused', 'https://example.test', 'fake'), tmp_path, Store())
    assert not (tmp_path / 'escape').exists()
