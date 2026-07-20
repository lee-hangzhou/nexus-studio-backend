# 滑块自动化 fixture — 人工验收

HTML fixture 供 Mac headed 或容器 driver 手动调试 **browser_challenge_*** 工具链。

## 开发配置

```bash
conda activate dream-drama-env
# .env:
# CHAT_BROWSER_INPROCESS=true
# CHAT_BROWSER_HEADED=true
# CHAT_BROWSER_SLOW_MO_MS=50
# CHAT_CAPTCHA_DEBUG_ARTIFACTS=true
make dev
```

## 验收步骤（`slider_to_end/index.html` 或同类 fixture）

1. `browser_exec_script` — `page.goto` 打开 fixture URL，inspect 得到 `#challenge` / `#track` / `#handle`（以实际 HTML 为准）。
2. `browser_challenge_read_geometry` — 确认 `travel` 与 `entities`。
3. （可选）`browser_challenge_screenshot_element` — 截图到 workspace。
4. `execute_python` — 生成目标 x 与人类化轨迹 JSON。
5. `browser_challenge_dispatch_pointer_trace` — 在 headed 窗口中应看到滑块拖动。
6. `browser_challenge_wait_probe` — `#success-msg`（或 fixture 内 success selector）应 `verified: true`。
7. 检查 `workspace/raw/captcha/{attempt_id}/` 工件目录。

## 容器路径

`.env` 改 `CHAT_BROWSER_INPROCESS=false`，`make docker-browser-build` 后重复上述流程（无桌面窗口，靠 probe + 工件）。

## 已废弃

- Chat 内 `interactive_challenge` gate、preflight、精灵截图 — 不再使用。
