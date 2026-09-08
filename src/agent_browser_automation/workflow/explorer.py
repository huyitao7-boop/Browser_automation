from pydantic import BaseModel, ConfigDict

from .action import Assertion
from .result import PageObservation


class ReadonlySuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: str
    reason: str
    assertion: Assertion | None = None


class ReadonlyExplorer:
    """Produces bounded readonly suggestions; the external Agent chooses among them."""

    def suggest(self, observation: PageObservation) -> tuple[ReadonlySuggestion, ...]:
        suggestions = [
            ReadonlySuggestion(kind="observe", reason="refresh the current page facts"),
            ReadonlySuggestion(
                kind="assert",
                reason="bind the workflow to the observed URL",
                assertion=Assertion(type="url_matches", value=observation.url),
            ),
        ]
        if observation.title:
            suggestions.append(
                ReadonlySuggestion(
                    kind="assert",
                    reason="verify the observed page title",
                    assertion=Assertion(type="title_contains", value=observation.title),
                )
            )
        return tuple(suggestions)
