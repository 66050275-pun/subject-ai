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
