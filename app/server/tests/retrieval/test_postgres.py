import os
from contextlib import closing
from pathlib import Path

import psycopg2
import pytest
from rahbar.retrieval.postgres import PostgresTextSessions
from rahbar.retrieval.sessions import SessionConflict

DSN=os.environ.get('RAHBAR_RAG_TEST_POSTGRES_DSN')
pytestmark=pytest.mark.skipif(not DSN,reason='requires disposable retrieval PostgreSQL')


def test_postgres_reopen_and_stale_write():
    with closing(psycopg2.connect(DSN)) as db:
        with db.cursor() as cursor:
            cursor.execute('SELECT current_database()')
            assert cursor.fetchone()[0]=='rahbar_retrieval_test'
            cursor.execute((Path(__file__).resolve().parents[4]/'tools/retrieval/postgres.sql').read_text())
        db.commit()
    store=PostgresTextSessions(DSN)
    identifier=store.create('v1')
    for number in range(6):
        revision, history=store.load(identifier,'v1')
        store.append(identifier,'v1',revision,history,str(number),'approved')
    reopened=PostgresTextSessions(DSN)
    assert reopened.load(identifier,'v1')[0]==6
    assert len(reopened.load(identifier,'v1')[1])==4
    with pytest.raises(SessionConflict):
        reopened.append(identifier,'v1',0,[],'late','stale')
    with pytest.raises(SessionConflict):
        reopened.load(identifier,'v2')
