from __future__ import annotations

from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.projects.persistence.repositories import ProjectRepository
from app.server.skills.domain.enums import SkillScope, SkillSurface
from app.server.skills.domain.limits import (
    MAX_DESCRIPTION_LEN,
    MAX_FILE_CHARS,
    MAX_FILES,
    MAX_NAME_LEN,
    MAX_NODES,
    MAX_SELECTED_SKILL_REFS,
    MAX_TOTAL_FILE_CHARS,
)
from app.server.skills.domain.models import (
    DualTreeView,
    SelectedSkill,
    SkillDirMeta,
    SkillFileDetail,
    SkillFileMeta,
    SkillTreeView,
)
from app.server.skills.domain.path import is_prefix_path, normalize_skill_path, parent_path
from app.server.skills.persistence.entries import UserSkillEntries
from app.server.skills.persistence.repository import UserSkillRepository


class UserSkillService:
    """用户自定义技能应用服务"""

    def __init__(
        self,
        *,
        skills: UserSkillRepository,
        projects: ProjectRepository,
    ) -> None:
        self._skills = skills
        self._projects = projects

    async def list_dual_tree(
        self,
        user_id: int,
        surface: SkillSurface,
        project_id: int | None,
        enabled_filter: bool | None,
    ) -> DualTreeView:
        """列出用户与项目双技能树"""
        user_tree = await self._build_tree_view(
            user_id=user_id,
            surface=surface,
            scope=SkillScope.USER,
            biz_key=user_id,
            enabled_filter=enabled_filter,
        )
        project_tree: SkillTreeView | None = None
        if project_id is not None:
            await self._require_project_write(user_id, project_id)
            project_tree = await self._build_tree_view(
                user_id=user_id,
                surface=surface,
                scope=SkillScope.PROJECT,
                biz_key=project_id,
                enabled_filter=enabled_filter,
            )
        return DualTreeView(user=user_tree, project=project_tree)

    async def get_file(
        self,
        user_id: int,
        surface: SkillSurface,
        scope: SkillScope,
        path: str,
        project_id: int | None,
    ) -> SkillFileDetail:
        """读取单个技能文件详情"""
        normalized = normalize_skill_path(path)
        biz_key = await self._resolve_biz_key(user_id, scope, project_id)
        row = await self._skills.get_by_path(surface, scope, biz_key, normalized)
        if row is None or row.is_dir:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "user skill file not found")
        return self._file_detail_from_row(row)

    async def mkdir(
        self,
        user_id: int,
        surface: SkillSurface,
        scope: SkillScope,
        path: str,
        project_id: int | None,
    ) -> DualTreeView:
        """创建技能目录"""
        normalized = normalize_skill_path(path)
        biz_key = await self._resolve_biz_key(user_id, scope, project_id)
        existing = await self._skills.get_by_path(surface, scope, biz_key, normalized)
        if existing is not None:
            raise AppError(ErrorCode.INVALID_PARAMS, "skill path already exists")
        parent = parent_path(normalized)
        if parent is not None:
            parent_row = await self._skills.get_by_path(surface, scope, biz_key, parent)
            if parent_row is None or not parent_row.is_dir:
                raise AppError(ErrorCode.INVALID_PARAMS, "parent directory not found")
        node_count = await self._skills.count_nodes(surface, scope, biz_key)
        if node_count >= MAX_NODES:
            raise AppError(ErrorCode.INVALID_PARAMS, "skill node limit exceeded")
        await self._skills.create_dir(
            user_id=user_id,
            surface=surface,
            scope=scope,
            biz_key=biz_key,
            path=normalized,
        )
        return await self.list_dual_tree(user_id, surface, project_id, None)

    async def write_file(
        self,
        user_id: int,
        surface: SkillSurface,
        scope: SkillScope,
        path: str,
        name: str,
        content: str,
        description: str | None,
        revision: int | None,
        project_id: int | None,
        entry_id: int | None,
    ) -> DualTreeView:
        """创建或覆盖技能文件"""
        await self._prepare_and_commit_file_write(
            user_id=user_id,
            surface=surface,
            scope=scope,
            path=path,
            name=name,
            content=content,
            description=description,
            revision=revision,
            project_id=project_id,
            entry_id=entry_id,
        )
        return await self.list_dual_tree(user_id, surface, project_id, None)

    async def write_user_file_detail(
        self,
        user_id: int,
        surface: SkillSurface,
        path: str,
        name: str,
        content: str,
        description: str | None,
        revision: int | None,
    ) -> SkillFileDetail:
        """写入 user 域技能并返回详情（供 Port，避免写后再读）"""
        row = await self._prepare_and_commit_file_write(
            user_id=user_id,
            surface=surface,
            scope=SkillScope.USER,
            path=path,
            name=name,
            content=content,
            description=description,
            revision=revision,
            project_id=None,
            entry_id=None,
        )
        return self._file_detail_from_row(row)

    async def _prepare_and_commit_file_write(
        self,
        *,
        user_id: int,
        surface: SkillSurface,
        scope: SkillScope,
        path: str,
        name: str,
        content: str,
        description: str | None,
        revision: int | None,
        project_id: int | None,
        entry_id: int | None,
    ) -> UserSkillEntries:
        """校验并写入技能文件，返回写入后的 ORM 行"""
        normalized = normalize_skill_path(path)
        biz_key = await self._resolve_biz_key(user_id, scope, project_id)
        clean_name = name.strip()
        if not clean_name:
            raise AppError(ErrorCode.INVALID_PARAMS, "name required")
        if len(clean_name) > MAX_NAME_LEN:
            raise AppError(ErrorCode.INVALID_PARAMS, "name too long")
        if len(content) > MAX_FILE_CHARS:
            raise AppError(ErrorCode.INVALID_PARAMS, "skill content too long")
        resolved_description = await self._ensure_description(
            clean_name,
            content,
            description,
            existing_description=(
                None
                if entry_id is None and revision is None
                else await self._peek_existing_description(
                    surface=surface,
                    scope=scope,
                    biz_key=biz_key,
                    path=normalized,
                    entry_id=entry_id,
                )
            ),
        )
        return await self._commit_file_write(
            user_id=user_id,
            surface=surface,
            scope=scope,
            biz_key=biz_key,
            normalized=normalized,
            clean_name=clean_name,
            content=content,
            resolved_description=resolved_description,
            revision=revision,
            entry_id=entry_id,
        )

    async def _commit_file_write(
        self,
        *,
        user_id: int,
        surface: SkillSurface,
        scope: SkillScope,
        biz_key: int,
        normalized: str,
        clean_name: str,
        content: str,
        resolved_description: str,
        revision: int | None,
        entry_id: int | None,
    ) -> UserSkillEntries:
        """创建或 CAS 覆盖技能文件，返回写入后的 ORM 行"""
        if entry_id is not None:
            return await self._write_file_by_id(
                surface=surface,
                scope=scope,
                biz_key=biz_key,
                entry_id=entry_id,
                clean_name=clean_name,
                content=content,
                description=resolved_description,
                revision=revision,
            )
        existing = await self._skills.get_by_path(surface, scope, biz_key, normalized)
        if revision is not None:
            if existing is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "user skill file not found")
            if existing.is_dir:
                raise AppError(ErrorCode.INVALID_PARAMS, "skill path is a directory")
            await self._validate_total_chars(
                surface, scope, biz_key, len(content), exclude_entry_id=int(existing.id)
            )
            return await self._skills.update_file_cas(
                int(existing.id),
                expected_revision=revision,
                name=clean_name,
                description=resolved_description,
                content=content,
            )
        if existing is not None:
            if existing.is_dir:
                raise AppError(ErrorCode.INVALID_PARAMS, "skill path is a directory")
            raise AppError(ErrorCode.INVALID_PARAMS, "revision required to overwrite existing skill file")
        parent = parent_path(normalized)
        if parent is not None:
            parent_row = await self._skills.get_by_path(surface, scope, biz_key, parent)
            if parent_row is None or not parent_row.is_dir:
                raise AppError(ErrorCode.INVALID_PARAMS, "parent directory not found")
        node_count = await self._skills.count_nodes(surface, scope, biz_key)
        if node_count >= MAX_NODES:
            raise AppError(ErrorCode.INVALID_PARAMS, "skill node limit exceeded")
        file_count = await self._skills.count_files(surface, scope, biz_key)
        if file_count >= MAX_FILES:
            raise AppError(ErrorCode.INVALID_PARAMS, "skill file limit exceeded")
        await self._validate_total_chars(surface, scope, biz_key, len(content))
        return await self._skills.create_file(
            user_id=user_id,
            surface=surface,
            scope=scope,
            biz_key=biz_key,
            path=normalized,
            name=clean_name,
            description=resolved_description,
            content=content,
        )

    async def move_path(
        self,
        user_id: int,
        surface: SkillSurface,
        scope: SkillScope,
        from_path: str,
        to_path: str,
        revision: int,
        project_id: int | None,
    ) -> DualTreeView:
        """移动技能文件或目录"""
        normalized_from = normalize_skill_path(from_path)
        normalized_to = normalize_skill_path(to_path)
        if normalized_from == normalized_to:
            raise AppError(ErrorCode.INVALID_PARAMS, "source and destination paths are identical")
        if is_prefix_path(normalized_from, normalized_to):
            raise AppError(ErrorCode.INVALID_PARAMS, "cannot move directory into its descendant")
        biz_key = await self._resolve_biz_key(user_id, scope, project_id)
        source = await self._skills.get_by_path(surface, scope, biz_key, normalized_from)
        if source is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "user skill entry not found")
        destination = await self._skills.get_by_path(surface, scope, biz_key, normalized_to)
        if destination is not None:
            raise AppError(ErrorCode.INVALID_PARAMS, "destination path already exists")
        to_parent = parent_path(normalized_to)
        if to_parent is not None:
            parent_row = await self._skills.get_by_path(surface, scope, biz_key, to_parent)
            if parent_row is None or not parent_row.is_dir:
                raise AppError(ErrorCode.INVALID_PARAMS, "destination parent directory not found")
        await self._skills.move(
            surface=surface,
            scope=scope,
            biz_key=biz_key,
            from_path=normalized_from,
            to_path=normalized_to,
            expected_revision=revision,
        )
        return await self.list_dual_tree(user_id, surface, project_id, None)

    async def remove_path(
        self,
        user_id: int,
        surface: SkillSurface,
        scope: SkillScope,
        path: str,
        revision: int,
        project_id: int | None,
    ) -> DualTreeView:
        """删除技能文件或目录并级联子路径"""
        normalized = normalize_skill_path(path)
        biz_key = await self._resolve_biz_key(user_id, scope, project_id)
        row = await self._skills.get_by_path(surface, scope, biz_key, normalized)
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "user skill entry not found")
        await self._skills.delete_entry_cas(
            int(row.id),
            expected_revision=revision,
            surface=surface,
            scope=scope,
            biz_key=biz_key,
            path=normalized,
        )
        return await self.list_dual_tree(user_id, surface, project_id, None)

    async def set_enabled(
        self,
        user_id: int,
        surface: SkillSurface,
        scope: SkillScope,
        path: str,
        enabled: bool,
        revision: int,
        project_id: int | None,
    ) -> DualTreeView:
        """切换技能文件启用状态"""
        normalized = normalize_skill_path(path)
        biz_key = await self._resolve_biz_key(user_id, scope, project_id)
        row = await self._skills.get_by_path(surface, scope, biz_key, normalized)
        if row is None or row.is_dir:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "user skill file not found")
        await self._skills.set_enabled_cas(
            int(row.id),
            expected_revision=revision,
            enabled=enabled,
        )
        return await self.list_dual_tree(user_id, surface, project_id, None)

    async def resolve_selected_skills(
        self,
        surface: SkillSurface,
        user_id: int,
        project_id: int | None,
        paths: list[str],
    ) -> list[SelectedSkill]:
        """解析运行时选中的技能列表"""
        normalized_paths = self._normalize_selected_paths(paths)
        user_files = await self._indexed_files(
            surface=surface,
            scope=SkillScope.USER,
            biz_key=user_id,
        )
        project_files: dict[str, UserSkillEntries] = {}
        if project_id is not None:
            await self._require_project_write(user_id, project_id)
            project_files = await self._indexed_files(
                surface=surface,
                scope=SkillScope.PROJECT,
                biz_key=project_id,
            )
        merged_enabled = {
            path: row
            for path, row in user_files.items()
            if row.enabled and not row.is_dir
        }
        for path, row in project_files.items():
            if row.enabled and not row.is_dir:
                merged_enabled[path] = row
        result: list[SelectedSkill] = []
        for path in normalized_paths:
            if path in merged_enabled:
                row = merged_enabled[path]
                project_wins = (
                    path in project_files
                    and not project_files[path].is_dir
                    and bool(project_files[path].enabled)
                )
                winning_scope = SkillScope.PROJECT if project_wins else SkillScope.USER
                result.append(
                    SelectedSkill(
                        path=path,
                        scope=winning_scope,
                        content=row.content or "",
                        description=row.description or "",
                        revision=int(row.revision),
                    )
                )
                continue
            if path in project_files and not project_files[path].is_dir and not project_files[path].enabled:
                raise AppError(ErrorCode.INVALID_PARAMS, "selected_skill_disabled")
            if path in user_files and not user_files[path].is_dir and not user_files[path].enabled:
                raise AppError(ErrorCode.INVALID_PARAMS, "selected_skill_disabled")
            raise AppError(ErrorCode.INVALID_PARAMS, "selected_skill_not_found")
        return result

    async def list_enabled_files_for_index(
        self,
        *,
        surface: SkillSurface,
        user_id: int,
        project_id: int | None,
    ) -> list[SelectedSkill]:
        """列出启用技能的索引载荷, project 同 path 覆盖 user"""
        user_files = await self._indexed_files(
            surface=surface,
            scope=SkillScope.USER,
            biz_key=user_id,
        )
        merged: dict[str, SelectedSkill] = {}
        for path, row in user_files.items():
            if row.is_dir or not row.enabled:
                continue
            merged[path] = SelectedSkill(
                path=path,
                scope=SkillScope.USER,
                content="",
                description=row.description or "",
                revision=int(row.revision),
            )
        if project_id is not None:
            await self._require_project_write(user_id, project_id)
            project_files = await self._indexed_files(
                surface=surface,
                scope=SkillScope.PROJECT,
                biz_key=project_id,
            )
            for path, row in project_files.items():
                if row.is_dir or not row.enabled:
                    continue
                merged[path] = SelectedSkill(
                    path=path,
                    scope=SkillScope.PROJECT,
                    content="",
                    description=row.description or "",
                    revision=int(row.revision),
                )
        return [merged[key] for key in sorted(merged.keys())]

    async def _write_file_by_id(
        self,
        *,
        surface: SkillSurface,
        scope: SkillScope,
        biz_key: int,
        entry_id: int,
        clean_name: str,
        content: str,
        description: str,
        revision: int | None,
    ) -> UserSkillEntries:
        """按主键 CAS 覆盖技能文件，返回写入后的行"""
        row = await self._skills.get_by_id(entry_id)
        if row is None or row.is_dir:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "user skill file not found")
        if (
            str(row.surface) != str(surface)
            or str(row.scope) != str(scope)
            or int(row.biz_key) != biz_key
        ):
            raise AppError(ErrorCode.PERMISSION_DENIED, "user skill entry scope mismatch")
        if revision is None:
            raise AppError(ErrorCode.INVALID_PARAMS, "revision required to overwrite existing skill file")
        await self._validate_total_chars(surface, scope, biz_key, len(content), exclude_entry_id=entry_id)
        return await self._skills.update_file_cas(
            entry_id,
            expected_revision=revision,
            name=clean_name,
            description=description,
            content=content,
        )

    async def _build_tree_view(
        self,
        *,
        user_id: int,
        surface: SkillSurface,
        scope: SkillScope,
        biz_key: int,
        enabled_filter: bool | None,
    ) -> SkillTreeView:
        """把条目列表组装为技能树视图"""
        _ = user_id
        rows = await self._skills.list_entries(surface, scope, biz_key)
        dirs: list[SkillDirMeta] = []
        files: list[SkillFileMeta] = []
        for row in rows:
            if row.is_dir:
                dirs.append(
                    SkillDirMeta(
                        id=int(row.id),
                        path=row.path,
                        revision=int(row.revision),
                    )
                )
                continue
            if enabled_filter is not None and bool(row.enabled) != enabled_filter:
                continue
            files.append(self._file_meta_from_row(row))
        return SkillTreeView(dirs=tuple(dirs), files=tuple(files))

    async def _indexed_files(
        self,
        *,
        surface: SkillSurface,
        scope: SkillScope,
        biz_key: int,
    ) -> dict[str, UserSkillEntries]:
        """按路径索引全部文件条目"""
        rows = await self._skills.list_entries(surface, scope, biz_key)
        return {
            row.path: row
            for row in rows
            if not row.is_dir
        }

    async def _resolve_biz_key(
        self,
        user_id: int,
        scope: SkillScope,
        project_id: int | None,
    ) -> int:
        """解析作用域对应的 biz_key"""
        if scope == SkillScope.USER:
            return user_id
        if project_id is None:
            raise AppError(ErrorCode.INVALID_PARAMS, "project_id required for project scope")
        await self._require_project_write(user_id, project_id)
        return project_id

    async def _require_project_write(self, user_id: int, project_id: int) -> None:
        """校验用户对项目的写权限"""
        row = await self._projects.get_owned(user_id, project_id)
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "project not found")

    async def _validate_total_chars(
        self,
        surface: SkillSurface,
        scope: SkillScope,
        biz_key: int,
        new_content_len: int,
        *,
        exclude_entry_id: int | None = None,
    ) -> None:
        """校验文件内容总字符数上限"""
        current_total = await self._skills.sum_file_chars(
            surface,
            scope,
            biz_key,
            exclude_entry_id=exclude_entry_id,
        )
        if current_total + new_content_len > MAX_TOTAL_FILE_CHARS:
            raise AppError(ErrorCode.INVALID_PARAMS, "skill total content size exceeded")

    async def _peek_existing_description(
        self,
        *,
        surface: SkillSurface,
        scope: SkillScope,
        biz_key: int,
        path: str,
        entry_id: int | None,
    ) -> str | None:
        """覆盖写时读取已有描述, 供省略 description 字段时保留"""
        row = None
        if entry_id is not None:
            row = await self._skills.get_by_id(entry_id)
        else:
            row = await self._skills.get_by_path(surface, scope, biz_key, path)
        if row is None or row.is_dir:
            return None
        return row.description

    async def _ensure_description(
        self,
        name: str,
        content: str,
        description: str | None,
        *,
        existing_description: str | None,
    ) -> str:
        """确定技能描述, 空串走网关补全且失败则整单失败"""
        from app.server.skills.services.description_fill import fill_skill_description

        if description is not None and description.strip():
            resolved = description.strip()
            if len(resolved) > MAX_DESCRIPTION_LEN:
                raise AppError(ErrorCode.INVALID_PARAMS, "description too long")
            return resolved
        if description is None and existing_description is not None:
            kept = existing_description.strip()
            if kept:
                return kept[:MAX_DESCRIPTION_LEN]
        return await fill_skill_description(name=name, content=content)

    def _normalize_selected_paths(self, paths: list[str]) -> list[str]:
        """规范化并去重选中路径"""
        if len(paths) > MAX_SELECTED_SKILL_REFS:
            raise AppError(ErrorCode.INVALID_PARAMS, "too many selected skills")
        seen: set[str] = set()
        normalized: list[str] = []
        for path in paths:
            item = normalize_skill_path(path)
            if item in seen:
                continue
            seen.add(item)
            normalized.append(item)
        return normalized

    def _file_meta_from_row(self, row: UserSkillEntries) -> SkillFileMeta:
        """把 ORM 行转为文件元数据"""
        return SkillFileMeta(
            id=int(row.id),
            path=row.path,
            name=row.name or "",
            revision=int(row.revision),
            enabled=bool(row.enabled),
            description=row.description or "",
        )

    def _file_detail_from_row(self, row: UserSkillEntries) -> SkillFileDetail:
        """把 ORM 行转为文件详情"""
        return SkillFileDetail(
            meta=self._file_meta_from_row(row),
            content=row.content or "",
        )


_user_skill_repository = UserSkillRepository()
_project_repository = ProjectRepository()

user_skill_service = UserSkillService(
    skills=_user_skill_repository,
    projects=_project_repository,
)
