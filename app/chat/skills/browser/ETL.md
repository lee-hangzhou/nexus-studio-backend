# ETL — clean and deliver

## WHEN TO USE

- `raw/` holds browser capture output; user asked for CSV/JSON/table or filtered fields.
- Deliverable must be a workspace file the user downloads.

## WHEN NOT TO USE

- Nothing in `raw/` yet → [CAPTURE.md](CAPTURE.md) first.
- Small result fits in chat → no forced publish.

## OBSERVE

- Raw file shapes, encoding, nested fields.
- **User field requirements** (which columns, date range, exclude reposts/replies, original posts only, etc.) — filter in ETL, do not dump all raw fields by default.
- Target format and path under workspace (e.g. `output/result.csv`).

## ACTION

1. `execute_python` reads `raw/`, maps/filters per user requirements, writes cleaned file outside `raw/` and `.browser/`.
2. `allow_network=true` **only** when the script calls external APIs.
3. Summarize key stats in tool output or a short reply **after** the script succeeds.
4. `publish_file` on the cleaned path — then `browser_end_session` if browser workflow is done ([COMPLETION.md](COMPLETION.md)).

Typical: read `raw/*.json` → drop forwards/replies if user wanted originals only → normalize timestamps → `output/result.csv` → `publish_file`.

## DO NOT

- `publish_file` on `raw/` or `.browser/` (backend rejects).
- Embed passwords, cookies, or vault secrets in ETL scripts.
- Skip bad rows silently — count or log rejects in script output.

## FAILURE / NEXT STEP

- Parse errors → inspect a raw sample, fix script; report what failed.
- Wrong publish path → path relative to workspace root, not host absolute path.
- Empty output after filter → tell user no rows matched criteria; do not publish an empty file without saying so.
