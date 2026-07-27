from __future__ import annotations

from datetime import datetime, timezone

from tortoise.exceptions import IntegrityError

from app.contracts.canvas import CanvasSessionView
from app.server.canvas.domain.constants import CANVAS_DEFAULT_SESSION_TITLE
from app.server.canvas.domain.enums import CanvasSessionStatus
from app.server.canvas.persistence.sessions import CanvasSessions
from app.server.chat.services.constants import CONVERSATION_TITLE_MAX_LEN
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.projects.domain.models import CanvasScope


def _view(row: CanvasSessions) -> CanvasSessionView:
    """ORM 行转会话视图"""
    return CanvasSessionView(
        id=row.id,
        episode_id=row.episode_id,
        title=row.title,
        status=row.status,
        is_default=row.is_default,
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


def _clamp_session_title(title: str) -> str:
    """裁剪会话标题到契约上限"""
    cleaned = title.strip()
    if not cleaned:
        raise AppError(ErrorCode.INVALID_PARAMS, "title must not be empty")
    if len(cleaned) > CONVERSATION_TITLE_MAX_LEN:
        return cleaned[: CONVERSATION_TITLE_MAX_LEN - 1] + "…"
    return cleaned


def new_session_placeholder_title(*, now: datetime | None = None) -> str:
    """非默认会话可区分临时标题"""
    stamp = (now or datetime.now(timezone.utc)).astimezone().strftime("%H:%M")
    return f"{CANVAS_DEFAULT_SESSION_TITLE} · {stamp}"


class CanvasSessionService:
    """画布 Agent 会话应用服务"""

    async def list_sessions(self, scope: CanvasScope) -> list[CanvasSessionView]:
        """列出当前用户在该集的 ACTIVE 会话, 纯读无副作用"""
        rows = (
            await CanvasSessions.filter(
                episode_id=scope.episode_id,
                user_id=scope.user_id,
                status=CanvasSessionStatus.ACTIVE,
            )
            .order_by("-updated_at", "-id")
            .all()
        )
        return [_view(row) for row in rows]

    async def ensure_default_session(self, scope: CanvasScope) -> CanvasSessionView:
        """写用例: 确保存在 is_default ACTIVE 会话"""
        row = await CanvasSessions.filter(
            episode_id=scope.episode_id,
            user_id=scope.user_id,
            status=CanvasSessionStatus.ACTIVE,
            is_default=True,
        ).first()
        if row is not None:
            return _view(row)
        try:
            created = await CanvasSessions.create(
                episode_id=scope.episode_id,
                user_id=scope.user_id,
                title=CANVAS_DEFAULT_SESSION_TITLE,
                status=CanvasSessionStatus.ACTIVE,
                is_default=True,
            )
            return _view(created)
        except IntegrityError:
            row = await CanvasSessions.filter(
                episode_id=scope.episode_id,
                user_id=scope.user_id,
                status=CanvasSessionStatus.ACTIVE,
                is_default=True,
            ).first()
            if row is None:
                raise AppError(ErrorCode.INTERNAL_ERROR, "failed to ensure default canvas session")
            return _view(row)

    async def create_session(self, scope: CanvasScope, *, title: str | None = None) -> CanvasSessionView:
        """显式新建非默认会话"""
        resolved = (
            _clamp_session_title(title)
            if title is not None and title.strip()
            else new_session_placeholder_title()
        )
        row = await CanvasSessions.create(
            episode_id=scope.episode_id,
            user_id=scope.user_id,
            title=resolved,
            status=CanvasSessionStatus.ACTIVE,
            is_default=False,
        )
        return _view(row)

    async def require_owned_session(
        self,
        scope: CanvasScope,
        session_id: int,
        *,
        allow_closed: bool = False,
    ) -> CanvasSessions:
        """校验会话归属当前用户与集"""
        row = await CanvasSessions.filter(
            id=session_id,
            episode_id=scope.episode_id,
            user_id=scope.user_id,
        ).first()
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "canvas session not found")
        if not allow_closed and row.status != CanvasSessionStatus.ACTIVE:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "canvas session not found")
        return row

    async def update_session(
        self,
        scope: CanvasScope,
        session_id: int,
        *,
        title: str,
    ) -> CanvasSessionView:
        """更新会话标题"""
        row = await self.require_owned_session(scope, session_id)
        row.title = _clamp_session_title(title)
        row.updated_at = datetime.now(timezone.utc)
        await row.save(update_fields=["title", "updated_at"])
        return _view(row)

    async def touch_session(self, *, episode_id: int, session_id: int, user_id: int) -> None:
        """发消息等活动时刷新会话 updated_at"""
        await CanvasSessions.filter(
            id=session_id,
            episode_id=episode_id,
            user_id=user_id,
            status=CanvasSessionStatus.ACTIVE,
        ).update(updated_at=datetime.now(timezone.utc))

    async def list_active_session_ids(self, episode_id: int, *, user_id: int | None = None) -> list[int]:
        """列出集内 ACTIVE session id"""
        query = CanvasSessions.filter(episode_id=episode_id, status=CanvasSessionStatus.ACTIVE)
        if user_id is not None:
            query = query.filter(user_id=user_id)
        rows = await query.only("id")
        return [row.id for row in rows]

    async def list_all_session_ids(self, episode_id: int) -> list[int]:
        """列出集内全部 session id, 含 CLOSED"""
        rows = await CanvasSessions.filter(episode_id=episode_id).only("id")
        return [row.id for row in rows]

    async def count_active_for_user(self, scope: CanvasScope) -> int:
        """统计用户在该集的 ACTIVE 会话数"""
        return await CanvasSessions.filter(
            episode_id=scope.episode_id,
            user_id=scope.user_id,
            status=CanvasSessionStatus.ACTIVE,
        ).count()

    async def soft_close_session(self, row: CanvasSessions) -> None:
        """软关单个会话"""
        row.status = CanvasSessionStatus.CLOSED
        row.updated_at = datetime.now(timezone.utc)
        await row.save(update_fields=["status", "updated_at"])

    async def soft_close_all_for_episode(self, episode_id: int) -> list[int]:
        """软关集内全部 ACTIVE 会话, 返回被关 id"""
        rows = await CanvasSessions.filter(
            episode_id=episode_id,
            status=CanvasSessionStatus.ACTIVE,
        ).all()
        ids = [row.id for row in rows]
        if ids:
            await CanvasSessions.filter(id__in=ids).update(
                status=CanvasSessionStatus.CLOSED,
                updated_at=datetime.now(timezone.utc),
            )
        return ids


canvas_session_service = CanvasSessionService()
