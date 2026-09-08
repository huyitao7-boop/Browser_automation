import re
from pathlib import Path
from typing import Any, cast

from pydantic import TypeAdapter

from agent_browser_automation.browser.browser_api import BrowserAPI, ResourceLedgerBrowserAPI
from agent_browser_automation.shared.artifacts import ArtifactWriter
from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import AutomationException, automation_error
from agent_browser_automation.shared.redaction import DEFAULT_REDACTOR, Redactor
from agent_browser_automation.shared.run_context import RunContext

from .action import AssertAction, Assertion, WebAction
from .result import StepResult, WorkflowResult
from .types import WorkflowDefinition

ACTION_ADAPTER: TypeAdapter[Any] = TypeAdapter(WebAction)
INPUT_PATTERN = re.compile(r"\$\{input\.([A-Za-z_][A-Za-z0-9_]*)\}")


class WorkflowRunner:
    def __init__(
        self,
        browser: BrowserAPI,
        artifact_root: Path,
        redactor: Redactor = DEFAULT_REDACTOR,
    ) -> None:
        self.browser = browser
        self.artifact_root = artifact_root
        self.redactor = redactor

    async def run(
        self,
        workflow: WorkflowDefinition,
        inputs: dict[str, Any],
        context: RunContext,
    ) -> WorkflowResult:
        artifacts = ArtifactWriter(self.artifact_root, context, self.redactor)
        if isinstance(self.browser, ResourceLedgerBrowserAPI):
            self.browser.bind_resource_ledger(artifacts.write_resource_ledger)
        logger = artifacts.logger
        completed: list[str] = []
        revision = 0
        failed_step: str | None = None
        failure = None
        evidence_failed = False
        close_failed = False
        logger.write("run_started", "workflow.runner")
        try:
            logger.write("browser_starting", "workflow.runner")
            await self.browser.start()
            logger.write("browser_started", "workflow.runner")
            for step in workflow.steps:
                failed_step = step.id
                logger.write("step_started", "workflow.runner", step_id=step.id)
                if step.precondition is not None:
                    precondition = self._resolve_assertion(step.precondition, workflow, inputs)
                    pre_result = await self.browser.execute(
                        AssertAction(assertion=precondition, expected_revision=revision), context
                    )
                    artifacts.append_step(
                        StepResult(
                            step_id=step.id,
                            kind="precondition",
                            status=pre_result.status,
                            action=AssertAction(
                                assertion=precondition, expected_revision=revision
                            ).model_dump(mode="json", exclude_none=True),
                            result=pre_result,
                        )
                    )
                    if pre_result.status == "failed":
                        failure = pre_result.error
                        logger.write(
                            "step_failed",
                            "workflow.runner",
                            level="error",
                            step_id=step.id,
                            condition="precondition",
                            classification=(
                                failure.classification if failure is not None else None
                            ),
                        )
                        break

                action = self._resolve_action(step.action, workflow, inputs, revision)
                action_result = await self.browser.execute(action, context)
                artifacts.append_step(
                    StepResult(
                        step_id=step.id,
                        status=action_result.status,
                        action=action.model_dump(mode="json", by_alias=True, exclude_none=True),
                        result=action_result,
                    )
                )
                revision = action_result.revision_after
                if action_result.status == "failed":
                    failure = action_result.error
                    logger.write(
                        "step_failed",
                        "workflow.runner",
                        level="error",
                        step_id=step.id,
                        classification=(failure.classification if failure is not None else None),
                    )
                    break

                if step.postcondition is not None:
                    postcondition = self._resolve_assertion(step.postcondition, workflow, inputs)
                    post_result = await self.browser.execute(
                        AssertAction(assertion=postcondition, expected_revision=revision), context
                    )
                    artifacts.append_step(
                        StepResult(
                            step_id=step.id,
                            kind="postcondition",
                            status=post_result.status,
                            action=AssertAction(
                                assertion=postcondition, expected_revision=revision
                            ).model_dump(mode="json", exclude_none=True),
                            result=post_result,
                        )
                    )
                    revision = post_result.revision_after
                    if post_result.status == "failed":
                        failure = post_result.error
                        logger.write(
                            "step_failed",
                            "workflow.runner",
                            level="error",
                            step_id=step.id,
                            condition="postcondition",
                            classification=(
                                failure.classification if failure is not None else None
                            ),
                        )
                        break
                completed.append(step.id)
                logger.write("step_completed", "workflow.runner", step_id=step.id)
                failed_step = None

            if failure is not None:
                screenshot_path = (
                    artifacts.screenshot_directory / f"{failed_step or 'failure'}.png"
                )
                try:
                    await self.browser.screenshot(screenshot_path)
                    artifacts.register_screenshot(screenshot_path, failed_step)
                    logger.write(
                        "failure_screenshot_written",
                        "workflow.runner",
                        step_id=failed_step,
                        path=screenshot_path.relative_to(artifacts.directory).as_posix(),
                    )
                except Exception as exc:
                    logger.write(
                        "failure_screenshot_failed",
                        "workflow.runner",
                        level="warning",
                        step_id=failed_step,
                        cause=str(exc),
                    )
                artifacts.write_failure(failure)
                result = WorkflowResult(
                    status="failed",
                    workflow_id=workflow.id,
                    run_id=context.run_id,
                    completed_steps=completed,
                    failed_step=failed_step,
                    error=failure,
                    artifact_directory=str(artifacts.directory),
                    evidence_manifest=str(artifacts.manifest_path),
                )
            else:
                observation = await self.browser.observe()
                result = WorkflowResult(
                    status="passed",
                    workflow_id=workflow.id,
                    run_id=context.run_id,
                    completed_steps=completed,
                    data={"observation": observation.model_dump(mode="json")},
                    artifact_directory=str(artifacts.directory),
                    evidence_manifest=str(artifacts.manifest_path),
                )
        except AutomationException as exc:
            logger.write(
                "run_exception",
                "workflow.runner",
                level="error",
                step_id=failed_step,
                classification=exc.error.classification,
                message=exc.error.message,
                details=exc.error.details,
            )
            artifacts.write_failure(exc.error)
            result = WorkflowResult(
                status="failed",
                workflow_id=workflow.id,
                run_id=context.run_id,
                completed_steps=completed,
                failed_step=failed_step,
                error=exc.error,
                artifact_directory=str(artifacts.directory),
                evidence_manifest=str(artifacts.manifest_path),
            )
        except Exception as exc:
            error = automation_error(
                ErrorClassification.INTERNAL_ERROR,
                "workflow execution failed unexpectedly",
                cause=str(exc)[:2_000],
            ).error
            logger.write(
                "run_exception",
                "workflow.runner",
                level="error",
                step_id=failed_step,
                classification=error.classification,
                cause=str(exc),
            )
            artifacts.write_failure(error)
            result = WorkflowResult(
                status="failed",
                workflow_id=workflow.id,
                run_id=context.run_id,
                completed_steps=completed,
                failed_step=failed_step,
                error=error,
                artifact_directory=str(artifacts.directory),
                evidence_manifest=str(artifacts.manifest_path),
            )
        finally:
            try:
                evidence = await self.browser.evidence()
                artifacts.write_browser_evidence(evidence)
                logger.write("browser_evidence_written", "workflow.runner")
            except Exception as exc:
                evidence_failed = True
                logger.write(
                    "browser_evidence_failed",
                    "workflow.runner",
                    level="warning",
                    cause=str(exc),
                )
            try:
                logger.write("browser_closing", "workflow.runner")
                await self.browser.close()
                logger.write("browser_closed", "workflow.runner")
            except Exception as exc:
                close_failed = True
                logger.write(
                    "browser_close_failed",
                    "workflow.runner",
                    level="warning",
                    cause=str(exc),
                )

        if (evidence_failed or close_failed) and result.status == "passed":
            error = automation_error(
                ErrorClassification.BROWSER_ERROR,
                "workflow passed but browser finalization failed",
                evidence_failed=evidence_failed,
                cleanup_failed=close_failed,
            ).error
            result = result.model_copy(update={"status": "needs-review", "error": error})
        logger.write(
            "run_completed",
            "workflow.runner",
            level="info" if result.status == "passed" else "error",
            status=result.status,
            completed_steps=result.completed_steps,
            failed_step=result.failed_step,
        )
        artifacts.write_result(result)
        artifacts.finalize(result.status)
        self.redactor.clear_sensitive_values()
        return result

    def _resolve_action(
        self,
        action: WebAction,
        workflow: WorkflowDefinition,
        inputs: dict[str, Any],
        revision: int,
    ) -> WebAction:
        raw = action.model_dump(mode="python", by_alias=True, exclude_none=True)
        raw = self._substitute(raw, inputs)
        if isinstance(action, AssertAction):
            raw["assertion"] = self._resolve_assertion(
                action.assertion, workflow, inputs
            ).model_dump(mode="python", by_alias=True, exclude_none=True)
        target_ref = raw.pop("targetRef", None)
        if target_ref is not None:
            target = workflow.targets.get(str(target_ref))
            if target is None:
                raise automation_error(
                    ErrorClassification.WORKFLOW_INVALID,
                    "workflow action contains an unknown targetRef",
                    target_ref=target_ref,
                )
            raw["target"] = target.model_dump(mode="python")
        raw["expected_revision"] = revision
        return cast(WebAction, ACTION_ADAPTER.validate_python(raw))

    def _resolve_assertion(
        self,
        assertion: Assertion,
        workflow: WorkflowDefinition,
        inputs: dict[str, Any],
    ) -> Assertion:
        raw = assertion.model_dump(mode="python", by_alias=True, exclude_none=True)
        raw = self._substitute(raw, inputs)
        target_ref = raw.pop("targetRef", None)
        if target_ref is not None:
            target = workflow.targets.get(str(target_ref))
            if target is None:
                raise automation_error(
                    ErrorClassification.WORKFLOW_INVALID,
                    "workflow assertion contains an unknown targetRef",
                    target_ref=target_ref,
                )
            raw["target"] = target.model_dump(mode="python")
        return Assertion.model_validate(raw)

    def _substitute(self, value: Any, inputs: dict[str, Any]) -> Any:
        if isinstance(value, dict):
            mapping = cast(dict[str, Any], value)
            return {key: self._substitute(item, inputs) for key, item in mapping.items()}
        if isinstance(value, list):
            sequence = cast(list[Any], value)
            return [self._substitute(item, inputs) for item in sequence]
        if isinstance(value, tuple):
            sequence_tuple = cast(tuple[Any, ...], value)
            return [self._substitute(item, inputs) for item in sequence_tuple]
        if not isinstance(value, str):
            return value
        exact = INPUT_PATTERN.fullmatch(value)
        if exact:
            name = exact.group(1)
            if name not in inputs:
                raise automation_error(
                    ErrorClassification.VALIDATION_ERROR,
                    "workflow references a missing input",
                    input=name,
                )
            return inputs[name]

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in inputs:
                raise automation_error(
                    ErrorClassification.VALIDATION_ERROR,
                    "workflow references a missing input",
                    input=name,
                )
            replacement = inputs[name]
            if not isinstance(replacement, (str, int, float, bool)):
                raise automation_error(
                    ErrorClassification.VALIDATION_ERROR,
                    "embedded workflow input must be scalar",
                    input=name,
                )
            return str(replacement)

        return INPUT_PATTERN.sub(replace, value)
