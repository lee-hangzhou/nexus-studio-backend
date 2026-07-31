from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, TypeAdapter

ROOT = Path(__file__).resolve().parents[1]
# 作为脚本直接执行时，先把仓库根目录加入模块搜索路径。
sys.path.insert(0, str(ROOT))

from app.contracts.canvas import (
    CanvasPatchOp,
    CanvasPatchRequest,
    CanvasPatchResponse,
    CanvasRevisionConflictItem,
    CanvasSessionCreateRequest,
    CanvasSessionIdRequest,
    CanvasSessionUpdateRequest,
    CanvasSessionView,
    CanvasSnapshot,
    CanvasToolPendingOperation,
    GenerationProgress,
    PendingCanvasPatchOperation,
    PendingGenerateOperation,
    PendingSkillWriteOperation,
)
from app.contracts.gateway import (
    GatewayGenerateCallback,
    GatewayImageSubmitRequest,
    GatewayModelsResponse,
    GatewayQueueResponse,
    GatewayTaskStatusResponse,
    GatewayTaskSubmitResponse,
    GatewayVideoSubmitRequest,
)
from app.contracts.ecommerce import (
    AdsReportDiagnosis,
    AdsStrategyBrief,
    AnomalyList,
    CampaignPlanDoc,
    ChartArtifact,
    CreativeBrief,
    DailyOrWeeklyReview,
    EmailSequenceDraft,
    GenerationAssetRef,
    ListingCopyVersion,
    MarketCompetitorBrief,
    MetricAvailability,
    MetricsSnapshot,
    PublishDiff,
    PublishReceipt,
    ShopConnectionPublic,
    SkuCostRecord,
    SkuRecord,
    TaobaoAdsDailyNormalized,
    TaobaoInventorySnapshotNormalized,
    TaobaoOrderLineNormalized,
    TaobaoProductDailyNormalized,
)
from app.contracts.generation import GenerateParamOptions
from app.contracts.projects import (
    EpisodeCreateRequest,
    EpisodeIdRequest,
    EpisodeListRequest,
    EpisodeListResponse,
    EpisodeUpdateRequest,
    EpisodeView,
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectDetailResponse,
    ProjectIdRequest,
    ProjectListRequest,
    ProjectListResponse,
    ProjectUpdateRequest,
    ProjectView,
)
from app.contracts.stream import StreamFrame
from app.contracts.turn_content import (
    TurnMediaBlock,
    TurnNodeBlock,
    TurnSkillBlock,
    TurnTextBlock,
    TurnUserInput,
)
from app.contracts.workshop import (
    ArtifactSubmissionView,
    WorkshopAssignTaskExpertRequest,
    WorkshopArtifactListRequest,
    WorkshopArtifactListResponse,
    WorkshopArtifactView,
    WorkshopAuthorizeOperationRequest,
    WorkshopAuthorizedOperationListResponse,
    WorkshopAuthorizedOperationView,
    WorkshopBeginShopAuthView,
    WorkshopCompleteScheduledRunRequest,
    WorkshopConfirmCustomExpertRequest,
    WorkshopConfirmTaskProposalRequest,
    WorkshopConfirmWorkflowRequest,
    ChatSelectedExpertView,
    ClearChatSelectedExpertRequest,
    ExpertDirectoryEntry,
    ExpertDirectoryResponse,
    WorkshopConnectorDirectoryResponse,
    WorkshopConnectorEntry,
    ExpertTeamDirectoryEntry,
    ExpertTeamDirectoryResponse,
    GetChatSelectedExpertRequest,
    SetChatSelectedExpertRequest,
    WorkshopCopyPresetRequest,
    WorkshopAddPresetRequest,
    WorkshopCreateProjectRequest,
    WorkshopCreateScheduleRequest,
    WorkshopCreateTaskProposalView,
    WorkshopDataSourcesView,
    WorkshopDataSourceStatus,
    WorkshopDeclineCustomExpertRequest,
    WorkshopDeclineTaskProposalRequest,
    WorkshopDraftWorkflowRequest,
    WorkshopEventListResponse,
    WorkshopEventView,
    WorkshopExpertProposalIdView,
    WorkshopGrantExternalAuthRequest,
    WorkshopGroupChatIdRequest,
    WorkshopImportErrorListResponse,
    WorkshopImportErrorView,
    WorkshopInviteDirectoryExpertView,
    WorkshopInviteDirectoryResponse,
    WorkshopInviteExpertRequest,
    WorkshopManualRunResultView,
    WorkshopManualRunWorkflowRequest,
    WorkshopOkView,
    WorkshopPendingProposalListResponse,
    WorkshopProjectByChatResponse,
    WorkshopProjectIdRequest,
    WorkshopProjectListResponse,
    WorkshopProjectView,
    WorkshopProposeCustomExpertRequest,
    WorkshopProposeTaskRequest,
    WorkshopRecordCapabilityUseRequest,
    WorkshopRemoveExpertRequest,
    WorkshopRoomMembersResponse,
    WorkshopRosterExpertView,
    WorkshopRosterListResponse,
    WorkshopScheduleIdRequest,
    WorkshopScheduleListResponse,
    WorkshopScheduleRunView,
    WorkshopScheduledCompletionResultView,
    WorkshopScheduleTriggerResultView,
    WorkshopScheduleView,
    WorkshopTaskAssignmentListResponse,
    WorkshopTaskAssignmentView,
    WorkshopTaskIdRequest,
    WorkshopTaskListResponse,
    WorkshopTaskView,
    WorkshopTriggerScheduleRequest,
    WorkshopUnassignTaskExpertRequest,
    WorkshopUpgradeRequest,
    WorkshopUpgradeFromTeamRequest,
    WorkshopUpgradeFromExpertRequest,
    WorkshopUpgradeResultView,
    WorkshopTeamSelectRequiresUpgradeView,
    WorkshopTurnTarget,
    WorkshopWakeDashboardView,
    WorkshopWeakAcceptRequest,
    WorkshopWeakAcceptResultView,
    WorkshopWorkflowListResponse,
    WorkshopWorkflowStepView,
    WorkshopWorkflowView,
)
from app.server.generation.schemas import (
    GenerateModelItem,
    GenerateTaskListRequest,
    GenerateTaskListResponse,
    GenerateTaskView,
    SubmitGenerateRequest,
)

