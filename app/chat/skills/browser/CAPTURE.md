# Capture — paginated raw extraction

## WHEN TO USE

- **After auth** (default), write list/detail data to `workspace/raw/`.
- Skip-auth capture only when probe proves logged-in or genuinely public data without session — exception, not default.
- User needs bulk data in workspace before ETL — not a one-line answer in chat.

## WHEN NOT TO USE

- Page still blocked by login/verification → pause, complete auth, resume capture (do not abandon raw plan).
- One page / one API call worth of data → answer or single fetch may suffice.
- Multiple `browser_exec_script` rounds for pagination → merge into **one** script instead.

## OBSERVE

- Data source: XHR/API vs DOM vs next-page control.
- `context`, `raw_dir`, `page` are already in script scope.
- If mid-capture a login wall appears → stop pagination, auth loop, then **resume** from last saved page index.

## ACTION

**Single script** paginates and writes `raw/page_{n}.json` (or one consolidated file when API allows). Prefer `context.request` with session cookies when inspect found a stable API.

At end, `print` a short JSON summary (page count, paths) — not full raw in stdout.

If blocked during capture: `browser_capture_state` for evidence → auth → continue the **same** script logic on resume (do not restart from page 1 unless raw is empty).

See [ETL.md](ETL.md) after raw is complete.

## DO NOT

- One `browser_exec_script` per page (latency + state drift).
- `publish_file` on `raw/` (backend rejects).
- Paste large raw JSON in the assistant reply.
- Probe APIs in many small exec rounds — one script, one pass.

## FAILURE / NEXT STEP

- API 401/403 mid-run → auth when blocked, then retry remaining pages.
- Single page timeout → log pages written so far; shrink batch or diagnose with `browser_capture_state`.
- Empty first page after auth → re-inspect DOM/API; do not silently claim success.
