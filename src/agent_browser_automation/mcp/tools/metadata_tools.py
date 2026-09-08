from mcp.server.fastmcp import FastMCP

from agent_browser_automation.mcp.schemas import MetadataTableCreateRequest, ToolResult

from .browser_tools import BrowserSessionService


def register_metadata_tools(mcp: FastMCP, browsers: BrowserSessionService) -> None:
    @mcp.tool()
    async def metadata_table_create(
        request: MetadataTableCreateRequest,
    ) -> ToolResult:
        """Create and verify one prefixed OpenMetadata Table metadata entity."""
        return await browsers.create_metadata_table(request)

    _ = metadata_table_create
