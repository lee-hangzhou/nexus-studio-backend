from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.server.api.v1.router import api_router
from app.server.chat.domain.enums import ChatConversationStatus
from app.server.chat.persistence.conversations import ChatConversations
from app.server.exceptions.codes import ErrorCode
from app.server.exceptions.handlers import register_exception_handlers
from app.server.infra.config import settings
from app.server.infra.database import db
from app.server.infra.security import create_access_token
from app.server.middleware import JWTAuthMiddleware
from app.server.workshop.domain.enums import WorkshopTaskStatus, WorkshopWorkflowStatus
from app.server.workshop.persistence.models import WorkshopProjects


def _workshop_test_app() -> FastAPI:
    """构造无 lifespan 的测试应用，仅挂载鉴权与 API 路由"""
    application = FastAPI()
    application.add_middleware(JWTAuthMiddleware, whitelist=set(), whitelist_prefixes=[])
    register_exception_handlers(application)
    application.include_router(api_router, prefix=settings.API_V1_PREFIX)
    return application


@asynccontextmanager
async def workshop_http_client(*, user_id: int = 1) -> AsyncIterator[tuple[AsyncClient, int, list[str], list[int]]]:
    """装配带鉴权的 Workshop HTTP 客户端，并登记清理资源"""
    await db.connect()
    app = _workshop_test_app()
    transport = ASGITransport(app=app)
    token = create_access_token(subject=user_id)
    project_ids: list[str] = []
    chat_ids: list[int] = []
    suffix = uuid4().hex
    chat = await ChatConversations.create(
        user_id=user_id,
        title=f"workshop-http-{suffix}",
        default_model="test",
        status=ChatConversationStatus.ACTIVE,
    )
    chat_ids.append(int(chat.id))
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        try:
            yield client, int(chat.id), project_ids, chat_ids
        finally:
            for project_id in project_ids:
                await WorkshopProjects.filter(id=project_id, user_id=user_id).delete()
            for chat_id in chat_ids:
                await ChatConversations.filter(id=chat_id, user_id=user_id).delete()
            await db.disconnect()


async def _post(client: AsyncClient, path: str, payload: dict[str, Any]) -> Any:
    """发起 workshop API POST 并返回 JSON"""
    response = await client.post(f"/api/v1/workshop{path}", json=payload)
    return response


@pytest.mark.integration
async def test_workshop_routes_require_auth() -> None:
    """未鉴权访问 workshop 路由应失败"""
    app = _workshop_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/workshop/projects/get",
            json={"project_id": "wp_missing"},
        )
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == int(ErrorCode.INVALID_TOKEN)


