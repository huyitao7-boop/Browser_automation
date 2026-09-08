from typing import Any, cast

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error

from .action import READONLY_ACTION_TYPES
from .types import WorkflowDefinition


def validate_workflow(workflow: WorkflowDefinition) -> None:
    validate_readonly_workflow(workflow, allowed_statuses={"published"})


def validate_readonly_workflow(
    workflow: WorkflowDefinition, *, allowed_statuses: set[str]
) -> None:
    if workflow.status not in allowed_statuses:
        raise automation_error(
            ErrorClassification.WORKFLOW_INVALID,
            "workflow status is not allowed for this operation",
            workflow_id=workflow.id,
            status=workflow.status,
        )
    if workflow.risk.value != "readonly":
        raise automation_error(
            ErrorClassification.UNSAFE_ACTION,
            "the minimal runner only accepts readonly workflows",
            workflow_id=workflow.id,
            risk=workflow.risk.value,
        )
    for step in workflow.steps:
        if step.action.type not in READONLY_ACTION_TYPES:
            raise automation_error(
                ErrorClassification.UNSAFE_ACTION,
                "readonly workflow contains a mutating action",
                workflow_id=workflow.id,
                step_id=step.id,
                action_type=step.action.type,
            )
        _validate_target_refs(workflow, step.action.model_dump(by_alias=True, exclude_none=True))
        for assertion in (step.precondition, step.postcondition):
            if assertion is not None:
                _validate_target_refs(
                    workflow, assertion.model_dump(by_alias=True, exclude_none=True)
                )


def validate_inputs(schema: dict[str, object], inputs: dict[str, Any]) -> None:
    if schema.get("type") not in {None, "object"}:
        raise automation_error(
            ErrorClassification.WORKFLOW_INVALID,
            "minimal input schema must describe an object",
        )
    required_value = schema.get("required", [])
    if not isinstance(required_value, list):
        raise automation_error(
            ErrorClassification.WORKFLOW_INVALID,
            "input schema required must be a list of strings",
        )
    untyped_required = cast(list[Any], required_value)
    if not all(isinstance(item, str) for item in untyped_required):
        raise automation_error(
            ErrorClassification.WORKFLOW_INVALID,
            "input schema required must be a list of strings",
        )
    required = cast(list[str], untyped_required)
    missing = [name for name in required if name not in inputs]
    if missing:
        raise automation_error(
            ErrorClassification.VALIDATION_ERROR,
            "workflow inputs are missing required values",
            missing=missing,
        )
    properties_value = schema.get("properties", {})
    if not isinstance(properties_value, dict):
        raise automation_error(
            ErrorClassification.WORKFLOW_INVALID,
            "input schema properties must be an object",
        )
    properties = cast(dict[str, object], properties_value)
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(inputs) - set(properties))
        if unknown:
            raise automation_error(
                ErrorClassification.VALIDATION_ERROR,
                "workflow inputs contain unknown values",
                unknown=unknown,
            )
    expected_types = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    for name, value in inputs.items():
        definition_value = properties.get(name)
        if not isinstance(definition_value, dict):
            continue
        definition = cast(dict[str, object], definition_value)
        declared = definition.get("type")
        expected = expected_types.get(declared) if isinstance(declared, str) else None
        if expected is not None and (isinstance(value, bool) and declared in {"integer", "number"}):
            valid = False
        else:
            valid = expected is None or isinstance(value, expected)
        if not valid:
            raise automation_error(
                ErrorClassification.VALIDATION_ERROR,
                "workflow input has the wrong type",
                input=name,
                expected=declared,
            )


def _validate_target_refs(workflow: WorkflowDefinition, value: object) -> None:
    if isinstance(value, dict):
        mapping = cast(dict[str, object], value)
        target_ref = mapping.get("targetRef")
        if isinstance(target_ref, str) and target_ref not in workflow.targets:
            raise automation_error(
                ErrorClassification.WORKFLOW_INVALID,
                "workflow references an unknown target",
                workflow_id=workflow.id,
                target_ref=target_ref,
            )
        for nested in mapping.values():
            _validate_target_refs(workflow, nested)
    elif isinstance(value, (list, tuple)):
        sequence = cast(list[object] | tuple[object, ...], value)
        for nested in sequence:
            _validate_target_refs(workflow, nested)
