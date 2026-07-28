from dataclasses import dataclass


@dataclass(frozen=True)
class PublishResult:
    attachment_id: int
    asset_id: int
    filename: str
    mime_type: str
    storage_key: str
    workspace_path: str
    size: int
