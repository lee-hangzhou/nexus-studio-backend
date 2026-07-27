from __future__ import annotations

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.skills.domain.enums import SkillScope, SkillSurface
from app.server.skills.persistence.entries import UserSkillEntries


class UserSkillRepository:
    """用户技能条目仓储"""

    async def list_entries(
        self,
        surface: SkillSurface | str,
        scope: SkillScope | str,
        biz_key: int,
    ) -> list[UserSkillEntries]:
        """列出指定作用域下的全部条目"""
        return await UserSkillEntries.filter(
            surface=str(surface),
            scope=str(scope),
            biz_key=biz_key,
        ).order_by("path").all()

    async def get_by_path(
        self,
        surface: SkillSurface | str,
        scope: SkillScope | str,
        biz_key: int,
        path: str,
    ) -> UserSkillEntries | None:
        """按路径查询单条条目"""
        return await UserSkillEntries.filter(
            surface=str(surface),
            scope=str(scope),
            biz_key=biz_key,
            path=path,
        ).first()

    async def get_by_id(self, entry_id: int) -> UserSkillEntries | None:
        """按主键查询单条条目"""
        return await UserSkillEntries.filter(id=entry_id).first()

    async def create_dir(
        self,
        *,
        user_id: int,
        surface: SkillSurface | str,
        scope: SkillScope | str,
        biz_key: int,
        path: str,
    ) -> UserSkillEntries:
        """创建目录条目"""
        try:
            return await UserSkillEntries.create(
                user_id=user_id,
                surface=str(surface),
                scope=str(scope),
                biz_key=biz_key,
                path=path,
                is_dir=True,
                content=None,
                name=None,
                description=None,
                enabled=True,
                revision=1,
            )
        except IntegrityError as exc:
            raise AppError(ErrorCode.INVALID_PARAMS, "skill path already exists") from exc

    async def create_file(
        self,
        *,
        user_id: int,
        surface: SkillSurface | str,
        scope: SkillScope | str,
        biz_key: int,
        path: str,
        name: str,
        description: str,
        content: str,
        enabled: bool = True,
    ) -> UserSkillEntries:
        """创建文件条目"""
        try:
            return await UserSkillEntries.create(
                user_id=user_id,
                surface=str(surface),
                scope=str(scope),
                biz_key=biz_key,
                path=path,
                is_dir=False,
                content=content,
                name=name,
                description=description,
                enabled=enabled,
                revision=1,
            )
        except IntegrityError as exc:
            raise AppError(ErrorCode.INVALID_PARAMS, "skill path already exists") from exc

    async def update_file_cas(
        self,
        entry_id: int,
        *,
        expected_revision: int,
        name: str,
        description: str,
        content: str,
    ) -> UserSkillEntries:
        """按 revision CAS 更新文件内容与元数据"""
        updated = await UserSkillEntries.filter(
            id=entry_id,
            revision=expected_revision,
            is_dir=False,
        ).update(
            name=name,
            description=description,
            content=content,
            revision=expected_revision + 1,
        )
        if updated != 1:
            row = await self.get_by_id(entry_id)
            self._raise_revision_conflict(
                entry_id=entry_id,
                path=row.path if row is not None else None,
            )
        row = await self.get_by_id(entry_id)
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "user skill entry not found")
        return row

    async def set_enabled_cas(
        self,
        entry_id: int,
        *,
        expected_revision: int,
        enabled: bool,
    ) -> UserSkillEntries:
        """按 revision CAS 切换文件启用状态"""
        updated = await UserSkillEntries.filter(
            id=entry_id,
            revision=expected_revision,
            is_dir=False,
        ).update(
            enabled=enabled,
            revision=expected_revision + 1,
        )
        if updated != 1:
            row = await self.get_by_id(entry_id)
            self._raise_revision_conflict(
                entry_id=entry_id,
                path=row.path if row is not None else None,
            )
        row = await self.get_by_id(entry_id)
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "user skill entry not found")
        return row

    async def delete_by_path_prefix(
        self,
        surface: SkillSurface | str,
        scope: SkillScope | str,
        biz_key: int,
        path: str,
    ) -> int:
        """按路径前缀级联删除目录或文件"""
        exact = await UserSkillEntries.filter(
            surface=str(surface),
            scope=str(scope),
            biz_key=biz_key,
            path=path,
        ).delete()
        prefix = await UserSkillEntries.filter(
            surface=str(surface),
            scope=str(scope),
            biz_key=biz_key,
            path__startswith=f"{path}/",
        ).delete()
        return exact + prefix

    async def delete_entry_cas(
        self,
        entry_id: int,
        *,
        expected_revision: int,
        surface: SkillSurface | str,
        scope: SkillScope | str,
        biz_key: int,
        path: str,
    ) -> None:
        """按 revision CAS 删除条目并级联子路径（同事务）"""
        async with in_transaction():
            deleted = await UserSkillEntries.filter(
                id=entry_id,
                revision=expected_revision,
            ).delete()
            if deleted != 1:
                existing = await self.get_by_id(entry_id)
                self._raise_revision_conflict(
                    entry_id=entry_id,
                    path=existing.path if existing is not None else path,
                )
            await UserSkillEntries.filter(
                surface=str(surface),
                scope=str(scope),
                biz_key=biz_key,
                path__startswith=f"{path}/",
            ).delete()

    async def move(
        self,
        *,
        surface: SkillSurface | str,
        scope: SkillScope | str,
        biz_key: int,
        from_path: str,
        to_path: str,
        expected_revision: int,
    ) -> UserSkillEntries:
        """移动文件或目录并级联更新子路径"""
        async with in_transaction():
            source = await self.get_by_path(surface, scope, biz_key, from_path)
            if source is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "user skill entry not found")
            updated = await UserSkillEntries.filter(
                id=source.id,
                revision=expected_revision,
            ).update(
                path=to_path,
                revision=expected_revision + 1,
            )
            if updated != 1:
                self._raise_revision_conflict(entry_id=int(source.id), path=from_path)
            descendants = await UserSkillEntries.filter(
                surface=str(surface),
                scope=str(scope),
                biz_key=biz_key,
                path__startswith=f"{from_path}/",
            ).all()
            for row in descendants:
                suffix = row.path[len(from_path) :]
                new_path = f"{to_path}{suffix}"
                child_updated = await UserSkillEntries.filter(id=row.id).update(path=new_path)
                if child_updated != 1:
                    self._raise_revision_conflict(entry_id=int(row.id), path=row.path)
            moved = await self.get_by_path(surface, scope, biz_key, to_path)
            if moved is None:
                raise AppError(ErrorCode.INTERNAL_ERROR, "user skill move failed")
            return moved

    async def count_nodes(
        self,
        surface: SkillSurface | str,
        scope: SkillScope | str,
        biz_key: int,
    ) -> int:
        """统计节点总数"""
        return await UserSkillEntries.filter(
            surface=str(surface),
            scope=str(scope),
            biz_key=biz_key,
        ).count()

    async def count_files(
        self,
        surface: SkillSurface | str,
        scope: SkillScope | str,
        biz_key: int,
    ) -> int:
        """统计文件总数"""
        return await UserSkillEntries.filter(
            surface=str(surface),
            scope=str(scope),
            biz_key=biz_key,
            is_dir=False,
        ).count()

    async def sum_file_chars(
        self,
        surface: SkillSurface | str,
        scope: SkillScope | str,
        biz_key: int,
        *,
        exclude_entry_id: int | None = None,
    ) -> int:
        """统计文件内容字符总数"""
        query = UserSkillEntries.filter(
            surface=str(surface),
            scope=str(scope),
            biz_key=biz_key,
            is_dir=False,
        )
        if exclude_entry_id is not None:
            query = query.exclude(id=exclude_entry_id)
        rows = await query.only("content").all()
        return sum(len(row.content or "") for row in rows)

    def _raise_revision_conflict(
        self,
        *,
        entry_id: int | None,
        path: str | None,
    ) -> None:
        """抛出技能 revision 冲突错误"""
        raise AppError(
            ErrorCode.USER_SKILL_REVISION_CONFLICT,
            "user skill revision conflict",
            details={
                "kind": "user_skill_entry",
                "id": entry_id,
                "path": path,
            },
        )
