import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from agent_browser_automation.browser.cleanup import CleanupPlan
from agent_browser_automation.environment.models import EnvironmentDefinition
from agent_browser_automation.environment.store import EnvironmentStore
from agent_browser_automation.evidence.store import EvidenceStore
from agent_browser_automation.publishing.models import ReviewRecord
from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import AutomationException, automation_error
from agent_browser_automation.shared.resource_ledger import ResourceRecord, ResourceState
from agent_browser_automation.shared.run_context import normalized_origin
from agent_browser_automation.shared.tracing import create_run_id
from agent_browser_automation.workflow.action import TargetSpec
from agent_browser_automation.workflow.result import ActionResult

from .models import CleanupDraft, CleanupDraftState, CleanupVerificationRecord
from .store import CleanupDraftStore, cleanup_draft_hash

CleanupRunner = Callable[
    [str, str, str, CleanupPlan, ResourceRecord, str], Awaitable[ActionResult]
]


class CleanupPublishService:
    def __init__(
        self,
        store: CleanupDraftStore,
        environments: EnvironmentStore,
        evidence: EvidenceStore,
        cleanup_runner: CleanupRunner,
    ) -> None:
        self.store = store
        self.environments = environments
        self.evidence = evidence
        self.cleanup_runner = cleanup_runner

    def create(
        self,
        draft: CleanupDraft,
        creator_id: str,
        *,
        trajectory_bound: bool = False,
    ) -> CleanupDraftState:
        return self.store.save(
            draft, creator_id, trajectory_bound=trajectory_bound
        )

    def validate(self, environment_id: str, plan_id: str) -> CleanupDraftState:
        draft, state = self._current(environment_id, plan_id)
        if not state.trajectory_bound:
            raise self._policy(
                "cleanup draft was not generated from a live recorded session"
            )
        environment = self.environments.get(environment_id)
        self._validate_draft(draft, environment)
        state = state.model_copy(update={"validated": True})
        self.store.save_state(environment_id, plan_id, state)
        return state

    async def verify(
        self,
        environment_id: str,
        plan_id: str,
        cycles: tuple[tuple[str, str, str], ...],
        request_id: str,
        trace_id: str,
    ) -> CleanupDraftState:
        draft, state = self._current(environment_id, plan_id)
        if not state.validated:
            raise self._policy("cleanup draft must be validated before verification")
        if len(cycles) != 2:
            raise self._policy("cleanup verification requires exactly two test resources")
        environment = self.environments.get(environment_id)
        records: list[tuple[str, str, str, ResourceRecord]] = []
        for source_run_id, idempotency_key, session_id in cycles:
            artifact = self.evidence.resource_ledger(source_run_id)
            matches = [
                item
                for item in artifact.records
                if item.idempotency_key == idempotency_key
            ]
            if len(matches) != 1:
                raise self._policy("verification resource ledger record was not found")
            record = matches[0]
            self._validate_record(draft, record, environment)
            records.append((source_run_id, idempotency_key, session_id, record))
        if len({item[3].resource_name for item in records}) != 2:
            raise self._policy("verification resources must have distinct names")

        cleanup_run_ids: list[str] = []
        for index, (source_run_id, idempotency_key, session_id, record) in enumerate(
            records
        ):
            cleanup_run_id = create_run_id()
            attempts = record.cleanup_attempts + 1
            self.evidence.transition_resource(
                source_run_id,
                idempotency_key,
                ResourceState.CLEANUP_RUNNING,
                cleanup_attempts=attempts,
                cleanup_run_id=cleanup_run_id,
            )
            try:
                result = await self.cleanup_runner(
                    session_id,
                    f"{request_id}-cycle-{index + 1}"[:128],
                    trace_id,
                    draft.plan,
                    record,
                    cleanup_run_id,
                )
            except AutomationException as exc:
                self.evidence.transition_resource(
                    source_run_id,
                    idempotency_key,
                    ResourceState.CLEANUP_FAILED,
                    cleanup_attempts=attempts,
                    cleanup_run_id=cleanup_run_id,
                    cleanup_error=exc.error.classification.value,
                )
                raise
            if result.status != "passed" or result.observed_value == "cleanup-not-needed":
                classification = (
                    result.error.classification.value
                    if result.error is not None
                    else ErrorClassification.ASSERTION_FAILED.value
                )
                self.evidence.transition_resource(
                    source_run_id,
                    idempotency_key,
                    ResourceState.CLEANUP_FAILED,
                    cleanup_attempts=attempts,
                    cleanup_run_id=cleanup_run_id,
                    cleanup_error=classification,
                )
                raise self._policy(
                    "cleanup draft failed a bound test-resource verification cycle",
                    failed_source_run_id=source_run_id,
                    cleanup_run_id=cleanup_run_id,
                )
            self.evidence.transition_resource(
                source_run_id,
                idempotency_key,
                ResourceState.CLEANED,
                cleanup_attempts=attempts,
                cleanup_run_id=cleanup_run_id,
            )
            cleanup_run_ids.append(cleanup_run_id)

        verification = CleanupVerificationRecord(
            draft_hash=state.draft_hash,
            passed_runs=2,
            source_run_ids=tuple(item[0] for item in records),
            cleanup_run_ids=tuple(cleanup_run_ids),
            resource_names=tuple(item[3].resource_name for item in records),
        )
        state = state.model_copy(update={"verification": verification})
        self.store.save_state(environment_id, plan_id, state)
        return state

    def review(
        self,
        environment_id: str,
        plan_id: str,
        draft_hash: str,
        reviewer_id: str,
        approved: bool,
        summary: str,
    ) -> CleanupDraftState:
        _, state = self._current(environment_id, plan_id)
        if draft_hash != state.draft_hash:
            raise self._policy("review hash does not match the cleanup draft")
        if reviewer_id == state.creator_id:
            raise self._policy("cleanup draft creator cannot perform the review")
        state = state.model_copy(
            update={
                "review": ReviewRecord(
                    draft_hash=draft_hash,
                    reviewer_id=reviewer_id,
                    approved=approved,
                    summary=summary,
                )
            }
        )
        self.store.save_state(environment_id, plan_id, state)
        return state

    def publish(self, environment_id: str, plan_id: str) -> tuple[CleanupDraftState, Path]:
        draft, state = self._current(environment_id, plan_id)
        self._enforce_gate(state)
        environment = self.environments.get(environment_id)
        self._validate_draft(draft, environment)
        updated = environment.model_copy(
            update={"cleanup_plans": (*environment.cleanup_plans, draft.plan)}
        )
        path = self._environment_path(environment_id)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                updated.model_dump(mode="json", exclude_none=True),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        EnvironmentDefinition.model_validate_json(temporary.read_text(encoding="utf-8"))
        temporary.replace(path)
        return state, path

    def validated_plan(
        self, environment_id: str, plan_id: str, capability: str, resource_kind: str
    ) -> CleanupPlan:
        draft, state = self._current(environment_id, plan_id)
        if not state.validated:
            raise self._policy("cleanup draft has not passed deterministic validation")
        if (
            draft.plan.capability != capability
            or draft.plan.resource_kind != resource_kind
        ):
            raise self._policy("cleanup draft does not match the requested test resource")
        return draft.plan

    def _current(
        self, environment_id: str, plan_id: str
    ) -> tuple[CleanupDraft, CleanupDraftState]:
        draft = self.store.load(environment_id, plan_id)
        state = self.store.load_state(environment_id, plan_id)
        if cleanup_draft_hash(draft) != state.draft_hash:
            raise self._policy("cleanup draft changed after reports were created")
        return draft, state

    @classmethod
    def _validate_draft(
        cls, draft: CleanupDraft, environment: EnvironmentDefinition
    ) -> None:
        if draft.environment_id != environment.id:
            raise cls._policy("cleanup draft environment does not match")
        if draft.trajectory_hash is None:
            raise cls._policy("cleanup draft is not bound to an exploration trajectory")
        policy = environment.write_policy
        authentication = environment.authentication
        if policy is None or authentication is None:
            raise cls._policy("cleanup learning requires write and authentication policies")
        if draft.plan.capability not in policy.allowed_capabilities:
            raise cls._policy("cleanup capability is outside the write policy")
        origins = {
            normalized_origin(draft.plan.resource_url_template),
            normalized_origin(draft.plan.absence_url),
        }
        if not origins.issubset(set(authentication.allowed_origins)):
            raise cls._policy("cleanup draft URL is outside authenticated origins")
        cls._semantic(draft.plan.identity_target)
        cls._semantic(draft.plan.delete_target)
        cls._semantic(draft.plan.absence_target)
        if draft.plan.delete_target.expected_control != "button":
            raise cls._policy("cleanup delete target must be an explicit button")
        if draft.plan.confirmation_target is not None:
            cls._semantic(draft.plan.confirmation_target)
        if draft.plan.confirmation_submit_target is not None:
            cls._semantic(draft.plan.confirmation_submit_target)
            if draft.plan.confirmation_submit_target.expected_control != "button":
                raise cls._policy("cleanup confirmation submit must be a button")
        duplicate = [
            item
            for item in environment.cleanup_plans
            if item.capability == draft.plan.capability
            and item.resource_kind == draft.plan.resource_kind
        ]
        if duplicate:
            raise cls._policy("environment already has this cleanup capability")

    @staticmethod
    def _semantic(target: TargetSpec) -> None:
        if any(item.kind in {"css", "text"} for item in target.strategies):
            raise CleanupPublishService._policy(
                "cleanup plans require semantic target strategies"
            )

    @staticmethod
    def _validate_record(
        draft: CleanupDraft,
        record: ResourceRecord,
        environment: EnvironmentDefinition,
    ) -> None:
        if (
            record.environment_id != draft.environment_id
            or record.capability != draft.plan.capability
            or record.resource_kind != draft.plan.resource_kind
            or record.operation != "create"
        ):
            raise CleanupPublishService._policy(
                "verification resource does not match the cleanup draft"
            )
        policy = environment.write_policy
        if (
            policy is None
            or record.policy_id != policy.id
            or not any(
                record.resource_name.startswith(prefix)
                for prefix in policy.resource_name_prefixes
            )
        ):
            raise CleanupPublishService._policy(
                "verification resource is outside the environment write policy"
            )
        if record.state not in {
            ResourceState.VERIFIED,
            ResourceState.NEEDS_CLEANUP,
            ResourceState.CLEANUP_FAILED,
        }:
            raise CleanupPublishService._policy(
                "verification resource state cannot enter cleanup"
            )

    @staticmethod
    def _enforce_gate(state: CleanupDraftState) -> None:
        if not state.validated:
            raise CleanupPublishService._policy("cleanup draft was not validated")
        verification = state.verification
        if (
            verification is None
            or verification.draft_hash != state.draft_hash
            or verification.passed_runs != 2
            or len(set(verification.resource_names)) != 2
        ):
            raise CleanupPublishService._policy(
                "cleanup draft needs two distinct hash-bound cleanup cycles"
            )
        review = state.review
        if (
            review is None
            or review.draft_hash != state.draft_hash
            or not review.approved
            or review.reviewer_id == state.creator_id
        ):
            raise CleanupPublishService._policy(
                "cleanup draft needs an approved independent review"
            )

    def _environment_path(self, environment_id: str) -> Path:
        matches: list[Path] = []
        for path in self.environments.root.glob("*.json"):
            try:
                value = EnvironmentDefinition.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            if value.id == environment_id:
                matches.append(path)
        if len(matches) != 1:
            raise self._policy("environment file is not uniquely resolvable")
        return matches[0]

    @staticmethod
    def _policy(message: str, **details: object):
        return automation_error(ErrorClassification.POLICY_DENIED, message, **details)
