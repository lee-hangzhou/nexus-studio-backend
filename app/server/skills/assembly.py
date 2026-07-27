from __future__ import annotations

from app.server.skills.domain.models import DualTreeView, SkillFileDetail, SkillTreeView
from app.server.skills.schemas.http import (
    DualTreeViewSchema,
    SkillDirMetaSchema,
    SkillFileDetailSchema,
    SkillFileMetaSchema,
    SkillTreeViewSchema,
)


def tree_to_schema(tree: SkillTreeView) -> SkillTreeViewSchema:
    """领域树视图转为 HTTP schema"""
    return SkillTreeViewSchema(
        dirs=[
            SkillDirMetaSchema(id=item.id, path=item.path, revision=item.revision)
            for item in tree.dirs
        ],
        files=[
            SkillFileMetaSchema(
                id=item.id,
                path=item.path,
                name=item.name,
                revision=item.revision,
                enabled=item.enabled,
                description=item.description,
            )
            for item in tree.files
        ],
    )


def dual_tree_to_schema(view: DualTreeView) -> DualTreeViewSchema:
    """双树领域视图转为 HTTP schema"""
    return DualTreeViewSchema(
        user=tree_to_schema(view.user),
        project=tree_to_schema(view.project) if view.project is not None else None,
    )


def file_detail_to_schema(detail: SkillFileDetail) -> SkillFileDetailSchema:
    """文件详情转为 HTTP schema"""
    return SkillFileDetailSchema(
        id=detail.meta.id,
        path=detail.meta.path,
        name=detail.meta.name,
        revision=detail.meta.revision,
        enabled=detail.meta.enabled,
        description=detail.meta.description,
        content=detail.content,
    )
