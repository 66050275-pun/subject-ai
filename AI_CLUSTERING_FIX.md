# Citation theme response handling

A streamed `/api/ai/clusters` request can return HTTP 200 before the AI has finished. The NDJSON `result` or `error` event determines the analysis outcome.

## Changes

- Keep serial assignment batches of at most 12 references after a shared 3–5-theme plan. Prompts explicitly require original reference IDs rather than within-batch positions.
- Accept equivalent explicit assignments: ID-keyed dictionaries, `paper_id` / `reference_id`, `cluster_id`, exact theme names, and grouped memberships. Conflicting fields, unknown IDs, duplicate memberships, and positional substitutions remain invalid.
- Empty planned themes are valid; no reference is assigned just to fill a theme.
- A malformed assignment response marks that batch unassigned and continues to the next batch. Valid batches remain usable. The response includes `failed_clustering_batches`, `clustering_complete`, and warnings; the frontend reports assigned/total and renders unassigned references grey. No paid retries are added.
- If the theme plan cannot be read, stop with a specific planning error. Authentication, payment, quota, network and timeout errors still stop processing and preserve the existing graph.

## Validation

99 Python tests pass. New tests cover alternate assignment shapes, conflicting IDs, a malformed middle batch in a 54-reference input (42 assigned / 12 unassigned, six total calls), invalid planning, and empty themes. Browser tests verify partial results (14/24 assigned), neutral grey nodes, invalid responses preserving the graph, and blocking requests above the current 100-reference limit. Provider responses are mocked; no live MaxPlus API key was used.

The graph is a renderer, not the source of response-schema failures, so a separate graph is not required for this fix.

## Follow-up: initial theme planning

The reported `AI ส่งแผนธีมไม่ถูกต้อง` error happens before the assignment batches. The planner still sees a global title catalogue; batches of 12 apply to assignments only. Previous partial-batch handling could not solve an invalid initial plan. No live response from this reported MaxPlus request was available, so the exact upstream format is unverified.

- Corrected the planner prompt example to show 3 themes rather than 1.
- Added a feature-specific response parser while retaining provider JSON mode where supported. It accepts explicit theme names in JSON string lists, agreed name aliases (`label`, `title`, `theme_name`), theme-ID mappings, and bounded numbered/bullet lists. Broken JSON and explanatory paragraphs are rejected. Complete explicit reasoning blocks are excluded; reasoning-only or unclosed blocks do not become theme evidence.
- Prefer 3–5 themes in the prompt, but accept actual 1–8 themes consistently in planning, small-graph validation, and the frontend. We do not fabricate themes to satisfy a count. Duplicate/conflicting theme names or IDs are rejected. Zero-/text-ID plans are reindexed before assignments; original paper IDs are never reindexed.
- Added safe provider-format diagnostics: `output_limit`, `empty_response`, or `invalid_structure`. Incomplete output with a token-limit stop gets a specific message. A complete valid answer at the limit is still accepted. No raw provider response, credentials or paper text is logged.
- No additional automatic paid retries or changes to the assignment batch size.

Validation: 118 Python tests pass with the uploaded Physics-informed lithium-battery recycling PDF enabled as an optional local fixture (52 references; mocked planning plus 5 assignment batches, 6 requests). Browser checks accept 2 and 6 themes, verify partial results, and preserve prior graph state on invalid memberships. Provider responses are mocked, including output-limit responses from MaxPlus, Gemini and Claude; live MaxPlus generation is not verified. The uploaded PDF is not committed.
