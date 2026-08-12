"""MCP exposure of the batch-persisted performance trace."""
from __future__ import annotations

import json

from mcp_server.server import handle_request


def test_performance_tool_requires_task_id_without_leaking_paths():
    response = handle_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "get_production_performance",
            "arguments": {"task_id": "missing-task"},
        },
    })
    result = response["result"]
    assert result["isError"] is True
    assert "structuredContent" in result
    json.dumps(result["structuredContent"], ensure_ascii=False)
