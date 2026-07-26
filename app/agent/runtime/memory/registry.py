from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from app.agent.runtime.memory.namespaces import (
    CANVAS_PROJECT_MEMORY_NAMESPACE,
    CANVAS_USER_MEMORY_NAMESPACE,
    CHAT_USER_MEMORY_NAMESPACE,
)
from app.agent.runtime.memory.schemas import ProjectFactMemory, UserMemory

MemoryDomainName = Literal["chat", "canvas"]
MemoryScopeName = Literal["user", "project"]


@dataclass(frozen=True)
class MemoryScopeSpec:
    """单个记忆 scope 的冻结规格"""

    scope: MemoryScopeName
    namespace: tuple[str, ...]
    schema: type[BaseModel]
    tool_names: tuple[str, ...]
    inject_mode: Literal["queryless", "semantic"]
    extract_enabled: bool


@dataclass(frozen=True)
class MemoryDomainSpec:
    """单个记忆域的冻结规格"""

    domain: MemoryDomainName
    scopes: tuple[MemoryScopeSpec, ...]

    def scope_spec(self, scope: MemoryScopeName) -> MemoryScopeSpec:
        """按名取 scope，缺失则失败"""
        for item in self.scopes:
            if item.scope == scope:
                return item
        raise ValueError(f"memory scope missing: {self.domain}.{scope}")


def _validate_domain(spec: MemoryDomainSpec) -> None:
    """构造期校验 namespace 终止段与工具名唯一"""
    scope_names = [s.scope for s in spec.scopes]
    if len(scope_names) != len(set(scope_names)):
        raise ValueError(f"duplicate scopes in domain {spec.domain}")
    tool_names: list[str] = []
    for scope in spec.scopes:
        if scope.namespace[-1] != "records":
            raise ValueError(f"namespace must end with records: {scope.namespace}")
        for part in scope.namespace:
            if any(ch in part for ch in ("%", "_", ".")) and "{" not in part:
                raise ValueError(f"illegal namespace segment: {part}")
        tool_names.extend(scope.tool_names)
    if len(tool_names) != len(set(tool_names)):
        raise ValueError(f"duplicate tool names in domain {spec.domain}")


CHAT_DOMAIN = MemoryDomainSpec(
    domain="chat",
    scopes=(
        MemoryScopeSpec(
            scope="user",
            namespace=CHAT_USER_MEMORY_NAMESPACE,
            schema=UserMemory,
            tool_names=("manage_user_memory", "recall_user_memory"),
            inject_mode="queryless",
            extract_enabled=False,
        ),
    ),
)

CANVAS_DOMAIN = MemoryDomainSpec(
    domain="canvas",
    scopes=(
        MemoryScopeSpec(
            scope="user",
            namespace=CANVAS_USER_MEMORY_NAMESPACE,
            schema=UserMemory,
            tool_names=("manage_user_memory", "recall_user_memory"),
            inject_mode="queryless",
            extract_enabled=False,
        ),
        MemoryScopeSpec(
            scope="project",
            namespace=CANVAS_PROJECT_MEMORY_NAMESPACE,
            schema=ProjectFactMemory,
            tool_names=("manage_project_memory", "recall_project_memory"),
            inject_mode="semantic",
            extract_enabled=True,
        ),
    ),
)

_validate_domain(CHAT_DOMAIN)
_validate_domain(CANVAS_DOMAIN)

MEMORY_DOMAINS: dict[MemoryDomainName, MemoryDomainSpec] = {
    "chat": CHAT_DOMAIN,
    "canvas": CANVAS_DOMAIN,
}


def get_memory_domain(domain: MemoryDomainName) -> MemoryDomainSpec:
    """返回已校验的记忆域规格"""
    return MEMORY_DOMAINS[domain]
