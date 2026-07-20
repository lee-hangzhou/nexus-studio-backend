# Abstract tool chains

No real site names. Patterns only.

## A — Public article, answer in chat

1. User gives URL.
2. `web_fetch` → body sufficient → answer.
3. No browser.

## B — Batch capture behind session

1. `browser_restore_session` (optional).
2. Not logged in → [LOGIN_PREP.md](LOGIN_PREP.md) if agreement UI → **`login_method`** → auth gate → **post-auth verify** ([AUTH_PRIORITY.md](AUTH_PRIORITY.md)) **before** task URLs.
3. `browser_exec_script` goto + capture single script to `raw/`.
4. `execute_python` ETL → `publish_file` → `browser_end_session`.

## C — Auth appears mid-capture

1. Capture script running; page N returns auth wall in tool_result.
2. Pause capture; inspect for auth; complete gate chain.
3. Resume capture from last successful page (do not restart entire task unless required).
