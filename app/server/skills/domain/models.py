from __future__ import annotations

from dataclasses import dataclass

from app.server.skills.domain.enums import SkillScope


@dataclass(frozen=True)
class SkillFileMeta:
    """技能文件元数据"""

    id: int
    path: str
    name: str
    revision: int
    enabled: bool
    description: str


@dataclass(frozen=True)
class SkillDirMeta:
    """技能目录元数据"""

    id: int
    path: str
    revision: int


@dataclass(frozen=True)
class SkillTreeView:
    """技能树视图，不含文件内容"""

    dirs: tuple[SkillDirMeta, ...]
    files: tuple[SkillFileMeta, ...]


@dataclass(frozen=True)
class SkillFileDetail:
    """技能文件详情，含内容"""

    meta: SkillFileMeta
    content: str


@dataclass(frozen=True)
class SelectedSkill:
    """运行时选中的技能"""

    path: str
    scope: SkillScope
    content: str
    description: str
    revision: int | None = None


@dataclass(frozen=True)
class DualTreeView:
    """用户与项目双树视图"""

    user: SkillTreeView
    project: SkillTreeView | None