@pytest.mark.integration
async def test_workshop_http_create_get_upgrade_task_workflow_schedule_path() -> None:
    """覆盖创建/读取/升级/任务/工作流/定时成功路径"""
    async with workshop_http_client() as (client, group_chat_id, project_ids, chat_ids):
        create_resp = await _post(
            client,
            "/projects/create",
            {"name": "投放工坊", "group_chat_id": group_chat_id},
        )
        assert create_resp.status_code == 200
        create_body = create_resp.json()
        assert create_body["code"] == 0
        project_id = create_body["data"]["id"]
        project_ids.append(project_id)
        assert create_body["data"]["group_chat_id"] == group_chat_id

        get_resp = await _post(client, "/projects/get", {"project_id": project_id})
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["id"] == project_id

        upgrade_chat = await ChatConversations.create(
            user_id=1,
            title=f"workshop-upgrade-{uuid4().hex}",
            default_model="test",
            status=ChatConversationStatus.ACTIVE,
        )
        chat_ids.append(int(upgrade_chat.id))
        upgrade_resp = await _post(
            client,
            "/projects/upgrade",
            {
                "group_chat_id": int(upgrade_chat.id),
                "project_name": "升级工坊",
                "carried_message_count": 2,
            },
        )
        assert upgrade_resp.status_code == 200
        upgrade_body = upgrade_resp.json()["data"]
        project_ids.append(upgrade_body["project"]["id"])
        assert upgrade_body.get("pending_task_proposal") is None

        propose_resp = await _post(
            client,
            "/tasks/propose",
            {
                "project_id": project_id,
                "title": "周报",
                "goals": ["汇总数据"],
                "required_artifacts": ["report"],
            },
        )
        assert propose_resp.status_code == 200
        proposal_id = propose_resp.json()["data"]["id"]
        confirm_resp = await _post(
            client,
            "/tasks/confirm",
            {"project_id": project_id, "proposal_id": proposal_id},
        )
        assert confirm_resp.status_code == 200
        assert confirm_resp.json()["data"]["status"] == WorkshopTaskStatus.ALIGNING.value

        draft_resp = await _post(
            client,
            "/workflows/user-draft",
            {
                "project_id": project_id,
                "name": "日报流",
                "steps": [{"title": "collect", "required_artifact_names": [], "external_capabilities": []}],
            },
        )
        assert draft_resp.status_code == 200
        workflow_id = draft_resp.json()["data"]["id"]
        confirm_wf = await _post(
            client,
            "/workflows/confirm",
            {"project_id": project_id, "workflow_id": workflow_id},
        )
        assert confirm_wf.status_code == 200
        assert confirm_wf.json()["data"]["status"] == WorkshopWorkflowStatus.SAVED.value

        schedule_resp = await _post(
            client,
            "/schedules/create",
            {
                "project_id": project_id,
                "workflow_id": workflow_id,
                "cron": "0 9 * * *",
                "timezone": "Asia/Shanghai",
                "authorized_capabilities": [],
            },
        )
        assert schedule_resp.status_code == 200
        schedule_id = schedule_resp.json()["data"]["id"]
        trigger_resp = await _post(
            client,
            "/schedules/trigger",
            {
                "project_id": project_id,
                "schedule_id": schedule_id,
                "trigger_key": f"manual-{uuid4().hex}",
            },
        )
        assert trigger_resp.status_code == 200
        trigger_body = trigger_resp.json()["data"]
        assert trigger_body["run"]["schedule_id"] == schedule_id
        assert trigger_body["task"]["schedule_id"] == schedule_id


