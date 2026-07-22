from __future__ import annotations

from dataclasses import dataclass

from app.server.generation.persistence.generate_task import GenerateTask


@dataclass(frozen=True)
class GenerationCallbackResult:
    # applied：本次是否经 CAS 推进了本地任务状态
    task: GenerateTask
    applied: bool
