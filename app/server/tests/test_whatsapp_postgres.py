"""Opt-in PostgreSQL tests. Only synthetic accounts created here are removed."""

import asyncio
import os
import time
from contextlib import closing
from uuid import uuid4

import psycopg2
import pytest
from rahbar.whatsapp.registration_notice import REGISTRATION_REQUIRED, RegistrationNotices
from rahbar.whatsapp.store import WhatsAppStore
from rahbar.whatsapp.webhook import IncomingMessage
from rahbar.whatsapp.worker import WhatsAppWorker, resolve_reference

DSN = os.environ.get("RAHBAR_TEST_WHATSAPP_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="requires WhatsApp development database")


@pytest.fixture
def learner():
    identity = str(uuid4())
    phone = "+999" + str(int(uuid4().hex[:10], 16)).zfill(13)[-11:]
    with closing(psycopg2.connect(DSN, sslmode="require")) as db, db:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO rahbar.learning_principals (principal_id,host_key,subject_key) "
                "VALUES (%s,'whatsapp-test',%s)",
                (identity, identity),
            )
            cur.execute(
                "INSERT INTO rahbar.learner_accounts "
                "(principal_id,email,password_hash,full_name,phone_number) "
                "VALUES (%s,%s,'test-not-a-login-hash','Synthetic Student',%s)",
                (identity, identity + "@example.com", phone),
            )
    store = WhatsAppStore(DSN, "test-business-" + identity)
    yield identity, phone, store
    store.close()
    with closing(psycopg2.connect(DSN, sslmode="require")) as db, db:
        with db.cursor() as cur:
            cur.execute(
                "DELETE FROM rahbar.wa_registration_notices WHERE business_phone_id=%s",
                (store.phone_id,),
            )
            cur.execute("DELETE FROM rahbar.wa_jobs WHERE principal_id=%s", (identity,))
            cur.execute("DELETE FROM rahbar.wa_history WHERE principal_id=%s", (identity,))
            cur.execute("DELETE FROM rahbar.learner_accounts WHERE principal_id=%s", (identity,))
            cur.execute("DELETE FROM rahbar.learning_principals WHERE principal_id=%s", (identity,))


def message(phone, suffix="0", reference=None):
    return IncomingMessage(
        "wamid." + suffix,
        phone.lstrip("+"),
        int(time.time()),
        "text",
        "What does a relay do?",
        reference,
    )


def test_number_allowlist_and_idempotent_acceptance(learner):
    _, phone, store = learner
    store.accept([message("+888123456789")])
    assert store.claim() is None
    store.accept([message(phone), message(phone)])
    store.accept([message(phone)])
    lease = store.claim()
    assert lease and lease.job["provider_message_id"] == "wamid.0"
    store.finish(lease, "failed")
    store.release(lease)
    assert store.claim() is None


def test_unregistered_number_gets_fixed_notice_without_tutor_history(learner):
    _, _, store = learner
    unknown = "+888" + str(int(uuid4().hex[:10], 16)).zfill(11)[-11:]
    incoming = message(unknown, "unregistered")
    store.accept([incoming, incoming])
    calls = []

    class Meta:
        async def send(self, *args, **kwargs):
            calls.append((args, kwargs))
            return "wamid.registration-reply"

    notices = RegistrationNotices(store.pool, store.phone_id, Meta())
    assert store.claim() is None
    assert asyncio.run(notices.run_once())
    assert calls == [((unknown.lstrip("+"), incoming.message_id), {"text": REGISTRATION_REQUIRED})]
    assert not asyncio.run(notices.run_once())
    store.accept([incoming, message(unknown, "another")])
    assert not asyncio.run(notices.run_once())  # Redelivery and rapid repeat are suppressed.
    conn = store.pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM rahbar.wa_history WHERE business_phone_id=%s",
                (store.phone_id,),
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT status FROM rahbar.wa_registration_notices WHERE business_phone_id=%s",
                (store.phone_id,),
            )
            assert cur.fetchall() == [("done",)]
    finally:
        store.pool.putconn(conn)


def test_registration_notice_rechecks_new_signup_before_sending(learner):
    identity, _, store = learner
    unknown = "+888" + str(int(uuid4().hex[:10], 16)).zfill(11)[-11:]
    store.accept([message(unknown, "before-signup")])
    conn = store.pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE rahbar.learner_accounts SET phone_number=%s WHERE principal_id=%s",
                (unknown, identity),
            )
    finally:
        store.pool.putconn(conn)
    notices = RegistrationNotices(store.pool, store.phone_id, None)
    assert asyncio.run(notices.run_once())  # A provider call would fail with this fixture.
    assert not asyncio.run(notices.run_once())