@pytest.mark.integration
async def test_workshop_http_room_members_task_get_and_reject_done() -> None:
    """覆盖房间成员、任务读取与弱验收驳回路径"""
    async with workshop_http_client() as (client, group_chat_id, project_ids, _chat_ids):
        create_resp = await _post(
            client,
            "/projects/create",
            {
                "name": "UI 缺口",
                "group_chat_id": group_chat_id,
                "initial_expert_keys": ["ecom_market_competitor_advisor"],
            },
        )
        assert create_resp.status_code == 200
        project_id = create_resp.json()["data"]["id"]
        project_ids.append(project_id)

        roster_resp = await _post(client, "/roster/list", {"project_id": project_id})
        assert roster_resp.status_code == 200
        expert = next(
            item
            for item in roster_resp.json()["data"]["items"]
            if item.get("preset_key") == "ecom_market_competitor_advisor"
        )
        invite_resp = await _post(
            client,
            "/roster/invite",
            {"project_id": project_id, "expert_id": expert["id"]},
        )
        assert invite_resp.status_code == 200
        members_resp = await _post(
            client, "/roster/room-members", {"project_id": project_id}
        )
        assert members_resp.status_code == 200
        assert expert["id"] in members_resp.json()["data"]["expert_ids"]

        propose_resp = await _post(
            client,
            "/tasks/propose",
            {
                "project_id": project_id,
                "title": "验收任务",
                "goals": ["cover A"],
                "required_artifacts": ["report.md"],
            },
        )
        proposal_id = propose_resp.json()["data"]["id"]
        confirm_resp = await _post(
            client,
            "/tasks/confirm",
            {"project_id": project_id, "proposal_id": proposal_id},
        )
        task_id = confirm_resp.json()["data"]["id"]

        get_resp = await _post(
            client,
            "/tasks/get",
            {"project_id": project_id, "task_id": task_id},
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["id"] == task_id
        assert get_resp.json()["data"]["status"] == WorkshopTaskStatus.ALIGNING.value

        for path in ("/tasks/propose-go", "/tasks/confirm-go", "/tasks/begin", "/tasks/review"):
            step = await _post(client, path, {"project_id": project_id, "task_id": task_id})
            assert step.status_code == 200, path

        accept_resp = await _post(
            client,
            "/tasks/weak-accept",
            {
                "project_id": project_id,
                "task_id": task_id,
                "covered_goals": ["cover A"],
                "artifacts": [
                    {
                        "name": "report.md",
                        "storage_type": "db",
                        "content": "hello",
                    }
                ],
            },
        )
        assert accept_resp.status_code == 200
        assert accept_resp.json()["data"]["passed"] is True

        done_get = await _post(
            client,
            "/tasks/get",
            {"project_id": project_id, "task_id": task_id},
        )
        assert done_get.json()["data"]["status"] == WorkshopTaskStatus.DONE.value

        reject_resp = await _post(
            client,
            "/tasks/reject-done",
            {"project_id": project_id, "task_id": task_id},
        )
        assert reject_resp.status_code == 200
        assert reject_resp.json()["data"]["status"] == WorkshopTaskStatus.RE_ALIGNING.value

        reject_again = await _post(
            client,
            "/tasks/reject-done",
            {"project_id": project_id, "task_id": task_id},
        )
        assert reject_again.status_code == 400
        assert reject_again.json()["code"] == int(ErrorCode.INVALID_PARAMS)


@pytest.mark.integration
async def test_workshop_http_ownership_and_conflict_mapping() -> None:
    """所有权失败与非法迁移映射为可区分错误码，禁止假成功"""
    async with workshop_http_client(user_id=1) as (client, group_chat_id, project_ids, _chat_ids):
        create_resp = await _post(
            client,
            "/projects/create",
            {"name": "归属测试", "group_chat_id": group_chat_id},
        )
        assert create_resp.status_code == 200
        project_id = create_resp.json()["data"]["id"]
        project_ids.append(project_id)

        other_token = create_access_token(subject=999001)
        foreign = await client.post(
            "/api/v1/workshop/projects/get",
            headers={"Authorization": f"Bearer {other_token}"},
            json={"project_id": project_id},
        )
        assert foreign.status_code == 404
        assert foreign.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)

        propose_resp = await _post(
            client,
            "/tasks/propose",
            {
                "project_id": project_id,
                "title": "任务",
                "goals": ["g1"],
            },
        )
        proposal_id = propose_resp.json()["data"]["id"]
        confirm_resp = await _post(
            client,
            "/tasks/confirm",
            {"project_id": project_id, "proposal_id": proposal_id},
        )
        task_id = confirm_resp.json()["data"]["id"]

        begin_resp = await _post(
            client,
            "/tasks/begin",
            {"project_id": project_id, "task_id": task_id},
        )
        assert begin_resp.status_code == 400
        assert begin_resp.json()["code"] == int(ErrorCode.INVALID_PARAMS)
        assert begin_resp.json()["data"] is None or begin_resp.json()["code"] != 0

        missing_task = await _post(
            client,
            "/tasks/begin",
            {"project_id": project_id, "task_id": "missing-task"},
        )
        assert missing_task.status_code == 404
        assert missing_task.json()["code"] == int(ErrorCode.TASK_NOT_FOUND)

    app = _workshop_test_app()
    workshop_paths = {
        getattr(route, "path", "")
        for route in app.routes
        if "workshop" in getattr(route, "path", "")
    }
    assert "/api/v1/workshop/roster/room-members" in workshop_paths
    assert "/api/v1/workshop/tasks/get" in workshop_paths
    assert "/api/v1/workshop/tasks/reject-done" in workshop_paths
    assert "/api/v1/workshop/roster/decline-custom" in workshop_paths
    assert "/api/v1/workshop/schedules/get" in workshop_paths
    assert "/api/v1/workshop/schedules/list" in workshop_paths
    assert "/api/v1/workshop/schedules/enable" in workshop_paths
    assert "/api/v1/workshop/schedules/disable" in workshop_paths
    assert "/api/v1/workshop/schedules/tick" not in workshop_paths
    assert "/api/v1/workshop/tasks/attribute-message" not in workshop_paths
    assert "/api/v1/workshop/tasks/fail-review-blocked" not in workshop_paths
    assert "/api/v1/workshop/tasks/confirm-create" not in workshop_paths
    assert not any(path.endswith("/tick") for path in workshop_paths)


