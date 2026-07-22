# Completion contract

## WHEN TO USE

- Before ending the turn with natural language only (no further tool_call).

## 允许 final

1. **Delivered:** `publish_file` for requested output, then **`browser_end_session`** when browser was used for this workflow ([SESSION_PERSISTENCE.md](SESSION_PERSISTENCE.md)).
2. **Waiting on user:** `request_user_gate` interrupted turn — **do not** `browser_end_session`.
3. **Blocked:** `signal_browser_blocked` after explaining — may `browser_end_session` if policy saves partial state.
4. **Clarify:** Missing URL, scope, or login method — ask user or gate; no “I will next…” plan sentences.

## 禁止 final

- Capture/ETL/publish incomplete.
- Plan-only finals (“接下来我会…”).
- Stop on `browser_error` without retry or structured stop.
- “Please wait” without tool progress.
- **Auth incomplete:** agreement unchecked, target auth gate not done, or **post-auth verify not passed** — do not goto task URLs, do not tell user “已登录” ([AUTH_PRIORITY.md](AUTH_PRIORITY.md)).

## ACTION

- Advance phases with tool calls until a row above applies.
- After publish + end_session → brief user summary.

## Index

- [SKILL.md](SKILL.md) · [FAILURE_POLICY.md](FAILURE_POLICY.md)