SCHEMA_DIR = ROOT / "contracts" / "schema"

CanvasContracts = Annotated[
    CanvasSnapshot
    | CanvasPatchRequest
    | CanvasPatchResponse
    | CanvasRevisionConflictItem
    | GenerationProgress
    | CanvasPatchOp
    | CanvasSessionView
    | CanvasSessionCreateRequest
    | CanvasSessionUpdateRequest
    | CanvasSessionIdRequest
    | PendingCanvasPatchOperation
    | PendingGenerateOperation
    | PendingSkillWriteOperation
    | CanvasToolPendingOperation,
    Field(union_mode="left_to_right"),
]
GenerationContracts = Annotated[
    SubmitGenerateRequest
    | GenerateTaskView
    | GenerateTaskListRequest
    | GenerateTaskListResponse
    | GenerateModelItem
    | GenerateParamOptions,
    Field(union_mode="left_to_right"),
]
GatewayContracts = Annotated[
    GatewayModelsResponse
    | GatewayImageSubmitRequest
    | GatewayVideoSubmitRequest
    | GatewayTaskSubmitResponse
    | GatewayTaskStatusResponse
    | GatewayQueueResponse
    | GatewayGenerateCallback,
    Field(union_mode="left_to_right"),
]
ProjectContracts = Annotated[
    ProjectCreateRequest
    | ProjectView
    | ProjectCreateResponse
    | ProjectListRequest
    | ProjectListResponse
    | ProjectIdRequest
    | ProjectDetailResponse
    | ProjectUpdateRequest
    | EpisodeView
    | EpisodeListRequest
    | EpisodeListResponse
    | EpisodeCreateRequest
    | EpisodeIdRequest
    | EpisodeUpdateRequest,
    Field(union_mode="left_to_right"),
]
EcommerceContracts = Annotated[
    MarketCompetitorBrief
    | ListingCopyVersion
    | CreativeBrief
    | GenerationAssetRef
    | CampaignPlanDoc
    | EmailSequenceDraft
    | AdsStrategyBrief
    | AdsReportDiagnosis
    | MetricsSnapshot
    | MetricAvailability
    | ShopConnectionPublic
    | DailyOrWeeklyReview
    | AnomalyList
    | ChartArtifact
    | PublishDiff
    | PublishReceipt
    | SkuRecord
    | SkuCostRecord
    | TaobaoOrderLineNormalized
    | TaobaoProductDailyNormalized
    | TaobaoInventorySnapshotNormalized
    | TaobaoAdsDailyNormalized,
    Field(union_mode="left_to_right"),
]
WorkshopContracts = Annotated[
    WorkshopCreateProjectRequest
    | WorkshopProjectIdRequest
    | WorkshopGroupChatIdRequest
    | WorkshopProjectByChatResponse
    | WorkshopUpgradeRequest
    | WorkshopProjectView
    | WorkshopProjectListResponse
    | WorkshopUpgradeResultView
    | WorkshopWakeDashboardView
    | WorkshopRosterExpertView
    | WorkshopRosterListResponse
    | WorkshopRoomMembersResponse
    | WorkshopCopyPresetRequest
    | WorkshopAddPresetRequest
    | WorkshopProposeCustomExpertRequest
    | WorkshopConfirmCustomExpertRequest
    | WorkshopDeclineCustomExpertRequest
    | WorkshopExpertProposalIdView
    | WorkshopInviteDirectoryExpertView
    | WorkshopInviteDirectoryResponse
    | WorkshopInviteExpertRequest
    | WorkshopRemoveExpertRequest
    | WorkshopAssignTaskExpertRequest
    | WorkshopAuthorizeOperationRequest
    | WorkshopAuthorizedOperationView
    | WorkshopAuthorizedOperationListResponse
    | WorkshopUnassignTaskExpertRequest
    | WorkshopCreateTaskProposalView
    | WorkshopPendingProposalListResponse
    | WorkshopProposeTaskRequest
    | WorkshopConfirmTaskProposalRequest
    | WorkshopDeclineTaskProposalRequest
    | WorkshopTaskIdRequest
    | WorkshopTaskView
    | WorkshopTaskListResponse
    | WorkshopGrantExternalAuthRequest
    | WorkshopRecordCapabilityUseRequest
    | WorkshopWeakAcceptRequest
    | WorkshopWeakAcceptResultView
    | ArtifactSubmissionView
    | WorkshopArtifactView
    | WorkshopArtifactListRequest
    | WorkshopArtifactListResponse
    | WorkshopWorkflowStepView
    | WorkshopDraftWorkflowRequest
    | WorkshopConfirmWorkflowRequest
    | WorkshopWorkflowView
    | WorkshopWorkflowListResponse
    | WorkshopManualRunWorkflowRequest
    | WorkshopManualRunResultView
    | WorkshopCreateScheduleRequest
    | WorkshopScheduleIdRequest
    | WorkshopScheduleView
    | WorkshopScheduleListResponse
    | WorkshopScheduleRunView
    | WorkshopTriggerScheduleRequest
    | WorkshopScheduleTriggerResultView
    | WorkshopCompleteScheduledRunRequest
    | WorkshopScheduledCompletionResultView
    | WorkshopEventView
    | WorkshopEventListResponse
    | WorkshopOkView
    | WorkshopTaskAssignmentView
    | WorkshopTaskAssignmentListResponse
    | WorkshopDataSourcesView
    | WorkshopDataSourceStatus
    | WorkshopImportErrorView
    | WorkshopImportErrorListResponse
    | WorkshopBeginShopAuthView
    | ExpertDirectoryEntry
    | ExpertDirectoryResponse
    | WorkshopConnectorDirectoryResponse
    | ExpertTeamDirectoryEntry
    | ExpertTeamDirectoryResponse
    | ChatSelectedExpertView
    | SetChatSelectedExpertRequest
    | ClearChatSelectedExpertRequest
    | GetChatSelectedExpertRequest
    | WorkshopUpgradeFromTeamRequest
    | WorkshopUpgradeFromExpertRequest
    | WorkshopTeamSelectRequiresUpgradeView
    | WorkshopTurnTarget,
    Field(union_mode="left_to_right"),
]