@pytest.mark.integration
async def test_workshop_http_duplicate_group_chat_and_foreign_chat_mapping() -> None:
    """重复群聊与越权群聊映射为 WORKSHOP_CONFLICT / RESOURCE_NOT_FOUND，非 500"""
    async with workshop_http_client(user_id=1) as (client, group_chat_id, project_ids, chat_ids):
        first = await _post(
            client,
            "/projects/create",
            {"name": "一号", "group_chat_id": group_chat_id},
        )
        assert first.status_code == 200
        project_ids.append(first.json()["data"]["id"])

        dup = await _post(
            client,
            "/projects/create",
            {"name": "二号", "group_chat_id": group_chat_id},
        )
        assert dup.status_code == 409
        assert dup.json()["code"] == int(ErrorCode.WORKSHOP_CONFLICT)

        foreign_chat = await ChatConversations.create(
            user_id=777001,
            title=f"foreign-{uuid4().hex}",
            default_model="test",
            status=ChatConversationStatus.ACTIVE,
        )
        chat_ids.append(int(foreign_chat.id))
        bad = await _post(
            client,
            "/projects/create",
            {"name": "越权", "group_chat_id": int(foreign_chat.id)},
        )
        assert bad.status_code == 404
        assert bad.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)


@pytest.mark.integration
async def test_workshop_http_read_list_endpoints() -> None:
    """覆盖项目/任务/产物/数据源/authorized_operations 只读列表端点"""
    async with workshop_http_client() as (client, group_chat_id, project_ids, _chat_ids):
        create_resp = await _post(
            client,
            "/projects/create",
            {
                "name": "列表测试",
                "group_chat_id": group_chat_id,
                "initial_expert_keys": ["ecom_listing_planner_executor"],
            },
        )
        assert create_resp.status_code == 200
        project_id = create_resp.json()["data"]["id"]
        project_ids.append(project_id)

        list_projects_resp = await client.post("/api/v1/workshop/projects/list")
        assert list_projects_resp.status_code == 200
        listed_ids = [item["id"] for item in list_projects_resp.json()["data"]["items"]]
        assert project_id in listed_ids

        propose_resp = await _post(
            client,
            "/tasks/propose",
            {
                "project_id": project_id,
                "title": "列表任务",
                "goals": ["cover list"],
                "required_artifacts": ["report.md"],
            },
        )
        assert propose_resp.status_code == 200
        proposal_id = propose_resp.json()["data"]["id"]

        pending_resp = await _post(
            client,
            "/tasks/pending-proposals",
            {"project_id": project_id},
        )
        assert pending_resp.status_code == 200
        pending_ids = [item["id"] for item in pending_resp.json()["data"]["items"]]
        assert proposal_id in pending_ids

        confirm_resp = await _post(
            client,
            "/tasks/confirm",
            {"project_id": project_id, "proposal_id": proposal_id},
        )
        assert confirm_resp.status_code == 200
        task_id = confirm_resp.json()["data"]["id"]

        tasks_resp = await _post(
            client,
            "/tasks/list",
            {"project_id": project_id},
        )
        assert tasks_resp.status_code == 200
        task_ids = [item["id"] for item in tasks_resp.json()["data"]["items"]]
        assert task_id in task_ids

        auth_ops_resp = await _post(
            client,
            "/tasks/authorized-operations",
            {"project_id": project_id},
        )
        assert auth_ops_resp.status_code == 200
        assert auth_ops_resp.json()["data"]["items"] == []

        assignments_resp = await _post(
            client,
            "/tasks/assignments",
            {"project_id": project_id},
        )
        assert assignments_resp.status_code == 200
        assert isinstance(assignments_resp.json()["data"]["items"], list)

        data_sources_resp = await _post(
            client,
            "/ecommerce/data-sources",
            {"project_id": project_id},
        )
        assert data_sources_resp.status_code == 200
        data_sources_body = data_sources_resp.json()["data"]
        assert data_sources_body["shop"] is None
        assert data_sources_body["sources"] == []

        import_errors_resp = await _post(
            client,
            "/ecommerce/import-errors",
            {"project_id": project_id},
        )
        assert import_errors_resp.status_code == 200
        assert import_errors_resp.json()["data"]["items"] == []

        for path in ("/tasks/propose-go", "/tasks/confirm-go", "/tasks/begin", "/tasks/review"):
            step = await _post(client, path, {"project_id": project_id, "task_id": task_id})
            assert step.status_code == 200, path

        accept_resp = await _post(
            client,
            "/tasks/weak-accept",
            {
                "project_id": project_id,
                "task_id": task_id,
                "covered_goals": ["cover list"],
                "artifacts": [
                    {
                        "name": "report.md",
                        "storage_type": "db",
                        "content": "artifact-body",
                    }
                ],
            },
        )
        assert accept_resp.status_code == 200
        assert accept_resp.json()["data"]["passed"] is True

        artifacts_resp = await _post(
            client,
            "/artifacts/list",
            {"project_id": project_id, "task_id": task_id},
        )
        assert artifacts_resp.status_code == 200
        artifacts = artifacts_resp.json()["data"]["items"]
        assert len(artifacts) == 1
        assert artifacts[0]["name"] == "report.md"
        assert artifacts[0]["content"] == "artifact-body"
        assert artifacts[0]["storage_type"] == "db"