def test_registration_notice_does_not_resend_ambiguous_delivery(learner):
    from rahbar.whatsapp.meta import AmbiguousSend

    _, _, store = learner
    unknown = "+888" + str(int(uuid4().hex[:10], 16)).zfill(11)[-11:]
    store.accept([message(unknown, "timeout")])
    calls = []

    class Meta:
        async def send(self, *args, **kwargs):
            calls.append(1)
            raise AmbiguousSend("fixture")

    notices = RegistrationNotices(store.pool, store.phone_id, Meta())
    assert asyncio.run(notices.run_once())
    assert not asyncio.run(notices.run_once())
    assert len(calls) == 1


def test_stale_unknown_message_does_not_queue_registration_notice(learner):
    _, _, store = learner
    store.accept([IncomingMessage("wamid.stale", "888123456789", 1, "text", "old")])
    assert not asyncio.run(RegistrationNotices(store.pool, store.phone_id, None).run_once())


def test_database_retains_only_five_pairs_and_reference_ids(learner):
    _, phone, store = learner
    for index in range(7):
        store.accept([message(phone, str(index))])
        lease = store.claim()
        assert lease
        store.update(
            lease,
            question_text="Question " + str(index),
            answer_text="Answer " + str(index),
            text_message_id=f"wamid.text-{index}",
            audio_message_id=f"wamid.audio-{index}",
        )
        store.finish(lease)
        store.release(lease)
    store.accept([message(phone, "next")])
    lease = store.claim()
    assert lease
    history = store.history(lease)
    assert [pair["question"] for pair in history] == ["Question " + str(i) for i in range(2, 7)]
    assert resolve_reference(history, "wamid.0") is None
    assert resolve_reference(history, "wamid.text-2")["answer"] == "Answer 2"
    assert resolve_reference(history, "wamid.audio-6")["question"] == "Question 6"
    store.finish(lease, "failed")
    store.release(lease)
    store.accept([message(phone, "0")])  # Receipt deduplication outlives the history window.
    assert store.claim() is None


def test_claim_serializes_same_student_across_workers(learner):
    _, phone, store = learner
    store.accept([message(phone, "one"), message(phone, "two")])
    first = store.claim()
    assert first and first.job["provider_message_id"] == "wamid.one"
    assert store.claim() is None
    store.finish(first, "failed")
    store.release(first)
    second = store.claim()
    assert second and second.job["provider_message_id"] == "wamid.two"
    store.finish(second, "failed")
    store.release(second)


def test_retry_does_not_let_newer_questions_overtake(learner):
    _, phone, store = learner
    store.accept([message(phone, "first"), message(phone, "second")])
    first = store.claim()
    assert first and first.job["provider_message_id"] == "wamid.first"
    store.retry(first)
    store.release(first)
    assert store.claim() is None
    conn = store.pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE rahbar.wa_jobs SET available_at=now() WHERE job_id=%s",
                (first.job["job_id"],),
            )
    finally:
        store.pool.putconn(conn)
    retried = store.claim()
    assert retried and retried.job["provider_message_id"] == "wamid.first"
    store.finish(retried, "failed")
    store.release(retried)
    second = store.claim()
    assert second and second.job["provider_message_id"] == "wamid.second"
    store.finish(second, "failed")
    store.release(second)


def test_worker_never_calls_providers_for_revoked_number(learner):
    identity, phone, store = learner
    store.accept([message(phone)])
    with closing(psycopg2.connect(DSN, sslmode="require")) as db, db:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE rahbar.learner_accounts SET disabled_at=now() WHERE principal_id=%s",
                (identity,),
            )
    worker = WhatsAppWorker(store, None, None, None)
    assert asyncio.run(worker.run_once())
    assert store.claim() is None


def test_uncertain_delivery_is_not_retried(learner):
    _, phone, store = learner
    store.accept([message(phone)])
    lease = store.claim()
    assert lease
    store.update(lease, text_state="sending")
    store.release(lease)  # Simulate a crash after recording send intent.
    worker = WhatsAppWorker(store, None, None, None)
    assert asyncio.run(worker.run_once())
    assert store.claim() is None


def test_queued_reference_outside_history_gets_clear_notice_without_model(learner):
    _, phone, store = learner
    store.accept([message(phone, reference="wamid.not-retained")])
    sent = []

    class Meta:
        async def send(self, recipient, question_id, **kwargs):
            sent.append(kwargs["text"])
            return "wamid.expired-notice"

    worker = WhatsAppWorker(store, Meta(), None, None)
    assert asyncio.run(worker.run_once())
    assert "last five" in sent[0]
    assert store.claim() is None


