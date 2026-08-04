from datetime import datetime, timezone

from tortoise import fields
from tortoise.models import Model


class WorkshopStringIdModel(Model):
    """工坊字符串主键模型基类"""

    id = fields.CharField(max_length=64, primary_key=True)
    created_at = fields.DatetimeField(default=lambda: datetime.now(timezone.utc))
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        abstract = True


class WorkshopProjects(WorkshopStringIdModel):
    """工坊项目表"""

    user_id = fields.BigIntField(null=False)
    name = fields.CharField(max_length=255, null=False)
    group_chat_id = fields.BigIntField(null=False, unique=True)
    brief: fields.JSONField[dict[str, object]] = fields.JSONField(
        null=False, default=dict
    )

    class Meta(WorkshopStringIdModel.Meta):
        table = "workshop_projects"
        abstract = False
        indexes = [("user_id", "updated_at")]


class WorkshopExperts(WorkshopStringIdModel):
    """工坊专家名册表"""

    project_id = fields.CharField(max_length=64, null=False)
    name = fields.CharField(max_length=255, null=False)
    kind = fields.CharField(max_length=16, null=False)
    preset_key = fields.CharField(max_length=64, null=True)
    source_preset_key = fields.CharField(max_length=64, null=True)

    class Meta(WorkshopStringIdModel.Meta):
        table = "workshop_experts"
        abstract = False
        indexes = [("project_id", "created_at")]


class WorkshopTaskProposals(WorkshopStringIdModel):
    """待用户确认的立任务提议表"""

    project_id = fields.CharField(max_length=64, null=False)
    title = fields.CharField(max_length=255, null=False)
    goals: fields.JSONField[list[str]] = fields.JSONField(null=False)
    required_artifacts: fields.JSONField[list[str]] = fields.JSONField(
        null=False, default=list
    )
    status = fields.CharField(max_length=16, null=False)

    class Meta(WorkshopStringIdModel.Meta):
        table = "workshop_task_proposals"
        abstract = False
        indexes = [("project_id", "status")]


class WorkshopExpertProposals(WorkshopStringIdModel):
    """待用户确认的定制专家提议表"""

    project_id = fields.CharField(max_length=64, null=False)
    name = fields.CharField(max_length=255, null=False)
    kind = fields.CharField(max_length=16, null=False)
    source_preset_key = fields.CharField(max_length=64, null=True)
    status = fields.CharField(max_length=16, null=False)

    class Meta(WorkshopStringIdModel.Meta):
        table = "workshop_expert_proposals"
        abstract = False
        indexes = [("project_id", "status")]


class WorkshopTasks(WorkshopStringIdModel):
    """工坊任务表"""

    project_id = fields.CharField(max_length=64, null=False)
    title = fields.CharField(max_length=255, null=False)
    goals: fields.JSONField[list[str]] = fields.JSONField(null=False)
    required_artifacts: fields.JSONField[list[str]] = fields.JSONField(
        null=False, default=list
    )
    status = fields.CharField(max_length=32, null=False)
    schedule_id = fields.CharField(max_length=64, null=True)
    schedule_authorized = fields.BooleanField(null=False, default=False)
    external_auth: fields.JSONField[list[str]] = fields.JSONField(
        null=False, default=list
    )
    revision = fields.BigIntField(null=False, default=1)

    class Meta(WorkshopStringIdModel.Meta):
        table = "workshop_tasks"
        abstract = False
        indexes = [
            ("project_id", "status", "updated_at"),
            ("project_id", "created_at"),
        ]


class WorkshopTaskExperts(Model):
    """任务与专家分配关系表"""

    id = fields.BigIntField(primary_key=True)
    project_id = fields.CharField(max_length=64, null=False)
    task_id = fields.CharField(max_length=64, null=False)
    expert_id = fields.CharField(max_length=64, null=False)
    created_at = fields.DatetimeField(default=lambda: datetime.now(timezone.utc))

    class Meta:
        table = "workshop_task_experts"
        unique_together = (("task_id", "expert_id"),)
        indexes = [("project_id", "task_id")]


class WorkshopTaskCapabilityUses(Model):
    """任务实际使用过的工具能力记录表"""

    id = fields.BigIntField(primary_key=True)
    project_id = fields.CharField(max_length=64, null=False)
    task_id = fields.CharField(max_length=64, null=False)
    capability = fields.CharField(max_length=64, null=False)
    created_at = fields.DatetimeField(default=lambda: datetime.now(timezone.utc))

    class Meta:
        table = "workshop_task_capability_uses"
        unique_together = (("task_id", "capability"),)
        indexes = [("task_id", "created_at")]