@pytest.mark.integration
async def test_workshop_http_get_by_chat_and_upgrade() -> None:
    """get-by-chat 区分工坊/普通群聊；upgrade 用 initial_expert_keys 预置专家"""
    async with workshop_http_client() as (client, group_chat_id, project_ids, chat_ids):
        create_resp = await _post(
            client,
            "/projects/create",
            {"name": "聊天绑定", "group_chat_id": group_chat_id},
        )
        assert create_resp.status_code == 200
        project_id = create_resp.json()["data"]["id"]
        project_ids.append(project_id)

        by_chat_resp = await _post(
            client,
            "/projects/get-by-chat",
            {"group_chat_id": group_chat_id},
        )
        assert by_chat_resp.status_code == 200
        by_chat_body = by_chat_resp.json()["data"]
        assert by_chat_body["project"] is not None
        assert by_chat_body["project"]["id"] == project_id

        random_chat = await ChatConversations.create(
            user_id=1,
            title=f"workshop-random-{uuid4().hex}",
            default_model="test",
            status=ChatConversationStatus.ACTIVE,
        )
        chat_ids.append(int(random_chat.id))
        random_resp = await _post(
            client,
            "/projects/get-by-chat",
            {"group_chat_id": int(random_chat.id)},
        )
        assert random_resp.status_code == 200
        assert random_resp.json()["data"]["project"] is None

        upgrade_chat = await ChatConversations.create(
            user_id=1,
            title=f"workshop-ecom-upgrade-{uuid4().hex}",
            default_model="test",
            status=ChatConversationStatus.ACTIVE,
        )
        chat_ids.append(int(upgrade_chat.id))
        upgrade_resp = await _post(
            client,
            "/projects/upgrade",
            {
                "group_chat_id": int(upgrade_chat.id),
                "project_name": "电商升级",
                "seed_goal": "完成首版店铺诊断",
                "carried_message_count": 0,
                "initial_expert_keys": [
                    "ecom_ops_analytics_executor",
                    "ecom_taobao_store_ops_executor",
                ],
            },
        )
        assert upgrade_resp.status_code == 200
        upgrade_body = upgrade_resp.json()["data"]
        project_ids.append(upgrade_body["project"]["id"])
        assert "pack" not in upgrade_body["project"]
        assert upgrade_body["project"]["name"] == "电商升级"