def test_signup_login_profile_and_duplicate_phone(learner, monkeypatch):
    from fastapi.testclient import TestClient
    from rahbar.platform_api import create_platform_app

    monkeypatch.setenv("RAHBAR_DATABASE_URL", DSN)
    monkeypatch.setenv("RAHBAR_WEB_ORIGIN", "http://127.0.0.1:4173")
    email = str(uuid4()) + "@example.com"
    phone = "+999" + str(int(uuid4().hex[:10], 16)).zfill(13)[-11:]
    account_id = None
    client = TestClient(create_platform_app(), base_url="http://127.0.0.1:4173")
    headers = {"Origin": "http://127.0.0.1:4173"}
    details = {
        "full_name": "Synthetic   Student",
        "email": email,
        "password": "Fixture!92756",
        "phone_number": phone,
    }
    try:
        response = client.post("/api/v1/auth/register", json=details, headers=headers)
        assert response.status_code == 201, response.status_code
        account = response.json()
        account_id = account["account_id"]
        assert account["full_name"] == "Synthetic Student"
        assert account["phone_number"] == phone
        assert client.get("/api/v1/me").json()["full_name"] == "Synthetic Student"
        duplicate = client.post(
            "/api/v1/auth/register",
            json={**details, "email": str(uuid4()) + "@example.com"},
            headers=headers,
        )
        assert duplicate.status_code == 409
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
        assert client.get("/api/v1/me").status_code == 401
        login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": details["password"]},
            headers=headers,
        )
        assert login.status_code == 200
        assert login.json()["phone_number"] == phone
        with closing(psycopg2.connect(DSN, sslmode="require")) as db, db:
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE rahbar.learner_accounts SET full_name=NULL,phone_number=NULL "
                    "WHERE principal_id=%s",
                    (account_id,),
                )
        legacy = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": details["password"]},
            headers=headers,
        )
        assert legacy.status_code == 200
        assert legacy.json()["full_name"] is None
    finally:
        client.close()
        if account_id:
            with closing(psycopg2.connect(DSN, sslmode="require")) as db, db:
                with db.cursor() as cur:
                    cur.execute(
                        "DELETE FROM rahbar.auth_sessions WHERE principal_id=%s", (account_id,)
                    )
                    cur.execute(
                        "DELETE FROM rahbar.learner_accounts WHERE principal_id=%s", (account_id,)
                    )
                    cur.execute(
                        "DELETE FROM rahbar.learning_principals WHERE principal_id=%s",
                        (account_id,),
                    )


def test_full_worker_sends_text_and_audio_and_keeps_pair(learner):
    _, phone, store = learner
    store.accept([message(phone)])
    calls = []

    class Tutor:
        async def answer(self, question, history, reference):
            assert question == "What does a relay do?"
            assert history == [] and reference is None
            calls.append("tutor")
            return "Approved relay answer", b"wav-fixture"

    class Codec:
        async def outgoing(self, wav):
            assert wav == b"wav-fixture"
            return b"ogg-fixture"

    class Meta:
        async def upload(self, ogg):
            assert ogg == b"ogg-fixture"
            return "12345678"

        async def send(self, recipient, question_id, **kwargs):
            assert recipient == phone[1:]
            calls.append("text" if kwargs.get("text") else "audio")
            return "wamid." + calls[-1]

    worker = WhatsAppWorker(store, Meta(), Tutor(), Codec())
    assert asyncio.run(worker.run_once())
    assert calls == ["tutor", "text", "audio"]
    store.accept([message(phone, "followup")])
    lease = store.claim()
    assert lease
    pair = store.history(lease)[0]
    assert pair["question_message_id"] == "wamid.0"
    assert pair["text_message_id"] == "wamid.text"
    assert pair["audio_message_id"] == "wamid.audio"
    assert pair["answer"] == "Approved relay answer"
    with closing(psycopg2.connect(DSN, sslmode="require")) as db, db.cursor() as cur:
        cur.execute(
            "SELECT input_content,answer_text FROM rahbar.wa_jobs "
            "WHERE principal_id=%s AND provider_message_id='wamid.0'",
            (lease.job["principal_id"],),
        )
        assert cur.fetchone() == (None, None)
    store.finish(lease, "failed")
    store.release(lease)


def test_real_worker_ambiguous_send_is_not_resent(learner):
    from rahbar.whatsapp.meta import AmbiguousSend

    _, phone, store = learner
    store.accept([message(phone)])
    lease = store.claim()
    assert lease
    store.update(
        lease, question_text="Question", answer_text="Prepared answer", reply_media_id="12345"
    )
    store.release(lease)
    calls = []

    class Meta:
        async def send(self, *args, **kwargs):
            calls.append(1)
            raise AmbiguousSend("fixture")

    worker = WhatsAppWorker(store, Meta(), None, None)
    assert asyncio.run(worker.run_once())
    assert len(calls) == 1
    assert not asyncio.run(worker.run_once())
