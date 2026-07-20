# Inspect — DOM observation

## WHEN TO USE

- Before the **next tool** needs selectors or auth facts (gate, OTP send, slider geometry, capture targets).
- After navigation, tab switch, submit, or gate resume when page structure may have changed.
- Before opening a gate or starting auth (login form, OTP send, slider C/T/H).
- When auth or work is **blocked** and the next tool needs fresh selectors.

## WHEN NOT TO USE

- Duplicate `browser_capture_state` in the same inspect pass without new failure evidence.
- Same step as `request_user_gate`.
- **Rescan policy — skip** when same modal/URL, selectors already in prior tool_result, no failed action since, gate just succeeded and only waiting for navigation.

## OBSERVE

Standard inputs/buttons plus:

- Agreement / toggle areas ([LOGIN_PREP.md](LOGIN_PREP.md)) — custom labels, not only `input[type=checkbox]`.
- Send-code control (`send_selector` for OTP).
- Login state hints (profile, logout, login modal).
- Slider C/T/H when needed — [INTERACTIVE_AUTO.md](INTERACTIVE_AUTO.md).

Example enumerate (extend in script as needed):

```python
items = await page.evaluate("""() => {
  return [...document.querySelectorAll('input, button, label, img, canvas, [role=button]')].map(el => ({
    tag: el.tagName, type: el.type || '', id: el.id || '',
    name: el.name || '', placeholder: el.placeholder || '',
    className: (el.className || '').slice(0, 80), visible: el.offsetParent !== null,
    text: (el.textContent || '').trim().slice(0, 60),
  }));
}""")
print(json.dumps(items, ensure_ascii=False))
```

Record URL, visible login tabs, captcha/QR/slider regions.

**Scope:** record `scope_selector` — login modal / challenge panel root for gate and `browser_challenge_*` (avoids global `a:has-text` strict violations).

## ACTION

1. One `browser_exec_script` step per inspect pass.
2. Optional `browser_capture_state` when diagnosis needs a debug snapshot.
3. Feed selectors into next gate/challenge/send tool — not shown to user/SSE.

## DO NOT

- Playwright-only selectors inside `page.evaluate` (`:has-text`, `:has`).
- Repeat identical inspect script without new evidence.
- Guess `request_user_gate` parameters.

## FAILURE / NEXT STEP

- Empty / not loaded → wait or re-goto; optional `browser_capture_state` for debug.
- `missing_selector` → new inspect; slider needs C, T, H all present.

Match DOM patterns to tools: [LOGIN_CLASSIFICATION.md](LOGIN_CLASSIFICATION.md).
