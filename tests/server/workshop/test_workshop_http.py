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


def _sample_definition(
    *,
    title: str = "cover A",
    output_name: str = "report.md",
    required: bool = True,
) -> dict[str, Any]:
    """构造合法 DAG definition HTTP 载荷"""
    return {
        "nodes": [
            {
                "id": "n1",
                "title": title,
                "instruction": title,
                "assignee": {"preset_key": "ecom_market_competitor_advisor"},
                "inputs": [],
                "outputs": [
                    {
                        "name": output_name,
                        "storage_type": "db",
                        "required": required,
                    }
                ],
                "external_capabilities": [],
                "on_failure": "fail_run",
            }
        ],
        "edges": [],
    }


@asynccontextmanager
async def workshop_http_client(
    *, user_id: int = 1
) -> AsyncIterator[tuple[AsyncClient, int, list[str], list[int]]]:
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
    """发起 workshop API POST 并返回响应"""
    return await client.post(f"/api/v1/workshop{path}", json=payload)


async def _save_workflow(
    client: AsyncClient,
    *,
    project_id: str,
    name: str,
    title: str = "cover A",
    output_name: str = "report.md",
) -> str:
    """草稿并确认保存工作流，返回 workflow_id"""
    draft_resp = await _post(
        client,
        "/workflows/user-draft",
        {
            "project_id": project_id,
            "name": name,
            "model_key": "test-model",
            "definition": _sample_definition(title=title, output_name=output_name),
        },
    )
    assert draft_resp.status_code == 200, draft_resp.text
    workflow_id = draft_resp.json()["data"]["id"]
    confirm_wf = await _post(
        client,
        "/workflows/confirm",
        {"project_id": project_id, "workflow_id": workflow_id},
    )
    assert confirm_wf.status_code == 200, confirm_wf.text
    assert confirm_wf.json()["data"]["status"] == WorkshopWorkflowStatus.SAVED.value
    return workflow_id


async def _manual_run_task(
    client: AsyncClient, *, project_id: str, workflow_id: str
) -> str:
    """手动跑工作流，返回与 run 同 id 的壳任务 id"""
    run_resp = await _post(
        client,
        "/workflows/manual-run",
        {
            "project_id": project_id,
            "workflow_id": workflow_id,
            "authorized_capabilities": [],
        },
    )
    assert run_resp.status_code == 200, run_resp.text
    return run_resp.json()["data"]["run"]["id"]


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
    """覆盖创建/读取/升级/工作流/定时成功路径"""
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

        workflow_id = await _save_workflow(
            client, project_id=project_id, name="日报流", title="collect"
        )

        list_wf = await _post(client, "/workflows/list", {"project_id": project_id})
        assert list_wf.status_code == 200
        assert any(item["id"] == workflow_id for item in list_wf.json()["data"]["items"])

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

        workflow_id = await _save_workflow(
            client,
            project_id=project_id,
            name="验收流",
            title="cover A",
            output_name="report.md",
        )
        task_id = await _manual_run_task(
            client, project_id=project_id, workflow_id=workflow_id
        )

        get_resp = await _post(
            client,
            "/tasks/get",
            {"project_id": project_id, "task_id": task_id},
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["id"] == task_id
        assert get_resp.json()["data"]["status"] == WorkshopTaskStatus.EXECUTING.value

        review = await _post(
            client, "/tasks/review", {"project_id": project_id, "task_id": task_id}
        )
        assert review.status_code == 200

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
    async with workshop_http_client(user_id=1) as (
        client,
        group_chat_id,
        project_ids,
        _chat_ids,
    ):
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

        workflow_id = await _save_workflow(
            client, project_id=project_id, name="归属流", title="g1"
        )
        task_id = await _manual_run_task(
            client, project_id=project_id, workflow_id=workflow_id
        )

        # EXECUTING 上再 begin 之类的路径已移除；审查后合法，取消后不可再 reject-done
        cancel_resp = await _post(
            client,
            "/tasks/cancel",
            {"project_id": project_id, "task_id": task_id},
        )
        assert cancel_resp.status_code == 200

        reject_cancelled = await _post(
            client,
            "/tasks/reject-done",
            {"project_id": project_id, "task_id": task_id},
        )
        assert reject_cancelled.status_code == 400
        assert reject_cancelled.json()["code"] == int(ErrorCode.INVALID_PARAMS)

        missing_task = await _post(
            client,
            "/tasks/review",
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
    assert "/api/v1/workshop/workflows/manual-run" in workshop_paths
    assert "/api/v1/workshop/workflows/runs/list" in workshop_paths
    assert "/api/v1/workshop/tasks/propose" not in workshop_paths
    assert "/api/v1/workshop/tasks/propose-go" not in workshop_paths
    assert "/api/v1/workshop/tasks/confirm-go" not in workshop_paths
    assert "/api/v1/workshop/tasks/begin" not in workshop_paths
    assert "/api/v1/workshop/tasks/pending-proposals" not in workshop_paths
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
    """覆盖项目/任务/产物/工作流运行/数据源只读列表端点"""
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

        workflow_id = await _save_workflow(
            client,
            project_id=project_id,
            name="列表流",
            title="cover list",
            output_name="report.md",
        )
        task_id = await _manual_run_task(
            client, project_id=project_id, workflow_id=workflow_id
        )

        runs_resp = await _post(
            client,
            "/workflows/runs/list",
            {"project_id": project_id, "workflow_id": workflow_id},
        )
        assert runs_resp.status_code == 200
        run_ids = [item["id"] for item in runs_resp.json()["data"]["items"]]
        assert task_id in run_ids

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

        review = await _post(
            client, "/tasks/review", {"project_id": project_id, "task_id": task_id}
        )
        assert review.status_code == 200

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
            title=f"workshop-upgrade-keys-{uuid4().hex}",
            default_model="test",
            status=ChatConversationStatus.ACTIVE,
        )
        chat_ids.append(int(upgrade_chat.id))
        upgrade_resp = await _post(
            client,
            "/projects/upgrade",
            {
                "group_chat_id": int(upgrade_chat.id),
                "project_name": "带专家升级",
                "carried_message_count": 0,
                "initial_expert_keys": ["ecom_market_competitor_advisor"],
            },
        )
        assert upgrade_resp.status_code == 200
        upgraded = upgrade_resp.json()["data"]
        project_ids.append(upgraded["project"]["id"])
        assert upgraded.get("pending_task_proposal") is None
        roster_resp = await _post(
            client, "/roster/list", {"project_id": upgraded["project"]["id"]}
        )
        assert roster_resp.status_code == 200
        keys = {item["preset_key"] for item in roster_resp.json()["data"]["items"]}
        assert "ecom_market_competitor_advisor" in keys
