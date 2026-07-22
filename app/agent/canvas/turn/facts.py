from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CanvasTurnFacts:
    """单轮画布请求开始前的轻量事实快照"""

    revision: int
    node_count: int
    edge_count: int
    mode: Literal["auto", "manual"]
    enable_tools: bool
    attachments_not_ready: bool
    pending_generation_count: int
