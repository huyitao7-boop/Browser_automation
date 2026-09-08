import json
import re
from collections.abc import Callable
from pathlib import Path

from agent_browser_automation.browser.browser_api import BrowserAPI
from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error
from agent_browser_automation.shared.run_context import RiskLevel, RunContext
from agent_browser_automation.shared.tracing import create_run_id
from agent_browser_automation.workflow.runner import WorkflowRunner
from agent_browser_automation.workflow.types import WorkflowDefinition
from agent_browser_automation.workflow.validator import (
    validate_readonly_workflow,
    validate_workflow,
)

from .models import DraftState, ReviewRecord, VerificationRecord
from .store import DraftStore, workflow_hash


class PublishService:
    """The only component allowed to atomically transition a draft to published."""

    def __init__(
        self,
        store: DraftStore,
        browser_factory: Callable[[], BrowserAPI],
        artifact_root: Path,
        action_timeout_ms: int,
        navigation_timeout_ms: int,
    ) -> None:
        self.store = store
        self.browser_factory = browser_factory
        self.artifact_root = Path(artifact_root)
        self.action_timeout_ms = action_timeout_ms
        self.navigation_timeout_ms = navigation_timeout_ms

    def create(self, workflow: WorkflowDefinition, creator_id: str) -> DraftState:
        validate_readonly_workflow(workflow, allowed_statuses={"draft"})
        return self.store.save(workflow, creator_id)

    def validate(self, workflow_id: str) -> DraftState:
        draft, state = self._current(workflow_id)
        validate_readonly_workflow(draft, allowed_statuses={"draft"})
        state = state.model_copy(update={"validated": True})
        self.store.save_state(workflow_id, state)
        return state

    async def verify(self, workflow_id: str, inputs: dict[str, object]) -> DraftState:
        draft, state = self._current(workflow_id)
        if not state.validated:
            raise self._policy("draft must be validated before verification")
        run_ids: list[str] = []
        for index in range(2):
            result = await self._run(
                draft,
                inputs,
                request_id=f"verify-{workflow_id}-{index + 1}",
            )
            if result.status != "passed":
                raise self._policy(
                    "clean-session verification failed",
                    failed_run_id=result.run_id,
                    failed_status=result.status,
                )
            run_ids.append(result.run_id)
        verification = VerificationRecord(
            draft_hash=state.draft_hash,
            passed_runs=2,
            inputs=inputs,
            run_ids=tuple(run_ids),
        )
        state = state.model_copy(update={"verification": verification})
        self.store.save_state(workflow_id, state)
        return state

    def review(
        self,
        workflow_id: str,
        draft_hash: str,
        reviewer_id: str,
        approved: bool,
        summary: str,
    ) -> DraftState:
        _, state = self._current(workflow_id)
        if draft_hash != state.draft_hash:
            raise self._policy("review hash does not match the current draft")
        if reviewer_id == state.creator_id:
            raise self._policy("draft creator cannot perform the independent review")
        review = ReviewRecord(
            draft_hash=draft_hash,
            reviewer_id=reviewer_id,
            approved=approved,
            summary=summary,
        )
        state = state.model_copy(update={"review": review})
        self.store.save_state(workflow_id, state)
        return state

    async def publish(self, workflow_id: str) -> tuple[DraftState, str, str]:
        draft, state = self._current(workflow_id)
        self._enforce_gate(state)
        assert state.verification is not None
        published = draft.model_copy(update={"status": "published"})
        validate_workflow(published)
        path = self._published_path(workflow_id)
        previous = path.read_bytes() if path.is_file() else None
        self._atomic_write(path, published)
        smoke = await self._run(
            published,
            state.verification.inputs,
            request_id=f"publish-smoke-{workflow_id}",
        )
        if smoke.status != "passed":
            isolated = self._isolate(draft)
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                temporary = path.with_suffix(path.suffix + ".rollback")
                temporary.write_bytes(previous)
                temporary.replace(path)
            raise self._policy(
                "published smoke test failed; draft was isolated and prior version restored",
                smoke_run_id=smoke.run_id,
                isolated_path=str(isolated),
            )
        return state, str(path), smoke.run_id

    def _current(self, workflow_id: str) -> tuple[WorkflowDefinition, DraftState]:
        draft = self.store.load(workflow_id)
        state = self.store.load_state(workflow_id)
        if workflow_hash(draft) != state.draft_hash:
            raise self._policy("draft content changed after reports were created")
        return draft, state

    @staticmethod
    def _enforce_gate(state: DraftState) -> None:
        if not state.validated:
            raise PublishService._policy("draft has no successful validation report")
        verification = state.verification
        if (
            verification is None
            or verification.draft_hash != state.draft_hash
            or verification.passed_runs < 2
        ):
            raise PublishService._policy("draft needs two hash-bound clean-session replays")
        review = state.review
        if (
            review is None
            or review.draft_hash != state.draft_hash
            or not review.approved
            or review.reviewer_id == state.creator_id
        ):
            raise PublishService._policy("draft needs an approved independent review")

    async def _run(
        self,
        workflow: WorkflowDefinition,
        inputs: dict[str, object],
        request_id: str,
    ):
        from agent_browser_automation.shared.tracing import create_trace_id

        run_id = create_run_id()
        context = RunContext(
            trace_id=create_trace_id(),
            request_id=request_id[:128],
            run_id=run_id,
            workflow_id=workflow.id,
            allowed_origins=workflow.allowed_origins,
            max_risk=RiskLevel.READONLY,
            action_timeout_ms=self.action_timeout_ms,
            navigation_timeout_ms=self.navigation_timeout_ms,
        )
        browser = self.browser_factory()
        return await WorkflowRunner(
            browser, self.artifact_root, browser.redactor
        ).run(workflow, inputs, context)

    def _published_path(self, workflow_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,256}", workflow_id):
            raise self._policy("workflow id is unsafe for publication")
        directory = self.store.root / "published"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{workflow_id}.json"

    def _isolate(self, draft: WorkflowDefinition) -> Path:
        directory = self.store.root / "disabled"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{draft.id}-{workflow_hash(draft)[:12]}.json"
        disabled = draft.model_copy(update={"status": "disabled"})
        self._atomic_write(path, disabled)
        return path

    @staticmethod
    def _atomic_write(path: Path, workflow: WorkflowDefinition) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                workflow.model_dump(mode="json", by_alias=True, exclude_none=True),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _policy(message: str, **details: object):
        return automation_error(ErrorClassification.POLICY_DENIED, message, **details)
