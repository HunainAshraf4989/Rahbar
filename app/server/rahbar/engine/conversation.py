"""Volatile lesson conversation state; never written to session persistence."""
from dataclasses import dataclass, field
from time import monotonic


@dataclass
class LessonConversation:
    turns: list[tuple[str, str]] = field(default_factory=list)
    touched: float = field(default_factory=monotonic)

    def snapshot(self) -> tuple[tuple[str, str], ...]:
        self.touched = monotonic()
        return tuple(self.turns)

    def delivered(self, question: str, answer: str) -> None:
        self.turns = [*self.turns, (question, answer)][-4:]
        self.touched = monotonic()

    def clear(self) -> None:
        self.turns.clear()


class LessonConversations:
    """Single-worker session memory; short reconnects retain delivered context."""
    def __init__(self, ttl_seconds: float = 1800):
        self.ttl_seconds = ttl_seconds
        self.items: dict[tuple[str, str, str], LessonConversation] = {}

    def get(self, session: str, release: str, lesson: str) -> LessonConversation:
        now = monotonic()
        for key, value in list(self.items.items()):
            if now - value.touched >= self.ttl_seconds:
                value.clear()
                del self.items[key]
        key = (session, release, lesson)
        if key not in self.items and len(self.items) >= 1000:
            raise ValueError('Active lesson conversation capacity reached')
        value = self.items.setdefault(key, LessonConversation())
        value.touched = now
        return value

    def end(self, session: str) -> None:
        for key in list(self.items):
            if key[0] == session:
                self.items.pop(key).clear()
