"""MCP 2.x transport compatibility for the pinned official x402 MCP client."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from mcp.client.session import ClientSession
from mcp.types import CallToolResult, RequestParamsMeta
from x402 import x402Client
from x402.mcp.client import x402MCPSession


@dataclass(frozen=True)
class MCPResultView:
    """Expose the official client's camelCase accessors over MCP 2.x results."""

    wire_result: CallToolResult

    @property
    def content(self) -> Any:
        return self.wire_result.content

    @property
    def isError(self) -> bool:
        return bool(self.wire_result.is_error)

    @property
    def structuredContent(self) -> dict[str, Any] | None:
        return self.wire_result.structured_content

    @property
    def structured_content(self) -> dict[str, Any] | None:
        return self.wire_result.structured_content

    @property
    def meta(self) -> dict[str, Any] | None:
        return self.wire_result.meta


class MCP2SessionAdapter:
    def __init__(self, session: ClientSession) -> None:
        self.session = session

    async def initialize(self) -> Any:
        return await self.session.initialize()

    async def list_tools(self) -> Any:
        return await self.session.list_tools()

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        read_timeout_seconds: timedelta | float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> MCPResultView:
        timeout = (
            read_timeout_seconds.total_seconds()
            if isinstance(read_timeout_seconds, timedelta)
            else read_timeout_seconds
        )
        result = await self.session.call_tool(
            name,
            arguments or {},
            read_timeout_seconds=timeout,
            meta=cast(RequestParamsMeta | None, meta),
        )
        return MCPResultView(result)


def create_x402_mcp_session(
    session: ClientSession,
    payment_client: x402Client,
    *,
    auto_payment: bool = False,
) -> x402MCPSession:
    """The caller configures its wallet, spending policy and payment-ID hook.

    This adapter only maps MCP result names and timeout units. Signing and x402
    payment creation stay in the official SDK; automatic payment is opt-in.
    """
    return x402MCPSession(MCP2SessionAdapter(session), payment_client, auto_payment=auto_payment)
