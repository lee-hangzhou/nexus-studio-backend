# 何时使用 Browser Skill

## WHEN TO USE

- Live Playwright session needed: gates, JS, cookies, paginated `raw/` capture, authenticated API from browser context.
- `web_fetch` insufficient (see [TOOL_BOUNDARIES.md](TOOL_BOUNDARIES.md)).

## WHEN NOT TO USE

- URL known and anonymous fetch enough → `web_fetch`.
- Discover URLs only → `web_search`.
- Offline workspace files → `execute_python` / file tools.

## OBSERVE

- Tool results from fetch (403, empty shell).
- Whether task needs files in `raw/` + publish.
- Same conversation continuing same site → [SESSION_PERSISTENCE.md](SESSION_PERSISTENCE.md).

## ACTION

1. Read [SKILL.md](SKILL.md) and [TOOL_BOUNDARIES.md](TOOL_BOUNDARIES.md).
2. **Auth first** on the site (restore → `login_method` → gate if needed), then work loop. See [AUTH_PRIORITY.md](AUTH_PRIORITY.md).
3. Skip auth only when restore/probe proves logged-in + data reachable.

## DO NOT

- Default to browser when fetch works.
- Pre-open session without a work goal.

## FAILURE / NEXT STEP

- `web_fetch` auth failure → browser + auth docs.
- Browser disabled → tell user; do not fake results.
