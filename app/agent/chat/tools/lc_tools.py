import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, List, Protocol
from uuid import uuid4

from langchain.tools import InjectedToolArg
from langchain_core.tools import InjectedToolCallId, StructuredTool
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import InjectedState

from app.agent.chat.artifacts.types import PublishResult
from app.agent.chat.browser.state_capture import capture_browser_state
from app.agent.chat.tools import file_ops, web_fetch, web_search
from app.agent.chat.tools.browser_blocked import signal_browser_blocked as _signal_browser_blocked_impl
from app.agent.chat.tools.browser_challenge import (
    browser_challenge_dispatch_pointer_trace,
    browser_challenge_read_geometry,
    browser_challenge_screenshot_element,
    browser_challenge_wait_probe,
)
from app.agent.chat.tools.browser_exec import browser_exec_script
from app.agent.chat.tools.browser_gate import GateType, PhoneOtpPhase, request_user_gate
from app.agent.chat.tools.browser_otp import browser_trigger_otp_send
from app.agent.chat.tools.browser_auth import browser_auth_status
from app.agent.chat.tools.browser_session import browser_end_session, browser_restore_session
from app.agent.chat.tools.cv_primitives import cv_find_gap_x, cv_image_info, cv_match_template
from app.agent.chat.tools.definitions import TOOL_DESCRIPTIONS
from app.agent.chat.tools.memory_tools import build_chat_memory_tools
from app.agent.chat.tools.python_sandbox import execute_python
from app.agent.chat.tools.result import (
    DUPLICATE_CALL,
    FILE_NOT_FOUND,
    FILE_NOT_READY,
    INTERNAL,
    INVALID_ARGUMENTS,
    INVALID_URL,
    TURN_INTERRUPTED,
    ToolResult,
)
from app.agent.chat.turn.trace import log_stage
from app.contracts.metadata import ToolAuditMetadata
from app.server.infra.config import settings
from app.server.infra.object_storage import object_storage
from app.server.assets.services.service import ASSET_SOURCE_ASSISTANT_OUTPUT, asset_service
from app.server.chat.domain.enums import AttachmentSource
from app.server.chat.persistence.attachments import ChatAttachments

if TYPE_CHECKING:
    from app.agent.chat.tools.judgment_gate import UpgradeInviteGateState
    from app.agent.chat.turn.guards import TurnGuards
    from app.agent.runtime.turn.tool_loop_guard import TurnToolLoopGuard
    from app.server.ports.product import UpgradeInviteProposalDTO

MAX_WEB_QUERY_LENGTH = 300
MAX_FILE_WRITE_CHARS = 200_000



class CreateUpgradeInviteFn(Protocol):
    """创建升级邀请提议的注入回调"""

    async def __call__(
        self,
        *,
        expert_keys: tuple[str, ...],
        primary_expert_key: str,
        rationale: str,
        host_narration: str,
    ) -> "UpgradeInviteProposalDTO": ...


@dataclass
class ChatToolContext:
    user_id: int
    conversation_id: int
    workspace: Path
    audit: list[ToolAuditMetadata]
    guards: "TurnGuards | None" = None
    cancel_event: object | None = None
    file_read_cache: dict[str, str] = field(default_factory=dict)
    published_artifacts: list[PublishResult] = field(default_factory=list)
    loop_guard: "TurnToolLoopGuard | None" = None
    judgment_gate: "UpgradeInviteGateState | None" = None
    create_upgrade_invite: "CreateUpgradeInviteFn | None" = None
    # Workshop Host：邀请后隐式交接的专家 id（房间成员）
    pending_expert_handoff_id: str | None = None
    source_user_text: str = ""
    # 当前回合模型；Host 起草工作流时写入定义
    model_key: str = ""


def _cancelled_tool_result(ctx: ChatToolContext) -> ToolResult | None:
    ev = ctx.cancel_event
    if ev is not None and getattr(ev, "is_set", lambda: False)():
        return ToolResult.fail(TURN_INTERRUPTED, detail="turn cancelled")
    return None


