"""Bounded text-session history with compare-and-swap turn persistence."""
import json
import sqlite3
import time
from pathlib import Path
from uuid import uuid4


class SessionConflict(ValueError):
    pass


class TextSessions:
    def __init__(self, path: Path, ttl: int = 1800):
        self.path, self.ttl = path, ttl
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS rag_sessions '
                       '(id TEXT PRIMARY KEY, version TEXT NOT NULL, revision INTEGER NOT NULL, '
                       'expires REAL NOT NULL, history TEXT NOT NULL)')

    def create(self, version: str) -> str:
        identifier = uuid4().hex
        with sqlite3.connect(self.path) as db:
            db.execute('DELETE FROM rag_sessions WHERE expires < ?', (time.time(),))
            db.execute('INSERT INTO rag_sessions VALUES (?, ?, 0, ?, ?)',
                       (identifier, version, time.time() + self.ttl, '[]'))
        return identifier

    def load(self, identifier: str, version: str) -> tuple[int, list[dict[str, str]]]:
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT revision, history FROM rag_sessions '
                             'WHERE id=? AND version=? AND expires>?',
                             (identifier, version, time.time())).fetchone()
        if row is None:
            raise SessionConflict('Unknown, expired, or incompatible session')
        return int(row[0]), json.loads(row[1])

    def append(self, identifier: str, version: str, revision: int,
               history: list[dict[str, str]], question: str, approved_answer: str) -> None:
        history = [*history, {'question': question, 'answer': approved_answer}][-4:]
        with sqlite3.connect(self.path) as db:
            result = db.execute('UPDATE rag_sessions SET revision=revision+1, history=?, expires=? '
                                'WHERE id=? AND version=? AND revision=? AND expires>?',
                                (json.dumps(history, ensure_ascii=False), time.time() + self.ttl,
                                 identifier, version, revision, time.time()))
            if result.rowcount != 1:
                raise SessionConflict('Turn is stale or session expired')
