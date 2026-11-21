"""PostgreSQL text-session store; schema changes are an explicit migration."""
import json
import time
from contextlib import closing
from uuid import uuid4

import psycopg2

from rahbar.retrieval.sessions import SessionConflict


class PostgresTextSessions:
    def __init__(self, database_url: str, ttl: int = 1800):
        self._database_url, self.ttl = database_url, ttl

    def create(self, version: str) -> str:
        identifier = uuid4().hex
        try:
            with closing(psycopg2.connect(self._database_url, connect_timeout=10)) as db, db:
                with db.cursor() as cursor:
                    cursor.execute('DELETE FROM rahbar.rag_text_sessions WHERE expires < %s', (time.time(),))
                    cursor.execute('INSERT INTO rahbar.rag_text_sessions (id,version,expires) VALUES (%s,%s,%s)',
                                   (identifier, version, time.time() + self.ttl))
        except psycopg2.Error:
            raise RuntimeError('Text-session storage unavailable') from None
        return identifier

    def load(self, identifier: str, version: str) -> tuple[int, list[dict[str, str]]]:
        try:
            with closing(psycopg2.connect(self._database_url, connect_timeout=10)) as db, db:
                with db.cursor() as cursor:
                    cursor.execute('SELECT revision, history FROM rahbar.rag_text_sessions '
                                   'WHERE id=%s AND version=%s AND expires>%s',
                                   (identifier, version, time.time()))
                    row = cursor.fetchone()
        except psycopg2.Error:
            raise RuntimeError('Text-session storage unavailable') from None
        if row is None:
            raise SessionConflict('Unknown, expired, or incompatible session')
        return int(row[0]), row[1]

    def append(self, identifier: str, version: str, revision: int,
               history: list[dict[str, str]], question: str, approved_answer: str) -> None:
        updated = [*history, {'question': question, 'answer': approved_answer}][-4:]
        try:
            with closing(psycopg2.connect(self._database_url, connect_timeout=10)) as db, db:
                with db.cursor() as cursor:
                    cursor.execute('UPDATE rahbar.rag_text_sessions SET revision=revision+1,history=%s::jsonb,expires=%s '
                                   'WHERE id=%s AND version=%s AND revision=%s AND expires>%s',
                                   (json.dumps(updated), time.time()+self.ttl, identifier, version, revision, time.time()))
                    if cursor.rowcount != 1:
                        raise SessionConflict('Turn is stale or session expired')
        except psycopg2.Error:
            raise RuntimeError('Text-session storage unavailable') from None
