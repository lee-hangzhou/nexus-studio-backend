from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.agent.chat.prompt.types import TurnPromptContext
from app.agent.chat.skills.registry import SkillRegistry
from app.server.infra.config import settings
from app.agent.runtime.memory_store import get_memory_store

_CORE_POLICY_PATH = Path(__file__).resolve().parent.parent / "prompts" / "core_policy.md"
_MEMORY_GUIDE_PATH = Path(__file__).resolve().parent.parent / "prompts" / "memory_guide.md"


def load_core_policy() -> str:
    return _CORE_POLICY_PATH.read_text(encoding="utf-8").strip()


def load_memory_guide() -> str:
    return _MEMORY_GUIDE_PATH.read_text(encoding="utf-8").strip()


class PromptComposer:
    @staticmethod
    def build_capability_brief(ctx: TurnPromptContext) -> str:
        if not ctx.enable_tools:
            return "## 本回合能力\n本回合未启用工具，仅使用对话作答，不要调用工具。"
        names = ", ".join(ctx.tool_names) if ctx.tool_names else "（无）"
        return (
            "## 本回合能力\n"
            f"已启用工具：{names}。\n"
            "工作区路径为相对路径；读写、执行代码、发布文件均在当前会话工作区内完成。"
        )

    @staticmethod
    def build_context_clock(*, now: datetime | None = None) -> str:
        tz = ZoneInfo(settings.CHAT_CONTEXT_TIMEZONE)
        instant = now.astimezone(tz) if now is not None else datetime.now(tz)
        iso = instant.isoformat(timespec="seconds")
        human = instant.strftime("%Y年%m月%d日 %H:%M")
        return (
            "## 当前上下文\n"
            f"当前日期时间（{settings.CHAT_CONTEXT_TIMEZONE}）：{human}\n"
            f"ISO8601：{iso}"
        )

    @staticmethod
    def build_vision_brief(ctx: TurnPromptContext) -> str | None:
        if not ctx.has_vision_images:
            return None
        return (
            "## 识图\n"
            "用户在本轮消息中已直接附带图片，请根据消息中的图片内容作答。"
            "描述或分析图片时不要调用 execute_python、read_file 等工具去读取 attachments/ 下的图片文件。"
        )

    @staticmethod
    def build_browser_brief(ctx: TurnPromptContext) -> str | None:
        if "browser_exec_script" not in ctx.tool_names:
            return None
        return (
            "## 浏览器会话\n"
            "本回合已启用 browser 工具。\n"
            "- 默认 **auth → work**：restore 后 `need_login` → inspect → **有协议先 LOGIN_PREP 勾选并验态** → "
            "`login_method` → 目标 gate → **post-auth verify 通过前禁止 goto 任务 URL**（见 "
            "`skills/browser/AUTH_PRIORITY.md`）。遇 WAF/滑块回 auth。\n"
            "- 脚本内 **`page`、`context`、`Path`、`workspace`、`raw_dir` 已注入**；"
            "**禁止** `async_playwright()`、`chromium.connect()` 或自建 browser。\n"
            "- 凭证不得写在 browser 脚本里；秘密走 `request_user_gate`。\n"
            "- 写操作（goto/click/fill/gate/challenge 施动）后 **下一步必须 read/verify**，定义见 "
            "`skills/browser/WRITE_VERIFY.md`。\n"
            "- 流程与 web_fetch 边界：先 `read_file skills/browser/SKILL.md`，按需读子文档（尤其 "
            "`TOOL_BOUNDARIES.md`、`WRITE_VERIFY.md`）。workflow 未完成不得 final，见 `skills/browser/COMPLETION.md`。"
        )

    @staticmethod
    def build_turn_system(ctx: TurnPromptContext) -> str:
        """Static system prompt; per-turn attachment context is in HumanMessage prefix."""
        parts = [
            load_core_policy(),
            PromptComposer.build_capability_brief(ctx),
        ]
        browser_brief = PromptComposer.build_browser_brief(ctx)
        if browser_brief:
            parts.append(browser_brief)
        if get_memory_store() is not None:
            parts.append(
                "## 长期记忆\n"
                "本回合已启用用户级与会话级结构化长期记忆。"
                "下文为操作手册，仅供你决策读/写时机，不得复述给用户。"
            )
            parts.append(load_memory_guide())
        vision_brief = PromptComposer.build_vision_brief(ctx)
        if vision_brief:
            parts.append(vision_brief)
        parts.append(PromptComposer.build_context_clock())
        skill_index = SkillRegistry.build_index_block()
        if skill_index:
            parts.append(skill_index)
        return "\n\n".join(parts)
