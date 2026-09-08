from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from enum import StrEnum

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings as FastMCPSettings

from agent_browser_automation.browser.playwright_browser import PlaywrightBrowser
from agent_browser_automation.browser.session import BrowserSession
from agent_browser_automation.config import Settings
from agent_browser_automation.diagnosis.engine import DiagnosisEngine
from agent_browser_automation.evidence.store import EvidenceStore
from agent_browser_automation.publishing.service import PublishService
from agent_browser_automation.publishing.store import DraftStore
from agent_browser_automation.testing.compiler import TestCaseCompiler
from agent_browser_automation.workflow.matcher import WorkflowMatcher
from agent_browser_automation.workflow.store import WorkflowStore

from .tools.browser_tools import (
    BrowserSessionService,
    register_browser_tools,
    register_demo_browser_tools,
    register_exploration_tools,
    register_minimal_browser_tools,
    register_simple_browser_tools,
)
from .tools.evidence_tools import (
    EvidenceToolService,
    register_evidence_tools,
    register_task_evidence_tools,
)
from .tools.publishing_tools import PublishingToolService, register_publishing_tools
from .tools.testing_tools import TestCaseService, register_testing_tools
from .tools.workflow_tools import (
    WorkflowService,
    register_task_workflow_tools,
    register_workflow_tools,
)

# mcp 1.29 leaves this generic forward reference unresolved on Python 3.14.
FastMCPSettings.model_rebuild()


class ServerProfile(StrEnum):
    """MCP tool surfaces for task execution, controlled exploration, and operations."""

    AGENT = "agent"
    MINIMAL = "minimal"
    DEMO = "demo"
    SIMPLE = "simple"
    TASK = "task"
    EXPLORATION = "exploration"
    OPERATIONS = "operations"


def create_server(
    settings: Settings | None = None,
    profile: ServerProfile = ServerProfile.AGENT,
) -> FastMCP:
    resolved = settings or Settings()
    store = WorkflowStore(resolved.resolved_workflow_root)
    matcher = WorkflowMatcher(store)
    browser_session_service: BrowserSessionService | None = None

    @asynccontextmanager
    async def lifespan(_: FastMCP[None]) -> AsyncGenerator[None]:
        try:
            yield None
        finally:
            if browser_session_service is not None:
                await browser_session_service.close_all()

    mcp = FastMCP(f"agent-browser-automation-{profile.value}", lifespan=lifespan)

    def browser_factory() -> PlaywrightBrowser:
        executable = resolved.resolved_browser_executable
        if executable is not None and not executable.is_file():
            raise FileNotFoundError(f"browser executable does not exist: {executable}")
        return PlaywrightBrowser(
            BrowserSession(executable_path=executable, headless=resolved.browser_headless)
        )

    service = WorkflowService(
        matcher=matcher,
        browser_factory=browser_factory,
        artifact_root=resolved.resolved_artifact_root,
        action_timeout_ms=resolved.action_timeout_ms,
        navigation_timeout_ms=resolved.navigation_timeout_ms,
        max_concurrent_runs=resolved.max_concurrent_runs,
    )
    browser_session_service = BrowserSessionService(
        browser_factory,
        resolved.action_timeout_ms,
        resolved.navigation_timeout_ms,
        max_sessions=resolved.max_browser_sessions,
        max_exploration_steps=resolved.max_exploration_steps,
        max_exploration_failures=resolved.max_exploration_failures,
        artifact_root=resolved.resolved_artifact_root,
    )
    evidence_store = EvidenceStore(resolved.resolved_artifact_root)

    evidence_service = EvidenceToolService(evidence_store, DiagnosisEngine(evidence_store))
    if profile is ServerProfile.AGENT:
        # Keep workflow matching and exploration in one process so the browser
        # session, revision, and trajectory remain available after a miss.
        register_task_workflow_tools(mcp, service)
        register_exploration_tools(mcp, browser_session_service)
        register_task_evidence_tools(mcp, evidence_service)
    elif profile is ServerProfile.MINIMAL:
        # A small browser-only surface for straightforward workflow-or-page
        # control tasks. It keeps declared test-write primitives but omits
        # demo relaxation, evidence, cleanup, publishing, and API tools.
        register_task_workflow_tools(mcp, service)
        register_minimal_browser_tools(mcp, browser_session_service)
    elif profile is ServerProfile.DEMO:
        register_task_workflow_tools(mcp, service)
        register_demo_browser_tools(mcp, browser_session_service)
    elif profile is ServerProfile.SIMPLE:
        # No Environment, authentication, persistence, or operations surface:
        # only direct localhost page control plus optional workflow replay.
        register_task_workflow_tools(mcp, service)
        register_simple_browser_tools(mcp, browser_session_service)
    elif profile is ServerProfile.TASK:
        register_task_workflow_tools(mcp, service)
        register_task_evidence_tools(mcp, evidence_service)
    elif profile is ServerProfile.EXPLORATION:
        register_exploration_tools(mcp, browser_session_service)
        register_task_evidence_tools(mcp, evidence_service)
    else:
        register_browser_tools(mcp, browser_session_service)
        register_workflow_tools(mcp, service)
        register_testing_tools(
            mcp,
            TestCaseService(
                TestCaseCompiler(matcher),
                service,
            ),
        )
        register_publishing_tools(
            mcp,
            PublishingToolService(
                PublishService(
                    DraftStore(resolved.resolved_workflow_root),
                    browser_factory,
                    resolved.resolved_artifact_root,
                    resolved.action_timeout_ms,
                    resolved.navigation_timeout_ms,
                ),
                browser_session_service,
            ),
        )
        register_evidence_tools(mcp, evidence_service)
    return mcp
