"""Seller-only MCP bridge. An external x402 wallet supplies payment metadata."""

import json
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from atlas_terminal.errors import AtlasError
from atlas_terminal.mcp_server import error_result
from atlas_terminal.seller_client import SellerClient
from atlas_terminal.seller_models import ProductQuery


def create_seller_mcp(client: SellerClient) -> MCPServer:
    server = MCPServer(
        "Atlas Terminal seller",
        instructions=(
            "Quotes disclose price, capture age and fictional flags. Buying requires an external "
            "wallet with spending limits. Keep signatures in x402 transport metadata. "
            "Use a stable intent_id per purchase. Unknown payments require reconciliation, "
            "never a new signature. BLS data is freely available; Atlas charges for delivery."
        ),
    )

    def execute(call: Any) -> CallToolResult:
        try:
            result = call()
            data = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(data))], structured_content=data
            )
        except AtlasError as exc:
            return error_result(exc)
        except Exception:
            return error_result(AtlasError("INVALID_INPUT", "Seller tool failed validation."))

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def atlas_seller_catalog() -> CallToolResult:
        """Inspect prepared products and prices without signing or paying."""
        return execute(client.catalog)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=True
        )
    )
    def atlas_seller_quote(series: str, intent_id: str) -> CallToolResult:
        """Prepare or recover a quote for one stable purchase intent. Does not pay."""
        return execute(lambda: client.quote(ProductQuery(series=series), intent_id=intent_id))

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def atlas_seller_status(quote_id: str) -> CallToolResult:
        """Read the saved order status; a quote ID alone is not authorization."""
        return execute(lambda: client.status(quote_id))

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def atlas_seller_preflight(quote_id: str) -> CallToolResult:
        """Read advisory order readiness. The external wallet controls budgets."""
        return execute(lambda: client.preflight(quote_id))

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def atlas_seller_recover(quote_id: str) -> CallToolResult:
        """Retrieve the same purchased artifact without a new payment signature."""
        return execute(lambda: client.recover(quote_id))

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=True
        )
    )
    def atlas_seller_reconcile(quote_id: str, transaction: str | None = None) -> CallToolResult:
        """Reconcile an unknown payment using finalized chain evidence. Never pays."""
        return execute(lambda: client.reconcile(quote_id, transaction))

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=True
        )
    )
    def atlas_seller_purchase(quote_id: str, ctx: Context[Any, Any]) -> CallToolResult:
        """Buy using an authorized external wallet's _meta['x402/payment'], never tool arguments."""
        try:
            meta = ctx.request_context.meta
            payment = dict(meta).get("x402/payment") if meta is not None else None
            if payment is not None and not isinstance(payment, dict):
                raise AtlasError("PAYMENT_INVALID", "Invalid external-wallet metadata.")
            data, settlement, required = client.external_purchase(quote_id, payment)
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(data))],
                structured_content=data,
                is_error=required,
                _meta={"x402/payment-response": settlement} if settlement else None,
            )
        except AtlasError as exc:
            return error_result(exc)
        except Exception:
            return error_result(AtlasError("PAYMENT_INVALID", "External-wallet payment stopped."))

    return server
