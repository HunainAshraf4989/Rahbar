"""Per-turn JSONL diagnostics. Payloads and exception messages are never logged."""
import json
import logging
import os
import traceback
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from rahbar.contracts.ports import TurnContext
from rahbar.engine.runtime import ProviderError


class PipelineLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # One handler per resolved file in this process, including multiple pipelines.
        self.logger = logging.getLogger('rahbar.pipeline.' + str(path.resolve()))
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        if not self.logger.handlers:
            handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(message)s'))
            self.logger.addHandler(handler)

    def turn(self, context: TurnContext, index_version: str, input_kind: str) -> 'TurnTrace':
        return TurnTrace(self.logger, context, index_version, input_kind)


class TurnTrace:
    def __init__(self, logger: logging.Logger, context: TurnContext,
                 index_version: str, input_kind: str):
        self.logger = logger
        self.identity = dict(trace_id=uuid4().hex, session_id=context.session_id,
            turn_id=context.turn_id, generation=context.generation,
            connection_epoch=context.connection_epoch, index_version=index_version,
            input_kind=input_kind, process_id=os.getpid())
        self.started = self.stage_started = perf_counter()
        self.current = 'request'
        self.write('request.started')

    def write(self, event: str, **fields: object) -> None:
        self.logger.info(json.dumps(dict(self.identity, event=event, stage=self.current,
            timestamp=datetime.now(timezone.utc).isoformat(),
            elapsed_ms=round((perf_counter()-self.started)*1000, 3), **fields)))

    def stage(self, name: str) -> None:
        if self.current != 'request':
            self.write('stage.completed', duration_ms=round((perf_counter()-self.stage_started)*1000, 3))
        self.current, self.stage_started = name, perf_counter()
        self.write('stage.started')

    def finish(self, outcome: str) -> None:
        self.write('stage.completed', duration_ms=round((perf_counter()-self.stage_started)*1000, 3))
        self.write('request.completed', outcome=outcome)

    def fail(self, error: BaseException, *, cancelled: bool = False) -> None:
        # Frames identify the failing operation without message, locals or source lines.
        frames = [dict(file=Path(f.filename).name, function=f.name, line=f.lineno)
                  for f in traceback.extract_tb(error.__traceback__)[-8:]]
        fields: dict[str, object] = dict(error_type=type(error).__name__, frames=frames)
        if isinstance(error, ProviderError):
            fields['error_code'] = error.failure.code
        self.write('request.cancelled' if cancelled else 'request.failed', **fields)
