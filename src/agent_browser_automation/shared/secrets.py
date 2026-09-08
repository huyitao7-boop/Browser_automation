from collections.abc import Mapping
from os import environ
from threading import RLock
from typing import Protocol

from pydantic import ConfigDict, Field, RootModel

from .classifications import ErrorClassification
from .errors import automation_error
from .redaction import Redactor


class SecretRef(RootModel[str]):
    model_config = ConfigDict(frozen=True)

    root: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,255}$")

    @property
    def value(self) -> str:
        return self.root


class SecretValue:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("secret value must not be empty")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretValue([REDACTED])"

    def __str__(self) -> str:
        return "[REDACTED]"


class SecretProvider(Protocol):
    async def resolve(self, reference: SecretRef) -> SecretValue: ...


class UnavailableSecretProvider:
    async def resolve(self, reference: SecretRef) -> SecretValue:
        raise automation_error(
            ErrorClassification.SECRET_PROVIDER_UNAVAILABLE,
            "no secret provider is configured",
            secret_ref=reference.value,
        )


class InMemorySecretProvider:
    """Test-only provider. Production composition must use a dedicated provider."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    async def resolve(self, reference: SecretRef) -> SecretValue:
        value = self._values.get(reference.value)
        if value is None:
            raise automation_error(
                ErrorClassification.SECRET_NOT_FOUND,
                "secret reference was not found",
                secret_ref=reference.value,
            )
        return SecretValue(value)


class EnvironmentVariableSecretProvider:
    """Allowlisted production adapter; secret values are read only on resolve()."""

    def __init__(
        self,
        bindings: Mapping[str, str],
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._bindings = dict(bindings)
        self._environment = environment if environment is not None else environ

    async def resolve(self, reference: SecretRef) -> SecretValue:
        variable = self._bindings.get(reference.value)
        if variable is None:
            raise automation_error(
                ErrorClassification.SECRET_ACCESS_DENIED,
                "secret reference is not allowlisted for this environment",
                secret_ref=reference.value,
            )
        value = self._environment.get(variable)
        if value is None:
            raise automation_error(
                ErrorClassification.SECRET_NOT_FOUND,
                "configured secret environment variable is unavailable",
                secret_ref=reference.value,
                environment_variable=variable,
            )
        return SecretValue(value)


class TransientSecretProvider:
    """Session-scoped, single-attempt overlay in front of a trusted fallback."""

    def __init__(
        self,
        fallback: SecretProvider,
        allowed_references: tuple[str, ...],
        replace_references: tuple[str, ...] | None = None,
    ) -> None:
        self._fallback = fallback
        self._allowed = frozenset(allowed_references)
        self._replace_allowed = frozenset(
            replace_references if replace_references is not None else allowed_references
        )
        if not self._replace_allowed.issubset(self._allowed):
            raise ValueError("replace references must be included in allowed references")
        self._values: dict[str, SecretValue] = {}
        self._lock = RLock()

    def replace(self, values: Mapping[str, str]) -> None:
        references = set(values)
        if references != set(self._replace_allowed):
            raise automation_error(
                ErrorClassification.SECRET_ACCESS_DENIED,
                "transient credentials must exactly match the environment allowlist",
            )
        prepared = {key: SecretValue(value) for key, value in values.items()}
        with self._lock:
            self._values = prepared

    async def resolve(self, reference: SecretRef) -> SecretValue:
        if reference.value not in self._allowed:
            raise automation_error(
                ErrorClassification.SECRET_ACCESS_DENIED,
                "secret reference is not allowlisted for this browser session",
                secret_ref=reference.value,
            )
        with self._lock:
            value = self._values.get(reference.value)
        if value is not None:
            return value
        return await self._fallback.resolve(reference)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


class SecretResolver:
    """Resolves only inside Browser Runtime and registers values for session redaction."""

    def __init__(self, provider: SecretProvider, redactor: Redactor) -> None:
        self.provider = provider
        self.redactor = redactor

    async def resolve(self, reference: SecretRef) -> SecretValue:
        value = await self.provider.resolve(reference)
        try:
            self.redactor.register_sensitive_value(value.reveal())
        except ValueError as exc:
            raise automation_error(
                ErrorClassification.SECRET_ACCESS_DENIED,
                "secret value cannot be safely registered for redaction",
                secret_ref=reference.value,
            ) from exc
        return value
