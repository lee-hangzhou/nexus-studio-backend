---
name: browser
description: "Runs Playwright browser sessions for pages that need cookies, JavaScript, user gates, paginated capture to workspace/raw/, or structured delivery via ETL. Use when web_fetch is insufficient (auth, gates, sliders, raw batch capture) or when continuing a logged-in site in the same conversation. Prefer web_search for discovery and web_fetch for anonymous one-shot URLs. Read TOOL_BOUNDARIES.md before choosing between web tools."
---

# Browser skill

## Quick start

Default on a site: **auth first**, then **work**. All side effects follow **write-verify** ([WRITE_VERIFY.md](WRITE_VERIFY.md)).

1. **Restore or login** — `browser_restore_session` / `browser_auth_status`; if not logged in → auth chain ([AUTH_PRIORITY.md](AUTH_PRIORITY.md)).
2. **Work** — goto targets, inspect, capture, ETL, deliver.
3. **Release** — `publish_file` → `browser_end_session` → final. See [COMPLETION.md](COMPLETION.md).

Tool choice vs `web_search` / `web_fetch`: [TOOL_BOUNDARIES.md](TOOL_BOUNDARIES.md).

## Phases

| Phase | Doc |
|-------|-----|
| **Auth** | [AUTH_PRIORITY.md](AUTH_PRIORITY.md), [LOGIN_METHOD.md](LOGIN_METHOD.md), [WRITE_VERIFY.md](WRITE_VERIFY.md), gate docs below |
| **Challenge** | [INTERACTIVE_AUTO.md](INTERACTIVE_AUTO.md), [COORDINATE_SPACE.md](COORDINATE_SPACE.md) |
| **Work** | [INSPECT.md](INSPECT.md), [CAPTURE.md](CAPTURE.md) |
| **Deliver / Release** | [ETL.md](ETL.md), [SESSION_PERSISTENCE.md](SESSION_PERSISTENCE.md), [COMPLETION.md](COMPLETION.md) |

## Tools

| Tool | Role |
|------|------|
| `browser_exec_script` | Session work: goto, inspect, capture (write → verify) |
| `browser_capture_state` | Ops debug snapshot |
| `request_user_gate` | Auth panels — one tool_call per step |
| `browser_auth_status` | Read-only auth facts (site_auth + probe) |
| `browser_restore_session` / `browser_end_session` | Load/save session |
| `browser_challenge_*` / `cv_*` | Interactive challenge loop |
| `signal_browser_blocked` | Stop turn when no viable path |

## Index

| Doc | Content |
|-----|---------|
| [WRITE_VERIFY.md](WRITE_VERIFY.md) | Read/write + mandatory verify |
| [TOOL_BOUNDARIES.md](TOOL_BOUNDARIES.md) | web_search / web_fetch / browser |
| [AUTH_PRIORITY.md](AUTH_PRIORITY.md) | Auth first |
| [LOGIN_METHOD.md](LOGIN_METHOD.md) | Panel login method |
| [INTERACTIVE_AUTO.md](INTERACTIVE_AUTO.md) | Challenge paradigm |
| [INSPECT.md](INSPECT.md) | DOM observe, scope_selector |
| [SESSION_PERSISTENCE.md](SESSION_PERSISTENCE.md) | Restore, auth status,断点续 |
| [FAILURE_POLICY.md](FAILURE_POLICY.md) | error_type handling |
| [examples.md](examples.md) | Abstract tool chains |

## Prohibitions

- Parallel `request_user_gate` with any other tool in the same step.
- Credentials in `browser_exec_script`; use gate + vault.
- `session_bridge`: not available.
- Final while workflow incomplete — [COMPLETION.md](COMPLETION.md).