CONTRACTS: dict[str, TypeAdapter[Any]] = {
    "canvas": TypeAdapter(CanvasContracts),
    "ecommerce": TypeAdapter(EcommerceContracts),
    "generation": TypeAdapter(GenerationContracts),
    "gateway": TypeAdapter(GatewayContracts),
    "projects": TypeAdapter(ProjectContracts),
    "workshop": TypeAdapter(WorkshopContracts),
    "stream": TypeAdapter(StreamFrame),
    "turn_content": TypeAdapter(
        TurnUserInput | TurnTextBlock | TurnSkillBlock | TurnMediaBlock | TurnNodeBlock
    ),
}


def render_schemas() -> dict[Path, str]:
    return {
        SCHEMA_DIR / f"{name}.json": (
            json.dumps(adapter.json_schema(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )
        for name, adapter in CONTRACTS.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查已提交 schema 是否与 Pydantic contract 一致",
    )
    args = parser.parse_args()
    rendered = render_schemas()
    if args.check:
        stale = [
            str(path.relative_to(ROOT))
            for path, content in rendered.items()
            if not path.exists() or path.read_text(encoding="utf-8") != content
        ]
        if stale:
            print("stale generated schemas:")
            print("\n".join(stale))
            return 1
        return 0

    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for path, content in rendered.items():
        path.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
