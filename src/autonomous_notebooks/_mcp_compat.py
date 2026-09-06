"""Run on mcp 1.x or 2.x; on 1.x, stop the SDK answering cancelled requests.

mcp 1.x replies `{"error": {"code": 0, "message": "Request cancelled"}}` to a
request the client has already cancelled. Claude Code no longer knows that id,
logs "Received a response for an unknown message ID", and drops the stdio
connection — taking this server and every kernel in it down (journal 17).
mcp 2.x never answers cancelled requests, per spec; this gives 1.x the same
behaviour. Delete once every consumer is on mcp>=2.
"""

try:
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server
    from mcp.shared.session import (  # pyright: ignore[reportMissingImports]
        RequestResponder,
    )

    async def _cancel_without_response(self: RequestResponder) -> None:
        # Upstream 1.x: cancel the scope, mark completed, then send an error
        # response. Everything but the response.
        if not self._cancel_scope:  # pragma: no cover
            raise RuntimeError("No active cancel scope")
        self._cancel_scope.cancel()
        self._completed = True

    RequestResponder.cancel = _cancel_without_response

__all__ = ["Server"]