class WorkshopWorkflows(WorkshopStringIdModel):
    """工坊工作流定义表；steps JSONB 存 DAG definition 文档"""

    project_id = fields.CharField(max_length=64, null=False)
    name = fields.CharField(max_length=255, null=False)
    steps: fields.JSONField[dict[str, object] | list[object]] = fields.JSONField(
        null=False
    )
    status = fields.CharField(max_length=16, null=False)
    source = fields.CharField(max_length=16, null=False)
    revision = fields.BigIntField(null=False, default=1)

    class Meta(WorkshopStringIdModel.Meta):
        table = "workshop_workflows"
        abstract = False
        indexes = [("project_id", "status", "updated_at")]


class WorkshopWorkflowRuns(WorkshopStringIdModel):
    """工作流任务运行记录表"""

    project_id = fields.CharField(max_length=64, null=False)
    workflow_id = fields.CharField(max_length=64, null=False)
    workflow_revision = fields.BigIntField(null=False)
    schedule_id = fields.CharField(max_length=64, null=True)
    trigger = fields.CharField(max_length=16, null=False)
    status = fields.CharField(max_length=16, null=False)
    current_node_id = fields.CharField(max_length=128, null=True)
    error_message = fields.TextField(null=True)
    started_at = fields.DatetimeField(null=True)
    finished_at = fields.DatetimeField(null=True)
    revision = fields.BigIntField(null=False, default=1)

    class Meta(WorkshopStringIdModel.Meta):
        table = "workshop_workflow_runs"
        abstract = False
        indexes = [
            ("project_id", "created_at"),
            ("project_id", "workflow_id", "created_at"),
            ("status", "created_at"),
        ]


class WorkshopSchedules(WorkshopStringIdModel):
    """工坊定时定义表"""

    project_id = fields.CharField(max_length=64, null=False)
    workflow_id = fields.CharField(max_length=64, null=False)
    cron = fields.CharField(max_length=128, null=False)
    timezone = fields.CharField(max_length=64, null=False)
    enabled = fields.BooleanField(null=False, default=True)
    authorized_at = fields.DatetimeField(null=False)
    authorized_external_capabilities: fields.JSONField[list[str]] = fields.JSONField(
        null=False, default=list
    )
    next_run_at = fields.DatetimeField(null=True)

    class Meta(WorkshopStringIdModel.Meta):
        table = "workshop_schedules"
        abstract = False
        indexes = [("enabled", "next_run_at"), ("project_id", "updated_at")]


class WorkshopScheduleRuns(Model):
    """工坊定时触发幂等运行表（追加声明，无更新）"""

    id = fields.CharField(max_length=64, primary_key=True)
    project_id = fields.CharField(max_length=64, null=False)
    schedule_id = fields.CharField(max_length=64, null=False)
    trigger_key = fields.CharField(max_length=128, null=False)
    task_id = fields.CharField(max_length=64, null=False)
    created_at = fields.DatetimeField(default=lambda: datetime.now(timezone.utc))

    class Meta:
        table = "workshop_schedule_runs"
        unique_together = (("schedule_id", "trigger_key"),)
        indexes = [("project_id", "created_at")]


class WorkshopArtifacts(WorkshopStringIdModel):
    """工坊产物索引表"""

    project_id = fields.CharField(max_length=64, null=False)
    task_id = fields.CharField(max_length=64, null=True)
    name = fields.CharField(max_length=512, null=False)
    storage_type = fields.CharField(max_length=16, null=False)
    storage_key = fields.CharField(max_length=1024, null=False)
    size_bytes = fields.BigIntField(null=True)
    metadata: fields.JSONField[dict[str, object]] = fields.JSONField(
        null=False, default=dict
    )

    class Meta(WorkshopStringIdModel.Meta):
        table = "workshop_artifacts"
        abstract = False
        indexes = [("project_id", "created_at"), ("task_id", "created_at")]


class WorkshopEvents(Model):
    """工坊追加式事件表"""

    id = fields.BigIntField(primary_key=True)
    event_key = fields.CharField(max_length=64, null=False, unique=True)
    project_id = fields.CharField(max_length=64, null=False)
    task_id = fields.CharField(max_length=64, null=True)
    kind = fields.CharField(max_length=64, null=False)
    payload: fields.JSONField[dict[str, object]] = fields.JSONField(null=False)
    created_at = fields.DatetimeField(default=lambda: datetime.now(timezone.utc))

    class Meta:
        table = "workshop_events"
        indexes = [("project_id", "created_at"), ("task_id", "created_at")]


class WorkshopRoomMembers(Model):
    """工坊群聊房间在场专家表"""

    id = fields.BigIntField(primary_key=True)
    project_id = fields.CharField(max_length=64, null=False)
    expert_id = fields.CharField(max_length=64, null=False)
    created_at = fields.DatetimeField(default=lambda: datetime.now(timezone.utc))

    class Meta:
        table = "workshop_room_members"
        unique_together = (("project_id", "expert_id"),)
        indexes = [("project_id", "created_at")]
