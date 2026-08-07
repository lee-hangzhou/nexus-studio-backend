from dataclasses import dataclass
from enum import StrEnum

from app.server.canvas.domain.enums import (
    CanvasNodeKind,
    CanvasNodeStatus,
    CanvasSourcePort,
    CanvasTargetPort,
)


class CanvasInputWaitReason(StrEnum):
    SOURCE_MISSING = "source_missing"
    SOURCE_FAILED = "source_failed"
    TEXT_NOT_READY = "text_not_ready"
    ASSET_NOT_READY = "asset_not_ready"


@dataclass(frozen=True)
class CanvasInputSource:
    node_id: str
    kind: CanvasNodeKind
    status: CanvasNodeStatus
    source_port: CanvasSourcePort
    target_port: CanvasTargetPort


@dataclass(frozen=True)
class CanvasInputWait:
    reason: CanvasInputWaitReason
    source_node_id: str
    source: CanvasInputSource | None = None


@dataclass(frozen=True)
class UpstreamText:
    text: str
    source_node_id: str


@dataclass(frozen=True)
class RefSlot:
    slot: int
    label: str
    asset_id: int
    source_node_id: str
    kind: CanvasNodeKind


@dataclass(frozen=True)
class ResolvedCanvasInputs:
    node_id: str
    local_prompt: str
    upstream_texts: tuple[UpstreamText, ...] = ()
    refs: tuple[RefSlot, ...] = ()
    library_ref_asset_ids: tuple[int, ...] = ()
    waiting_on: tuple[CanvasInputWait, ...] = ()
    sources: tuple[CanvasInputSource, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.waiting_on

    def missing_reasons(self) -> list[str]:
        return [
            f"{item.source_node_id}:{item.reason.value}"
            for item in self.waiting_on
        ]
