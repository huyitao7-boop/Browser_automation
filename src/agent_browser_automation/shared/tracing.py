import re
from uuid import uuid4

TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def create_trace_id() -> str:
    return f"trace-{uuid4().hex}"


def create_run_id() -> str:
    return uuid4().hex


def ensure_trace_id(value: str | None) -> str:
    if value is None:
        return create_trace_id()
    if TRACE_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "trace_id must be 1-128 characters using letters, digits, dot, colon, "
            "underscore or hyphen"
        )
    return value
