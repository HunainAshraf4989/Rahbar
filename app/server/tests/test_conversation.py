from rahbar.engine.conversation import LessonConversations


def test_lesson_memory_is_bounded_isolated_and_explicitly_erasable():
    store = LessonConversations()
    a = store.get('a','v1','lesson')
    for i in range(6):
        a.delivered(str(i), 'answer')
    snapshot = a.snapshot()
    assert len(snapshot) == 4 and snapshot[0][0] == '2'
    assert store.get('a','v1','lesson') is a  # reconnect
    assert not store.get('b','v1','lesson').snapshot()
    assert not store.get('a','v2','lesson').snapshot()
    store.end('a')
    assert not a.snapshot() and not store.get('a','v1','lesson').snapshot()
    assert len(snapshot) == 4  # an in-flight turn has an immutable snapshot


def test_expired_and_restarted_sessions_cannot_recover_history():
    store = LessonConversations(ttl_seconds=10)
    old = store.get('s','v','l')
    old.delivered('private question','answer')
    old.touched -= 11
    assert not store.get('s','v','l').snapshot()
    assert not old.turns
    assert not LessonConversations().get('s','v','l').snapshot()
