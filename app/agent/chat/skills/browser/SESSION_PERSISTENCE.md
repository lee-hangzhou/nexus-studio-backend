# Session persistence

**Not `session_bridge`.** Bridge is not wired; use save/restore tools below.

## WHEN TO USE

- Same conversation will hit the **same site** again → start with `browser_restore_session` (optional `expected_domain`).
- Task legally finished after deliver → `browser_end_session` before final natural language.

## WHEN NOT TO USE

- Public one-shot page, never logged in → no save needed; restore may return nothing (normal).
- Gate pending / waiting on user panel → **do not** `browser_end_session` (browser must stay for gate).
- Turn cancelled/aborted → do not treat as successful save (mechanism may discard).

## OBSERVE

- `browser_restore_session` tool_result: `restored`, `logged_in`, `need_login`, `domain`, `auth_flags`.
- `browser_auth_status` — merged site_auth + storage + probe (read-only).
- After restore + goto, page facts (profile UI, login modal, data reachable).

## ACTION — start

1. Optional `browser_restore_session` or `browser_auth_status`.
2. If `logged_in` true and a minimal probe shows target data reachable → work loop.
3. Otherwise → **`login_method`** then auth chain; use `auth_flags` to resume (e.g. skip credentials if already submitted only when probe shows session still valid).
4. **Challenge / tool_error 断点续** — 禁止 goto 首页、关闭登录弹窗、restore 除非 `session_invalid` 或 `auth_flags` 表明会话失效。用 `browser_auth_status` 判断重走 auth 哪一步。

## ACTION — end

1. After `publish_file` or allowed `signal_browser_blocked` completion.
2. `browser_end_session` (default saves storage state, closes browser).
3. Then final message to user.

## DO NOT

- Ask user for cookies or tokens.
- Mention bridge or manual cookie export.
- Close browser mid-gate.

## FAILURE / NEXT STEP

- Save fails → log visible to ops; tell user next turn may need login again.
- Restore invalid → auth chain before substantive work; do not pretend logged in.
