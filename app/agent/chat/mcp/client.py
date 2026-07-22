import json
from typing import List

from langchain_core.tools import StructuredTool

from app.server.infra.config import settings


def load_mcp_tools() -> List[StructuredTool]:
    """占位：读取 CHAT_MCP_SERVERS JSON，后续接 MCP 客户端。"""
    try:
        servers = json.loads(settings.CHAT_MCP_SERVERS)
    except json.JSONDecodeError as exc:
        raise ValueError("CHAT_MCP_SERVERS is not valid JSON") from exc
    if not isinstance(servers, list):
        raise ValueError("CHAT_MCP_SERVERS must be a JSON array")
    if not servers:
        return []
    return []
