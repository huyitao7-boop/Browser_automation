from agent_browser_automation.workflow.matcher import WorkflowMatcher

from .models import CompiledTestPlan, TestCase


class TestCaseCompiler:
    def __init__(self, matcher: WorkflowMatcher) -> None:
        self.matcher = matcher

    def compile(self, testcase: TestCase) -> CompiledTestPlan:
        workflow = self.matcher.match(
            workflow_id=testcase.workflow_id,
            capability=testcase.capability,
            inputs=testcase.inputs,
        )
        return CompiledTestPlan(
            testcase_id=testcase.id,
            workflow_id=workflow.id,
            capability=workflow.capability,
            inputs=testcase.inputs,
            expected_status=testcase.expected_status,
        )
