from __future__ import annotations

from app.server.canvas.services.canvas_service import canvas_service
from app.server.projects.domain.enums import ProjectStatus
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.projects.persistence.projects import Projects
from app.server.projects.schemas import ProjectView


def _to_view(row: Projects) -> ProjectView:
    return ProjectView(
        id=int(row.id),
        name=row.name,
        status=int(row.status),
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


class ProjectService:
    async def require_owned(self, user_id: int, project_id: int) -> Projects:
        row = await Projects.filter(id=project_id, owner_user_id=str(user_id)).first()
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "project not found")
        return row

    async def list_for_user(
        self,
        user_id: int,
        *,
        page: int = 1,
        page_size: int = 11,
        query_text: str = "",
    ) -> tuple[list[ProjectView], int]:
        query = Projects.filter(owner_user_id=str(user_id))
        keyword = query_text.strip()
        if keyword:
            query = query.filter(name__icontains=keyword)
        total = await query.count()
        rows = (
            await query
            .order_by("-updated_at", "-id")
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [_to_view(r) for r in rows], total

    async def get_for_user(self, user_id: int, project_id: int) -> ProjectView:
        row = await self.require_owned(user_id, project_id)
        return _to_view(row)

    async def create(self, user_id: int, name: str) -> ProjectView:
        row = await Projects.create(
            owner_user_id=str(user_id),
            name=name.strip(),
            status=int(ProjectStatus.ACTIVE),
            tone_constraint={},
            style_constraint={},
            config={},
        )
        await canvas_service.ensure_meta(int(row.id))
        return _to_view(row)


project_service = ProjectService()
