import hashlib
import json

from agent_browser_automation.browser.cleanup import RESOURCE_PLACEHOLDER, CleanupPlan
from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error
from agent_browser_automation.workflow.action import LocatorStrategy, TargetSpec
from agent_browser_automation.workflow.trajectory_recorder import TrajectoryEvent

from .models import CleanupDraft


class CleanupPlanGenerator:
    def generate(
        self,
        *,
        environment_id: str,
        plan_id: str,
        capability: str,
        resource_kind: str,
        resource_name: str,
        resource_url: str,
        identity_target: TargetSpec,
        delete_target: TargetSpec,
        confirmation_target: TargetSpec | None,
        confirmation_submit_target: TargetSpec | None,
        absence_url: str,
        absence_target: TargetSpec,
        events: tuple[TrajectoryEvent, ...],
    ) -> CleanupDraft:
        if not events:
            raise automation_error(
                ErrorClassification.VALIDATION_ERROR,
                "cleanup generation requires a recorded browser trajectory",
            )
        if resource_name not in resource_url:
            raise automation_error(
                ErrorClassification.VALIDATION_ERROR,
                "observed resource URL does not contain the exact resource name",
            )
        trajectory_hash = hashlib.sha256(
            json.dumps(
                [item.model_dump(mode="json") for item in events],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        plan = CleanupPlan(
            id=plan_id,
            capability=capability,
            resource_kind=resource_kind,
            resource_url_template=resource_url.replace(
                resource_name, "{resource_name}"
            ),
            identity_target=self._generalize(identity_target, resource_name),
            delete_target=delete_target,
            confirmation_target=confirmation_target,
            confirmation_submit_target=confirmation_submit_target,
            absence_url=absence_url,
            absence_target=self._generalize(absence_target, resource_name),
        )
        return CleanupDraft(
            environment_id=environment_id,
            plan=plan,
            trajectory_hash=trajectory_hash,
        )

    @staticmethod
    def _generalize(target: TargetSpec, resource_name: str) -> TargetSpec:
        found = False
        strategies: list[LocatorStrategy] = []
        for item in target.strategies:
            name = item.name
            value = item.value
            if name == resource_name:
                name = RESOURCE_PLACEHOLDER
                found = True
            if value == resource_name:
                value = RESOURCE_PLACEHOLDER
                found = True
            strategies.append(item.model_copy(update={"name": name, "value": value}))
        if not found:
            raise automation_error(
                ErrorClassification.VALIDATION_ERROR,
                "identity target does not contain the exact observed resource name",
            )
        return target.model_copy(update={"strategies": tuple(strategies)})
