import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .redaction import DEFAULT_REDACTOR, Redactor
from .run_context import RunContext

LogLevel = Literal["debug", "info", "warning", "error"]


class StructuredLogWriter:
    def __init__(
        self,
        path: Path,
        context: RunContext,
        redactor: Redactor = DEFAULT_REDACTOR,
    ) -> None:
        self.path = path
        self.context = context
        self.redactor = redactor
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def write(
        self,
        event: str,
        component: str,
        *,
        level: LogLevel = "info",
        step_id: str | None = None,
        **details: Any,
    ) -> None:
        record: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level,
            "trace_id": self.context.trace_id,
            "request_id": self.context.request_id,
            "run_id": self.context.run_id,
            "workflow_id": self.context.workflow_id,
            "component": component,
            "event": event,
        }
        if step_id is not None:
            record["step_id"] = step_id
        record.update(details)
        safe_record = self.redactor.redact(record)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(safe_record, ensure_ascii=False, default=str) + "\n")
