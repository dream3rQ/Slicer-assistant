# A small MCP server (JSON-RPC 2.0) over the plugin's tool registry.
# The official MCP SDK needs Python 3.10+, so we implement the three methods
# the case requires: initialize, tools/list, tools/call. The agent calls the
# same object in-process, and external MCP clients can reach it at POST /mcp.
import json
import traceback

from . import tools as tool_registry

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "ste-slicer-ai-assistant", "version": "0.1.0"}


class McpServer:
    def __init__(self, context_factory=None):
        # context_factory() -> ToolContext used by tools/call from external clients
        self._context_factory = context_factory

    # ---- in-process API used by the agent ----
    def list_tools(self):
        return [
            {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
            for t in tool_registry.TOOLS.values()
        ]

    def openai_tools(self):
        return [
            {"type": "function", "function": {
                "name": t["name"], "description": t["description"], "parameters": t["inputSchema"]}}
            for t in tool_registry.TOOLS.values() if t.get("for_llm", True)
        ]

    def call_tool(self, name, arguments, context):
        """Returns (result_dict, is_error)."""
        try:
            return tool_registry.call(name, arguments or {}, context), False
        except tool_registry.ToolError as e:
            return {"error": str(e)}, True
        except Exception as e:
            return {"error": "%s: %s" % (type(e).__name__, e), "traceback": traceback.format_exc()[-1500:]}, True

    # ---- JSON-RPC endpoint ----
    def handle(self, payload):
        if isinstance(payload, list):
            replies = [r for r in (self._handle_one(p) for p in payload) if r is not None]
            return replies or None
        return self._handle_one(payload)

    def _handle_one(self, req):
        if not isinstance(req, dict):
            return _error(None, -32600, "Invalid Request")
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        is_notification = "id" not in req
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                }
            elif method == "notifications/initialized" or method == "initialized":
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.list_tools()}
            elif method == "tools/call":
                name = params.get("name")
                if name not in tool_registry.TOOLS:
                    return _error(req_id, -32602, "Unknown tool: %s" % name)
                context = self._context_factory() if self._context_factory else None
                data, is_error = self.call_tool(name, params.get("arguments") or {}, context)
                result = {
                    "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                    "isError": is_error,
                }
            else:
                return None if is_notification else _error(req_id, -32601, "Method not found: %s" % method)
        except Exception as e:
            return _error(req_id, -32603, "Internal error: %s" % e)
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
