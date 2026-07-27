from fastapi import APIRouter, Request

from app.server.api.schemas import Response
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.skills.assembly import dual_tree_to_schema, file_detail_to_schema
from app.server.skills.domain.enums import SkillScope
from app.server.skills.schemas import (
    DualTreeViewSchema,
    SkillFileDetailSchema,
    UserSkillsGetRequest,
    UserSkillsListRequest,
    UserSkillsMkdirRequest,
    UserSkillsMoveRequest,
    UserSkillsRemoveRequest,
    UserSkillsSetEnabledRequest,
    UserSkillsWriteRequest,
)
from app.server.skills.services import user_skill_service

router = APIRouter()


def _require_project_id(scope: SkillScope, project_id: int | None) -> None:
    """project 作用域必须带 project_id"""
    if scope == SkillScope.PROJECT and project_id is None:
        raise AppError(ErrorCode.INVALID_PARAMS, "project_id required for project scope")


@router.post("/list")
async def list_user_skills(request: Request, body: UserSkillsListRequest) -> Response[DualTreeViewSchema]:
    """列出当前 surface 的用户/项目技能双树"""
    user_id: int = request.state.user_id
    if body.scope == SkillScope.PROJECT:
        _require_project_id(body.scope, body.project_id)
    project_id = body.project_id
    if body.scope == SkillScope.USER:
        project_id = None
    view = await user_skill_service.list_dual_tree(
        user_id,
        body.surface,
        project_id,
        body.enabled,
    )
    if body.scope == SkillScope.PROJECT:
        from app.server.skills.domain.models import DualTreeView, SkillTreeView

        view = DualTreeView(user=SkillTreeView(dirs=(), files=()), project=view.project)
    return Response(data=dual_tree_to_schema(view))


@router.post("/get")
async def get_user_skill(request: Request, body: UserSkillsGetRequest) -> Response[SkillFileDetailSchema]:
    """读取单个技能文件正文"""
    user_id: int = request.state.user_id
    _require_project_id(body.scope, body.project_id)
    detail = await user_skill_service.get_file(
        user_id,
        body.surface,
        body.scope,
        body.path,
        body.project_id,
    )
    return Response(data=file_detail_to_schema(detail))


@router.post("/mkdir")
async def mkdir_user_skill(request: Request, body: UserSkillsMkdirRequest) -> Response[DualTreeViewSchema]:
    """创建技能目录"""
    user_id: int = request.state.user_id
    _require_project_id(body.scope, body.project_id)
    view = await user_skill_service.mkdir(
        user_id,
        body.surface,
        body.scope,
        body.path,
        body.project_id,
    )
    return Response(data=dual_tree_to_schema(view))


@router.post("/write")
async def write_user_skill(request: Request, body: UserSkillsWriteRequest) -> Response[DualTreeViewSchema]:
    """创建或覆盖技能文件"""
    user_id: int = request.state.user_id
    _require_project_id(body.scope, body.project_id)
    view = await user_skill_service.write_file(
        user_id,
        body.surface,
        body.scope,
        body.path,
        body.name,
        body.content,
        body.description,
        body.revision,
        body.project_id,
        body.id,
    )
    return Response(data=dual_tree_to_schema(view))


@router.post("/move")
async def move_user_skill(request: Request, body: UserSkillsMoveRequest) -> Response[DualTreeViewSchema]:
    """移动技能路径"""
    user_id: int = request.state.user_id
    _require_project_id(body.scope, body.project_id)
    view = await user_skill_service.move_path(
        user_id,
        body.surface,
        body.scope,
        body.from_path,
        body.to_path,
        body.revision,
        body.project_id,
    )
    return Response(data=dual_tree_to_schema(view))


@router.post("/remove")
async def remove_user_skill(request: Request, body: UserSkillsRemoveRequest) -> Response[DualTreeViewSchema]:
    """删除技能路径"""
    user_id: int = request.state.user_id
    _require_project_id(body.scope, body.project_id)
    view = await user_skill_service.remove_path(
        user_id,
        body.surface,
        body.scope,
        body.path,
        body.revision,
        body.project_id,
    )
    return Response(data=dual_tree_to_schema(view))


@router.post("/set-enabled")
async def set_user_skill_enabled(
    request: Request,
    body: UserSkillsSetEnabledRequest,
) -> Response[DualTreeViewSchema]:
    """切换技能启用状态"""
    user_id: int = request.state.user_id
    _require_project_id(body.scope, body.project_id)
    view = await user_skill_service.set_enabled(
        user_id,
        body.surface,
        body.scope,
        body.path,
        body.enabled,
        body.revision,
        body.project_id,
    )
    return Response(data=dual_tree_to_schema(view))
