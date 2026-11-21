import pytest
from rahbar.retrieval.sessions import SessionConflict, TextSessions


def test_session_isolation_history_bound_and_stale_write(tmp_path):
    store = TextSessions(tmp_path / 'state.sqlite')
    a, b = store.create('v1'), store.create('v1')
    for i in range(6):
        revision, history = store.load(a, 'v1')
        store.append(a, 'v1', revision, history, str(i), 'approved')
    assert len(store.load(a, 'v1')[1]) == 4
    assert store.load(b, 'v1') == (0, [])
    with pytest.raises(SessionConflict):
        store.append(a, 'v1', 0, [], 'late', 'stale')
    with pytest.raises(SessionConflict):
        store.load(a, 'v2')
    assert TextSessions(tmp_path / 'state.sqlite').load(a, 'v1')[0] == 6
