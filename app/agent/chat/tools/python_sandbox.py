from __future__ import annotations

import asyncio
import re
import shlex
import textwrap
from pathlib import Path

from app.agent.chat.tools.result import (
    INVALID_ARGUMENTS,
    SANDBOX_ERROR,
    SANDBOX_TIMEOUT,
    SANDBOX_UNAVAILABLE,
    ToolResult,
)
from app.server.infra.config import settings

SAFE_PACKAGE_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")
MAX_PACKAGES = 20
OUTPUT_MAX_CHARS = 12_000
SANDBOX_PACKAGES_MOUNT = "/sandbox_packages"


def _truncate_stream(text: str, *, label: str) -> str:
    if len(text) <= OUTPUT_MAX_CHARS:
        return text
    return text[:OUTPUT_MAX_CHARS] + f"\n[{label} truncated at {OUTPUT_MAX_CHARS} chars]"


def _packages_host_path() -> Path:
    return Path(settings.CHAT_SANDBOX_PACKAGES_ROOT)


def _pip_install_cmd(packages: list[str]) -> str:
    index = settings.CHAT_SANDBOX_PIP_INDEX.strip()
    trusted_host = settings.CHAT_SANDBOX_PIP_TRUSTED_HOST.strip()
    parts = [
        "pip",
        "install",
        "--target",
        SANDBOX_PACKAGES_MOUNT,
        "--cache-dir",
        f"{SANDBOX_PACKAGES_MOUNT}/.pip-cache",
    ]
    if index:
        parts.extend(["-i", index])
    if trusted_host:
        parts.extend(["--trusted-host", trusted_host])
    parts.extend(packages)
    return " ".join(shlex.quote(part) for part in parts)


def _sandbox_env_prefix() -> str:
    return (
        f"export NODE_PATH=/usr/local/lib/node_modules:${{NODE_PATH:-}}; "
        f"export PYTHONPATH={SANDBOX_PACKAGES_MOUNT}:$PYTHONPATH"
    )


def _python_run_cmd() -> str:
    return f"{_sandbox_env_prefix()}; python __run__.py"


async def _docker_run(
    workspace: Path,
    shell_cmd: str,
    *,
    network: str,
) -> tuple[int, str, str]:
    packages_host = _packages_host_path()
    packages_host.mkdir(parents=True, exist_ok=True)
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        "--memory",
        "512m",
        "--cpus",
        "1",
        "-v",
        f"{workspace.resolve()}:/workspace",
        "-v",
        f"{packages_host.resolve()}:{SANDBOX_PACKAGES_MOUNT}",
        "-w",
        "/workspace",
        settings.CHAT_SANDBOX_IMAGE,
        "/bin/sh",
        "-c",
        shell_cmd,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(
        proc.communicate(),
        timeout=settings.CHAT_SANDBOX_TIMEOUT_SEC,
    )
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    return proc.returncode or 0, out, err


async def execute_python(
    workspace: Path,
    code: str,
    packages: list[str] | None = None,
    *,
    allow_network: bool = False,
) -> ToolResult:
    workspace.mkdir(parents=True, exist_ok=True)
    script_path = workspace / "__run__.py"
    script_path.write_text(textwrap.dedent(code), encoding="utf-8")

    if packages:
        if len(packages) > MAX_PACKAGES:
            return ToolResult.fail(
                INVALID_ARGUMENTS,
                detail=f"too many packages ({len(packages)} > {MAX_PACKAGES})",
            )
        if any(not SAFE_PACKAGE_RE.match(pkg) for pkg in packages):
            return ToolResult.fail(INVALID_ARGUMENTS, detail="invalid package name")

        shell_cmd = f"{_sandbox_env_prefix()}; {_pip_install_cmd(packages)} && python __run__.py"
        network = settings.CHAT_SANDBOX_PIP_NETWORK
    else:
        shell_cmd = _python_run_cmd()
        network = settings.CHAT_SANDBOX_RUN_NETWORK if not allow_network else settings.CHAT_SANDBOX_PIP_NETWORK

    try:
        run_rc, run_out, run_err = await _docker_run(workspace, shell_cmd, network=network)
    except asyncio.TimeoutError:
        label = "pip install and python execution" if packages else "python execution"
        return ToolResult.fail(SANDBOX_TIMEOUT, detail=f"{label} timed out")
    except FileNotFoundError:
        return ToolResult.fail(SANDBOX_UNAVAILABLE, detail="docker not available on host")

    if run_rc != 0:
        label = "install+run" if packages else "run"
        detail = _truncate_stream(
            f"exit={run_rc}\nstdout:\n{run_out}\nstderr:\n{run_err}",
            label=label,
        )
        return ToolResult.fail(SANDBOX_ERROR, detail=detail)

    combined = _truncate_stream(run_out or run_err or "(no output)", label="stdout")
    return ToolResult.ok(combined)
