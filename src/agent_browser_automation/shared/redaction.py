import re
from threading import RLock
from typing import Any, cast
from urllib.parse import quote, urlsplit, urlunsplit

SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set_cookie",
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "client_secret",
        "authorization_code",
        "session_id",
    }
)
SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)\b(authorization(?:[_-]?code)?|cookie|password|passwd|secret|token|"
    r"access[_-]?token|refresh[_-]?token|api[_-]?key|client[_-]?secret|session[_-]?id)\b"
    r"(\s*[:=]\s*)(?:Bearer\s+)?([^\s,;]+)"
)
BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


class SensitiveValueRegistry:
    """Keeps session-scoped values in memory so every evidence sink can redact them."""

    def __init__(self) -> None:
        self._values: set[str] = set()
        self._lock = RLock()

    def register(self, value: str) -> None:
        if len(value) < 4:
            raise ValueError("sensitive values shorter than four characters are unsafe")
        with self._lock:
            self._values.add(value)

    def variants(self) -> tuple[str, ...]:
        with self._lock:
            values = set(self._values)
        values.update(quote(value, safe="") for value in tuple(values))
        return tuple(sorted(values, key=len, reverse=True))

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


class Redactor:
    replacement = "[REDACTED]"

    def __init__(self, sensitive_values: SensitiveValueRegistry | None = None) -> None:
        self.sensitive_values = sensitive_values

    def register_sensitive_value(self, value: str) -> None:
        if self.sensitive_values is None:
            raise RuntimeError("redactor has no session-sensitive value registry")
        self.sensitive_values.register(value)

    def clear_sensitive_values(self) -> None:
        if self.sensitive_values is not None:
            self.sensitive_values.clear()

    def is_sensitive_key(self, key: str) -> bool:
        normalized = key.strip().lower().replace("-", "_").replace(" ", "_")
        return normalized in SENSITIVE_KEYS or any(
            normalized.endswith(f"_{item}") for item in SENSITIVE_KEYS
        )

    def sanitize_url(self, url: str) -> str:
        try:
            parsed = urlsplit(url)
            if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname is None:
                return "[REDACTED_URL]"
            host = parsed.hostname.lower()
            if ":" in host:
                host = f"[{host}]"
            port = f":{parsed.port}" if parsed.port is not None else ""
            return urlunsplit((parsed.scheme.lower(), f"{host}{port}", parsed.path, "", ""))
        except ValueError:
            return "[REDACTED_URL]"

    def redact_text(self, value: str, limit: int | None = None) -> str:
        without_url_secrets = URL_PATTERN.sub(
            lambda match: self.sanitize_url(match.group(0)), value
        )
        without_sensitive_values = SENSITIVE_VALUE_PATTERN.sub(
            rf"\1\2{self.replacement}", without_url_secrets
        )
        redacted = BEARER_PATTERN.sub(f"Bearer {self.replacement}", without_sensitive_values)
        if self.sensitive_values is not None:
            for sensitive in self.sensitive_values.variants():
                redacted = redacted.replace(sensitive, self.replacement)
        return redacted if limit is None else redacted[:limit]

    def redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            mapping = cast(dict[object, Any], value)
            return {
                str(key): self.replacement
                if self.is_sensitive_key(str(key))
                else self.redact(item)
                for key, item in mapping.items()
            }
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, (list, tuple)):
            sequence = cast(list[Any] | tuple[Any, ...], value)
            return [self.redact(item) for item in sequence]
        return value


DEFAULT_REDACTOR = Redactor()
