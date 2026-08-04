"""工作流节点专家执行：挂载专家执行面并验收命名产物。

异步执行平面：不写 group_chat / chat_messages；与交互 Turn 解耦。
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from app.agent.workshop.turn.orchestrator import stream_workshop_turn
from app.contracts.workshop import WorkshopTurnTarget
from app.server.infra.logger import logger
from app.server.workshop.domain.enums import WorkshopArtifactStorageType
from app.server.workshop.domain.presets import get_preset
from app.server.workshop.domain.workflow_executor import (
    NodeRunContext,
    ProducedArtifact,
)
from app.server.workshop.persistence.repository import WorkshopRepository
from app.server.workshop.services.task_orchestrator import WorkshopTaskOrchestrator
from app.server.workshop.services.workshop_project_service import WorkshopProjectService


class ExpertNodeRunner:
    """用现有专家 mount 执行节点；工作区文件经 OSS 收口为可持久化产物。"""

    def __init__(
        self,
        *,
        projects: WorkshopProjectService,
        orchestrator: WorkshopTaskOrchestrator,
        repository: WorkshopRepository,
    ) -> None:
        """注入项目、任务编排与仓储"""
        self._projects = projects
        self._orchestrator = orchestrator
        self._repository = repository

    async def run_node(self, ctx: NodeRunContext) -> list[ProducedArtifact]:
        """确保专家在场后跑执行面，再收口工作区文件产物"""
        model_key = ctx.model_key.strip()
        if not model_key:
            raise RuntimeError("workflow model_key required")

        user_id = await self._repository.get_project_owner_user_id(
            project_id=ctx.project_id
        )
        project = await self._projects.get_project(
            project_id=ctx.project_id, user_id=user_id
        )

        preset = get_preset(ctx.node.assignee.preset_key)
        expert = await self._projects.add_preset_to_roster(
            project_id=ctx.project_id,
            user_id=user_id,
            preset_key=preset.key,
        )
        await self._projects.invite_to_room(
            project_id=ctx.project_id,
            user_id=user_id,
            expert_id=expert.id,
        )

        content = self._build_prompt(ctx)
        cancel_event = asyncio.Event()
        turn_id = f"wf-run-{ctx.run_id}-{ctx.node.id}-{uuid4().hex[:8]}"
        chunks: list[str] = []
        async for chunk in stream_workshop_turn(
            projects=self._projects,
            orchestrator=self._orchestrator,
            project=project,
            user_id=user_id,
            conversation_id=project.group_chat_id,
            turn_id=turn_id,
            content=content,
            model_key=model_key,
            enable_tools=True,
            cancel_event=cancel_event,
            turn_target=WorkshopTurnTarget(
                expert_id=expert.id,
                task_id=ctx.run_id,
                speaker_role="expert",
                persist_user_message=False,
                persist_chat_messages=False,
            ),
            persist_user_message=False,
        ):
            chunks.append(chunk)

        logger.info(
            "workshop.workflow_node.turn_finished",
            run_id=ctx.run_id,
            node_id=ctx.node.id,
            chunk_count=len(chunks),
        )
        existing = await self._repository.list_artifacts_for_project(
            project_id=ctx.project_id,
            user_id=user_id,
            task_id=ctx.run_id,
        )
        before_names = {item.name for item in existing}
        await self._harvest_workspace_outputs(ctx=ctx, user_id=user_id)
        return await self._load_new_file_artifacts(
            ctx=ctx, user_id=user_id, before_names=before_names
        )

    def _build_prompt(self, ctx: NodeRunContext) -> str:
        """组装节点执行提示：指令、输入、最终文件交付契约"""
        out_dir = f"workflow_outputs/{ctx.run_id}"
        lines = [
            "你正在执行工坊工作流的一个异步节点（独立执行平面，非用户闲聊）。",
            f"节点：{ctx.node.title}（id={ctx.node.id}）",
            "",
            "任务说明：",
            ctx.node.instruction.strip(),
            "",
            "可用输入产物：",
        ]
        if not ctx.input_artifacts:
            lines.append("（无）")
        else:
            for name, artifact in ctx.input_artifacts.items():
                if artifact.storage_type is WorkshopArtifactStorageType.DB:
                    lines.append(f"### {name}\n{artifact.content}")
                else:
                    lines.append(
                        f"- {name}: storage_key={artifact.storage_key} "
                        f"size={artifact.size_bytes}"
                    )
        lines.append("")
        lines.append(
            f"整次任务成功标准：工作区目录 `{out_dir}/` 下最终须有至少一个非空交付文件"
            "（文件名自定，如 script_YYYYMMDD_HHMMSS.txt）；系统会将该目录文件上传对象存储并持久化。"
        )
        lines.append(
            "本节点可只产中间内容；结论勿只留在对话里，需要交付的内容请写入该目录下的文件。"
        )
        lines.append("完成后停止；不要向用户追问。")
        return "\n".join(lines)

    async def _harvest_workspace_outputs(
        self, *, ctx: NodeRunContext, user_id: int
    ) -> None:
        """把 run 输出目录文件上传 OSS 并登记为 oss 产物"""
        from app.agent.workshop.turn.persistence_subscriber import workshop_workspace
        from app.server.workshop.services.workflow_artifact_persist import (
            persist_workflow_output_files,
        )

        await persist_workflow_output_files(
            repository=self._repository,
            project_id=ctx.project_id,
            user_id=user_id,
            run_id=ctx.run_id,
            workspace_root=workshop_workspace(),
        )

    async def _load_new_file_artifacts(
        self,
        *,
        ctx: NodeRunContext,
        user_id: int,
        before_names: set[str],
    ) -> list[ProducedArtifact]:
        """只返回本节点新收口的文件类产物，避免多节点重复登记"""
        rows = await self._repository.list_artifacts_for_project(
            project_id=ctx.project_id,
            user_id=user_id,
            task_id=ctx.run_id,
        )
        produced: list[ProducedArtifact] = []
        for row in rows:
            if row.name in before_names:
                continue
            if row.storage_type is WorkshopArtifactStorageType.DB:
                if not (row.content and row.content.strip()):
                    continue
            produced.append(
                ProducedArtifact(
                    name=row.name,
                    storage_type=row.storage_type,
                    content=row.content or "",
                    storage_key=row.storage_key or "",
                    size_bytes=row.size_bytes,
                )
            )
        return produced
