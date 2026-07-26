from dataclasses import dataclass, field
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class CanvasScope:
    project_id: int
    episode_id: int
    user_id: int


@dataclass(frozen=True, slots=True)
class UpdateField(Generic[T]):
    provided: bool
    value: T | None = None

    @classmethod
    def omitted(cls) -> "UpdateField[T]":
        return cls(provided=False)

    @classmethod
    def set(cls, value: T | None) -> "UpdateField[T]":
        return cls(provided=True, value=value)


@dataclass(frozen=True, slots=True)
class NameAndCoverUpdate:
    name: UpdateField[str] = field(default_factory=UpdateField.omitted)
    cover_asset_id: UpdateField[int] = field(default_factory=UpdateField.omitted)