def _emit(
    ctx: ChatToolContext,
    name: str,
    args: dict,
    result: ToolResult,
    started: float,
) -> str:
    ctx.audit.append(
        ToolAuditMetadata(
            name=name,
            args=args,
            result_preview=result.display_text[:500],
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    )
    return result.to_tool_message()


def build_langchain_tools(ctx: ChatToolContext, enable_tools: bool = True) -> List[StructuredTool]:
    if not enable_tools:
        return []

    async def _web_search(
        query: str,
        freshness: str | None = None,
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        started = time.perf_counter()
        query = query.strip()[:MAX_WEB_QUERY_LENGTH]
        try:
            normalized_freshness = web_search.normalize_freshness(freshness)
        except web_search.InvalidFreshnessError as exc:
            return _emit(
                ctx,
                "web_search",
                {"query": query, "freshness": freshness},
                ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc)),
                started,
            )
        try:
            text = await web_search.search_web(query, freshness=normalized_freshness)
        except Exception as exc:
            return _emit(
                ctx,
                "web_search",
                {"query": query, "freshness": freshness},
                ToolResult.fail(INTERNAL, detail=str(exc)),
                started,
            )
        if text.startswith("web_search unavailable:"):
            return _emit(
                ctx,
                "web_search",
                {"query": query, "freshness": freshness},
                ToolResult.fail(INTERNAL, detail=text),
                started,
            )
        return _emit(
            ctx,
            "web_search",
            {"query": query, "freshness": freshness},
            ToolResult.ok(text),
            started,
        )

    async def _web_fetch(
        url: str,
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        started = time.perf_counter()
        if not url.startswith(("http://", "https://")):
            return _emit(
                ctx,
                "web_fetch",
                {"url": url},
                ToolResult.fail(INVALID_URL, detail="URL must start with http:// or https://"),
                started,
            )
        try:
            text = await web_fetch.fetch_url(url)
        except Exception as exc:
            return _emit(
                ctx,
                "web_fetch",
                {"url": url},
                ToolResult.fail(INTERNAL, detail=str(exc)),
                started,
            )
        return _emit(ctx, "web_fetch", {"url": url}, ToolResult.ok(text), started)

    async def _execute_python(
        code: str,
        packages: list[str] | None = None,
        allow_network: bool = False,
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        started = time.perf_counter()
        result = await execute_python(ctx.workspace, code, packages, allow_network=allow_network)
        return _emit(
            ctx,
            "execute_python",
            {"packages": packages or [], "allow_network": allow_network},
            result,
            started,
        )

    def _read_file(
        path: str,
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        started = time.perf_counter()
        cached = ctx.file_read_cache.get(path)
        if cached is not None:
            return _emit(ctx, "read_file", {"path": path}, ToolResult.ok(cached), started)
        log_stage("tool.read_file.start", path=path, call_id=tool_call_id)
        result = file_ops.read_workspace_file(ctx.workspace, path)
        if result.success:
            ctx.file_read_cache[path] = result.output
        log_stage(
            "tool.read_file.end",
            path=path,
            call_id=tool_call_id,
            started=started,
            result_chars=len(result.display_text),
        )
        return _emit(ctx, "read_file", {"path": path}, result, started)

    def _write_file(
        path: str,
        content: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        started = time.perf_counter()
        if len(content) > MAX_FILE_WRITE_CHARS:
            result = ToolResult.fail(
                INVALID_ARGUMENTS,
                detail=f"content too large ({len(content)} chars)",
            )
            return _emit(ctx, "write_file", {"path": path}, result, started)
        result = file_ops.write_workspace_file(ctx.workspace, path, content)
        return _emit(ctx, "write_file", {"path": path}, result, started)

    def _list_files(
        path: str = ".",
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        started = time.perf_counter()
        result = file_ops.list_workspace_files(ctx.workspace, path)
        return _emit(ctx, "list_files", {"path": path}, result, started)

    async def _publish_file(
        path: str,
        filename: str | None = None,
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        started = time.perf_counter()
        if file_ops.is_skills_path(path):
            result = ToolResult.fail(
                INVALID_ARGUMENTS,
                detail=file_ops.PUBLISH_SKILLS_REJECT_DETAIL,
            )
            return _emit(ctx, "publish_file", {"path": path}, result, started)
        if file_ops.is_browser_raw_path(path):
            result = ToolResult.fail(
                INVALID_ARGUMENTS,
                detail=file_ops.PUBLISH_BROWSER_RAW_REJECT_DETAIL,
            )
            return _emit(ctx, "publish_file", {"path": path}, result, started)
        if file_ops.is_browser_internal_path(path):
            result = ToolResult.fail(
                INVALID_ARGUMENTS,
                detail=file_ops.PUBLISH_BROWSER_INTERNAL_REJECT_DETAIL,
            )
            return _emit(ctx, "publish_file", {"path": path}, result, started)
        target = file_ops.try_resolve_workspace_path(ctx.workspace, path)
        if target is None:
            result = ToolResult.fail(
                INVALID_ARGUMENTS,
                detail=f"{file_ops.PATH_ESCAPE_DETAIL}\npath: {path}",
            )
            return _emit(ctx, "publish_file", {"path": path}, result, started)
        if not target.exists() or not target.is_file():
            result = ToolResult.fail(
                FILE_NOT_FOUND,
                detail=f"publish_file: file not found: {path}",
            )
            return _emit(ctx, "publish_file", {"path": path}, result, started)
        if not object_storage._is_configured:  # noqa: SLF001
            return _emit(
                ctx,
                "publish_file",
                {"path": path},
                ToolResult.fail(INTERNAL, detail="object storage not configured"),
                started,
            )

        upload_name = filename or target.name
        storage_key = f"chat/{ctx.user_id}/{ctx.conversation_id}/{uuid4().hex}/{upload_name}"
        file_size = target.stat().st_size
        raw_bytes = await asyncio.to_thread(target.read_bytes)
        file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        await object_storage.put_file_path(storage_key, target)
        row = await ChatAttachments.create(
            conversation_id=ctx.conversation_id,
            message_id=None,
            user_id=ctx.user_id,
            filename=upload_name,
            mime_type="application/octet-stream",
            storage_key=storage_key,
            size=file_size,
            source=AttachmentSource.ASSISTANT_TOOL.value,
            is_attached=True,
            file_sha256=file_sha256,
        )
        asset = await asset_service.create_asset(
            user_id=ctx.user_id,
            storage_key=storage_key,
            filename=upload_name,
            mime_type=row.mime_type,
            source_type=ASSET_SOURCE_ASSISTANT_OUTPUT,
            source_id=row.id,
            metadata={
                "conversation_id": ctx.conversation_id,
                "attachment_id": row.id,
                "size": file_size,
                "file_sha256": file_sha256,
            },
        )
        row.asset_id = asset.id
        await row.save(update_fields=["asset_id"])
        ctx.published_artifacts.append(
            PublishResult(
                attachment_id=row.id,
                asset_id=asset.id,
                filename=upload_name,
                mime_type=row.mime_type,
                storage_key=storage_key,
                workspace_path=path,
                size=file_size,
            )
        )
        text = f"published attachment_id={row.id} filename={upload_name}"
        return _emit(ctx, "publish_file", {"path": path, "filename": upload_name}, ToolResult.ok(text), started)

    async def _browser_exec_script(
        code: str,
        *,
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> str:
        started = time.perf_counter()
        blocked = _cancelled_tool_result(ctx)
        if blocked is not None:
            return _emit(ctx, "browser_exec_script", {"code": code}, blocked, started)
        raw = await browser_exec_script(
            code,
            config=config,
            cancel_event=ctx.cancel_event,
        )
        return _emit(
            ctx,
            "browser_exec_script",
            {"code": code},
            ToolResult.parse_tool_message(raw),
            started,
        )

    async def _browser_capture_state(
        label: str | None = None,
        full_page: bool = True,
        text_max_chars: int = 1200,
        *,
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> str:
        started = time.perf_counter()
        conversation_id = int((config.get("configurable") or {}).get("conversation_id"))
        workspace = Path((config.get("configurable") or {}).get("workspace") or "/tmp")
        turn_id = (config.get("configurable") or {}).get("turn_id")
        result = await capture_browser_state(
            conversation_id=conversation_id,
            workspace=workspace,
            label=label,
            full_page=full_page,
            text_max_chars=text_max_chars,
            turn_id=str(turn_id) if turn_id else None,
        )
        return _emit(
            ctx,
            "browser_capture_state",
            {"label": label, "full_page": full_page, "text_max_chars": text_max_chars},
            result,
            started,
        )

    async def _signal_browser_blocked(
        label: str | None = None,
        *,
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> str:
        started = time.perf_counter()
        conversation_id = int((config.get("configurable") or {}).get("conversation_id"))
        workspace = Path((config.get("configurable") or {}).get("workspace") or "/tmp")
        result = await _signal_browser_blocked_impl(
            conversation_id=conversation_id,
            workspace=workspace,
            label=label,
        )
        return _emit(ctx, "signal_browser_blocked", {"label": label}, result, started)

    async def _request_user_gate(
        gate_type: GateType,
        prompt: str,
        fields: list[dict[str, Any]] | None = None,
        phase: PhoneOtpPhase | None = None,
        choices: list[dict[str, Any]] | None = None,
        username_selector: str | None = None,
        password_selector: str | None = None,
        submit_selector: str | None = None,
        phone_selector: str | None = None,
        code_selector: str | None = None,
        captcha_image_selector: str | None = None,
        captcha_input_selector: str | None = None,
        qr_image_selector: str | None = None,
        login_probe_selector: str | None = None,
        login_probe_timeout_ms: int | None = None,
        expected_domain: str | None = None,
        scope_selector: str | None = None,
        *,
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
        state: Annotated[dict[str, Any], InjectedState] = None,
    ) -> str:
        started = time.perf_counter()
        raw = await request_user_gate(
            gate_type=gate_type,
            prompt=prompt,
            fields=fields,
            phase=phase,
            choices=choices,
            username_selector=username_selector,
            password_selector=password_selector,
            submit_selector=submit_selector,
            phone_selector=phone_selector,
            code_selector=code_selector,
            captcha_image_selector=captcha_image_selector,
            captcha_input_selector=captcha_input_selector,
            qr_image_selector=qr_image_selector,
            login_probe_selector=login_probe_selector,
            login_probe_timeout_ms=login_probe_timeout_ms,
            expected_domain=expected_domain,
            scope_selector=scope_selector,
            config=config,
            state=state,
        )
        return _emit(
            ctx,
            "request_user_gate",
            {
                "gate_type": gate_type,
                "prompt": prompt,
                "phase": phase,
            },
            ToolResult.parse_tool_message(raw),
            started,
        )

    async def _browser_challenge_read_geometry(
        container_selector: str,
        track_selector: str,
        handle_selector: str,
        attempt_id: str | None = None,
        scope_selector: str | None = None,
        content_selector: str | None = None,
        *,
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> str:
        started = time.perf_counter()
        raw = await browser_challenge_read_geometry(
            container_selector,
            track_selector,
            handle_selector,
            attempt_id,
            scope_selector=scope_selector,
            content_selector=content_selector,
            config=config,
        )
        return _emit(
            ctx,
            "browser_challenge_read_geometry",
            {"container_selector": container_selector},
            ToolResult.parse_tool_message(raw),
            started,
        )

    async def _browser_challenge_screenshot_element(
        selector: str,
        path: str,
        attempt_id: str | None = None,
        scope_selector: str | None = None,
        *,
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> str:
        started = time.perf_counter()
        raw = await browser_challenge_screenshot_element(
            selector,
            path,
            attempt_id,
            scope_selector=scope_selector,
            config=config,
        )
        return _emit(
            ctx,
            "browser_challenge_screenshot_element",
            {"selector": selector, "path": path},
            ToolResult.parse_tool_message(raw),
            started,
        )

    async def _browser_challenge_dispatch_pointer_trace(
        events: list[dict[str, Any]],
        attempt_id: str | None = None,
        *,
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> str:
        started = time.perf_counter()
        raw = await browser_challenge_dispatch_pointer_trace(events, attempt_id, config=config)
        return _emit(
            ctx,
            "browser_challenge_dispatch_pointer_trace",
            {"events_count": len(events)},
            ToolResult.parse_tool_message(raw),
            started,
        )

    async def _browser_challenge_wait_probe(
        selector: str | None = None,
        success_selector: str | None = None,
        failure_selector: str | None = None,
        retry_text_probe: str | None = None,
        panel_selector: str | None = None,
        scope_selector: str | None = None,
        timeout_ms: int = 5000,
        attempt_id: str | None = None,
        *,
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> str:
        started = time.perf_counter()
        raw = await browser_challenge_wait_probe(
            selector=selector,
            success_selector=success_selector,
            failure_selector=failure_selector,
            retry_text_probe=retry_text_probe,
            panel_selector=panel_selector,
            scope_selector=scope_selector,
            timeout_ms=timeout_ms,
            attempt_id=attempt_id,
            config=config,
        )
        return _emit(
            ctx,
            "browser_challenge_wait_probe",
            {
                "selector": success_selector or selector,
                "timeout_ms": timeout_ms,
            },
            ToolResult.parse_tool_message(raw),
            started,
        )

    async def _browser_auth_status(
        expected_domain: str | None = None,
        login_probe_selector: str | None = None,
        *,
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> str:
        started = time.perf_counter()
        raw = await browser_auth_status(
            expected_domain=expected_domain,
            login_probe_selector=login_probe_selector,
            config=config,
        )
        return _emit(
            ctx,
            "browser_auth_status",
            {"expected_domain": expected_domain},
            ToolResult.parse_tool_message(raw),
            started,
        )

    async def _cv_image_info(
        image_path: str,
        *,
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> str:
        started = time.perf_counter()
        raw = await cv_image_info(image_path, config=config)
        return _emit(ctx, "cv_image_info", {"image_path": image_path}, ToolResult.parse_tool_message(raw), started)

    async def _cv_match_template(
        image_path: str,
        template_path: str,
        roi: dict[str, int] | None = None,
        *,
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> str:
        started = time.perf_counter()
        raw = await cv_match_template(image_path, template_path, roi, config=config)
        return _emit(
            ctx,
            "cv_match_template",
            {"image_path": image_path, "template_path": template_path},
            ToolResult.parse_tool_message(raw),
            started,
        )

    async def _cv_find_gap_x(
        background_path: str | None = None,
        piece_path: str | None = None,
        image_path: str | None = None,
        *,
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> str:
        started = time.perf_counter()
        raw = await cv_find_gap_x(background_path, piece_path, image_path, config=config)
        return _emit(ctx, "cv_find_gap_x", {}, ToolResult.parse_tool_message(raw), started)

    async def _browser_trigger_otp_send(
        send_selector: str,
        otp_flow_id: str | None = None,
        *,
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> str:
        started = time.perf_counter()
        raw = await browser_trigger_otp_send(
            send_selector=send_selector,
            otp_flow_id=otp_flow_id,
            config=config,
        )
        return _emit(
            ctx,
            "browser_trigger_otp_send",
            {"send_selector": send_selector, "otp_flow_id": otp_flow_id},
            ToolResult.parse_tool_message(raw),
            started,
        )

    async def _browser_restore_session(
        expected_domain: str | None = None,
        login_probe_selector: str | None = None,
        *,
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> str:
        started = time.perf_counter()
        raw = await browser_restore_session(
            expected_domain=expected_domain,
            login_probe_selector=login_probe_selector,
            config=config,
        )
        return _emit(
            ctx,
            "browser_restore_session",
            {"expected_domain": expected_domain},
            ToolResult.parse_tool_message(raw),
            started,
        )

    async def _browser_end_session(
        save: bool = True,
        expected_domain: str | None = None,
        *,
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> str:
        started = time.perf_counter()
        raw = await browser_end_session(
            save=save,
            expected_domain=expected_domain,
            config=config,
        )
        return _emit(
            ctx,
            "browser_end_session",
            {"save": save, "expected_domain": expected_domain},
            ToolResult.parse_tool_message(raw),
            started,
        )

    tools = [
        StructuredTool.from_function(
            coroutine=_web_search,
            name="web_search",
            description=TOOL_DESCRIPTIONS["web_search"],
        ),
        StructuredTool.from_function(
            coroutine=_web_fetch,
            name="web_fetch",
            description=TOOL_DESCRIPTIONS["web_fetch"],
        ),
        StructuredTool.from_function(
            coroutine=_execute_python,
            name="execute_python",
            description=TOOL_DESCRIPTIONS["execute_python"],
        ),
        StructuredTool.from_function(
            _read_file,
            name="read_file",
            description=TOOL_DESCRIPTIONS["read_file"],
        ),
        StructuredTool.from_function(
            _write_file,
            name="write_file",
            description=TOOL_DESCRIPTIONS["write_file"],
        ),
        StructuredTool.from_function(
            _list_files,
            name="list_files",
            description=TOOL_DESCRIPTIONS["list_files"],
        ),
        StructuredTool.from_function(
            coroutine=_publish_file,
            name="publish_file",
            description=TOOL_DESCRIPTIONS["publish_file"],
        ),
    ]
    tools.extend(build_chat_memory_tools(ctx.loop_guard))
    if settings.CHAT_BROWSER_ENABLED:
        tools.extend(
            [
                StructuredTool.from_function(
                    coroutine=_browser_exec_script,
                    name="browser_exec_script",
                    description=TOOL_DESCRIPTIONS["browser_exec_script"],
                ),
                StructuredTool.from_function(
                    coroutine=_browser_capture_state,
                    name="browser_capture_state",
                    description=TOOL_DESCRIPTIONS["browser_capture_state"],
                ),
                StructuredTool.from_function(
                    coroutine=_signal_browser_blocked,
                    name="signal_browser_blocked",
                    description=TOOL_DESCRIPTIONS["signal_browser_blocked"],
                ),
                StructuredTool.from_function(
                    coroutine=_request_user_gate,
                    name="request_user_gate",
                    description=TOOL_DESCRIPTIONS["request_user_gate"],
                ),
                StructuredTool.from_function(
                    coroutine=_browser_challenge_read_geometry,
                    name="browser_challenge_read_geometry",
                    description=TOOL_DESCRIPTIONS["browser_challenge_read_geometry"],
                ),
                StructuredTool.from_function(
                    coroutine=_browser_challenge_screenshot_element,
                    name="browser_challenge_screenshot_element",
                    description=TOOL_DESCRIPTIONS["browser_challenge_screenshot_element"],
                ),
                StructuredTool.from_function(
                    coroutine=_browser_challenge_dispatch_pointer_trace,
                    name="browser_challenge_dispatch_pointer_trace",
                    description=TOOL_DESCRIPTIONS["browser_challenge_dispatch_pointer_trace"],
                ),
                StructuredTool.from_function(
                    coroutine=_browser_challenge_wait_probe,
                    name="browser_challenge_wait_probe",
                    description=TOOL_DESCRIPTIONS["browser_challenge_wait_probe"],
                ),
                StructuredTool.from_function(
                    coroutine=_cv_image_info,
                    name="cv_image_info",
                    description=TOOL_DESCRIPTIONS["cv_image_info"],
                ),
                StructuredTool.from_function(
                    coroutine=_cv_match_template,
                    name="cv_match_template",
                    description=TOOL_DESCRIPTIONS["cv_match_template"],
                ),
                StructuredTool.from_function(
                    coroutine=_cv_find_gap_x,
                    name="cv_find_gap_x",
                    description=TOOL_DESCRIPTIONS["cv_find_gap_x"],
                ),
                StructuredTool.from_function(
                    coroutine=_browser_trigger_otp_send,
                    name="browser_trigger_otp_send",
                    description=TOOL_DESCRIPTIONS["browser_trigger_otp_send"],
                ),
                StructuredTool.from_function(
                    coroutine=_browser_restore_session,
                    name="browser_restore_session",
                    description=TOOL_DESCRIPTIONS["browser_restore_session"],
                ),
                StructuredTool.from_function(
                    coroutine=_browser_auth_status,
                    name="browser_auth_status",
                    description=TOOL_DESCRIPTIONS["browser_auth_status"],
                ),
                StructuredTool.from_function(
                    coroutine=_browser_end_session,
                    name="browser_end_session",
                    description=TOOL_DESCRIPTIONS["browser_end_session"],
                ),
            ]
        )
    return tools
