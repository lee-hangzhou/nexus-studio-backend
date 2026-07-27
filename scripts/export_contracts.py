from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Annotated

from pydantic import Field, TypeAdapter

ROOT = Path(__file__).resolve().parents[1]
# 作为脚本直接执行时，先把仓库根目录加入模块搜索路径。
sys.path.insert(0, str(ROOT))

from app.contracts.canvas import (
    CanvasPatchOp,
    CanvasPatchRequest,
    CanvasPatchResponse,
    CanvasRevisionConflictItem,
    CanvasSnapshot,
    GenerationProgress,
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
from app.contracts.generation import GenerateParamOptions
from app.contracts.stream import StreamFrame
from app.server.generation.schemas import (
    GenerateModelItem,
    GenerateTaskListRequest,
    GenerateTaskListResponse,
    GenerateTaskView,
    SubmitGenerateRequest,
)
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

SCHEMA_DIR = ROOT / "contracts" / "schema"

CanvasContracts = Annotated[
    CanvasSnapshot
    | CanvasPatchRequest
    | CanvasPatchResponse
    | CanvasRevisionConflictItem
    | GenerationProgress
    | CanvasPatchOp,
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

CONTRACTS = {
    "canvas": TypeAdapter(CanvasContracts),
    "generation": TypeAdapter(GenerationContracts),
    "gateway": TypeAdapter(GatewayContracts),
    "projects": TypeAdapter(ProjectContracts),
    "stream": TypeAdapter(StreamFrame),
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
