from dataclasses import dataclass


@dataclass(frozen=True)
class AttachmentBrief:
    attachment_id: int
    filename: str
    mime_type: str
    status: str
    is_attached: bool = True
    workspace_path: str = ""


@dataclass(frozen=True)
class TurnPromptContext:
    user_id: int
    conversation_id: int
    model_key: str
    enable_tools: bool
    tool_names: list[str]
    has_turn_media_refs: bool = False
