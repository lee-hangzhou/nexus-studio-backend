import json
from typing import List

from langchain_core.tools import StructuredTool

from app.core.config import settings


def load_mcp_tools() -> List[StructuredTool]:
    """占位：读取 CHAT_MCP_SERVERS JSON，后续接 MCP 客户端。"""
    try:
        servers = json.loads(settings.CHAT_MCP_SERVERS)
    except json.JSONDecodeError:
        return []
    if not servers:
        return []
    return []
