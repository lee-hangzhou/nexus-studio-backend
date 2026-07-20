# Write-Verify — browser side effects

## WHEN TO USE

- Before any browser tool that changes page state, cookies, or navigation.
- After every **write** tool returns — plan the **next model step** as read/verify.

## WHEN NOT TO USE

- Announcing success in natural language without a verify read in a separate step.
- Treating gate resume JSON (`choice_submitted`, `submit_dispatched`, `qr_acknowledged`) as logged-in proof.

## Read vs Write

| Kind | Tools |
|------|-------|
| **Read** | `browser_capture_state`, inspect-only `browser_exec_script`, `browser_challenge_read_geometry`, `browser_challenge_screenshot_element`, `browser_challenge_wait_probe`, `browser_restore_session`, `browser_auth_status` |
| **Write** | `request_user_gate` submit paths, `browser_trigger_otp_send`, `browser_challenge_dispatch_pointer_trace`, `browser_exec_script` with goto/click/fill |

## Rule

Any write → **next model step** must call a read/probe tool and use returned **facts** (`outcome`, `auth_flags`, `logged_in`, geometry) to decide continue. Do not skip verify to save steps.

## Boundaries

- Write-verify is a loop pattern, not a gate checklist per site.
- Mechanism only enforces gate **argument** validity and `login_method` before target auth gate in the same chain when `site_auth` has no prior choice.
- `submit_dispatched` / `choice_submitted` **≠** logged in — use `browser_auth_status` (with probe selector) or inspect.

## Examples

1. `browser_exec_script` click agreement → next step inspect toggle state.
2. `browser_challenge_dispatch_pointer_trace` → next step `browser_challenge_wait_probe` → read `outcome`.
3. `request_user_gate(credentials)` → next step `browser_auth_status` with `login_probe_selector` or inspect DOM.

## See also

- [FAILURE_POLICY.md](FAILURE_POLICY.md) — `error_type` after failed verify
- [INTERACTIVE_AUTO.md](INTERACTIVE_AUTO.md) — challenge verify loop
- [SESSION_PERSISTENCE.md](SESSION_PERSISTENCE.md) — restore / auth status
