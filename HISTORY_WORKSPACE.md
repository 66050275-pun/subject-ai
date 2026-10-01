# History & Research Workspace

The existing app now uses the same workspace on FastAPI and the Streamlit
component. History is stored in IndexedDB on the browser origin. No login,
cloud sync, server history database or server PDF files are introduced.

## Architecture and implemented sequence

1. `main.py` / `web_security.py`: bound API request bodies to 64 MB and configure
   Starlette multipart spooling above that bound, so permitted files remain in RAM.
   Existing endpoints still reject PDFs over 50 MB. `extractor.py` reads explicit
   PDF metadata without writing files; unknown publication years remain unknown.
2. `resolver.py`: retain authors, abstracts and journal metadata when available
   from existing lookups. No additional network lookup is required for history.
3. `static/history.js`: whitelist metadata, summaries/synthesis and numeric graph
   state before IndexedDB writes/imports. Existing version-1 histories/backups
   remain readable; new optional fields need no destructive database migration.
4. `static/workspace.js`: local shared-reference analysis and BibTeX generation.
5. `ai_features.py`: `POST /api/ai/compare` uses the same `ai_engine.generate`
   adapters. `streamlit_transport.py` explicitly allowlists the new route.
6. `static/index.html`, `static/settings.js`, `static/graph.js`: history filtering,
   selection, privacy switches, instant restore, saved AI output and graph state.
   `streamlit_frontend.py` bundles the same scripts into the component/CSP.

## Client API (`window.PaperRefHistory`)

| Function | Behavior |
| --- | --- |
| `initDB()` | Promise resolving to whether local storage initialized successfully |
| `saveSession(payload, kind, id = null)` | Save a sanitized result; `kind` is `code`, `ai` or `doi`; update only an existing ID |
| `getSessions()` | Promise of cloned stored sessions |
| `deleteSession(id)` | Delete an entry transactionally |
| `clearAll()` | Clear entries; does not change recording preferences |
| `searchSessions(query)` | Cloned cached sessions filtered by source title, authors or year |
| `exportJSON()` | Return a versioned JSON-safe backup object; UI downloads it |
| `importJSON(object)` | Use the same validation/import path as the JSON upload control |
| `onRestore(handler)` | Handle an offline snapshot restore without academic API requests |

Stored session: `{id, kind, savedAt, payload}`. Allowed payload fields include
source metadata, resolved references/citing works, bibliography text, author/year,
abstracts, access links, saved AI summary/synthesis, graph positions/zoom/themes.
Secret fields/headers and unknown nested objects are discarded, not copied with
object spread. Known current key strings are redacted from saved text; URLs with
credential query fields are rejected. Graph coordinates are finite and bounded.
API keys, Authorization/X-AI headers, binary PDFs, full paper body text, citation
context snippets and Q&A conversation history are not stored.

A snapshot is limited to 1 MB; newest 50 entries / 8 MB are retained by original
search time (FIFO). Editing graph/summary does not move an old entry to the front.
Deleting a history item prevents delayed update callbacks from recreating it.
Updates to a result replace its existing history entry. Backups are limited to
16 MB / 50 entries and validated before one atomic transaction; malformed,
oversized and failed writes preserve existing data. Storage denial does not stop
searches. The app does not encrypt IndexedDB: another person using the same browser
profile can inspect it. Export before clearing site data or changing browser/app URL.

Recording can be disabled persistently. The Incognito/Ephemeral switch separately
blocks all history creation/updates in the current tab until turned off; it does
not erase existing entries and resets on reload. Summaries may include sensitive
research information: use this mode when appropriate.

## Shared references and BibTeX

Select at least two distinct source papers. Different code/AI snapshots of the same
source are counted once using source DOI or normalized source title/filename.
Reference identity prefers canonical, case-insensitive DOI. A DOI-less reference
may match a unique known DOI by an exact normalized title of at least 15 characters.
Conflicting DOIs are kept separate; title-only matches carry a verification label.
Duplicate references within one source do not inflate counts.

This is overlap of reference lists (shared cited papers), not a complete co-citation
network or proof of seminal status. Highlighted results are candidates to inspect.
No academic API is called for this operation.

Each history card exports its bibliography; the selected-group button exports the
combined, deduplicated references. Output uses `@article` when journal metadata is
known and `@misc` otherwise, with LaTeX-special characters escaped. Unknown authors
or years are omitted. Validate metadata before submitting citations.

## Comparative AI endpoint

`POST /api/ai/compare` accepts multipart form:

- `provider`, `model`, optional `base_url` for MaxPlus
- `payload`: JSON `{papers: [...]}`, exactly 2–3 distinct sources
- API key only via `X-AI-API-Key`

Each selected paper supplies bounded title, DOI, authors, year, abstract, saved AI
summary and at most 100 reference metadata records. Extra fields (including secret
fields) are rejected by Pydantic. MaxPlus HTTPS host allowlisting and the existing
provider timeouts/error handling apply. No Crossref/OpenAlex lookup or PDF read is
performed by this endpoint. Response: `{comparison, provider, model, evidence}`.

Only clicking Compare sends selected evidence and the transient key through the
server to the chosen provider. Results depend on the cached evidence: title-only
histories cannot establish methodology/results. The prompt requires explicit gaps
and separates saved AI summaries from abstracts. Model output is a synthesis for
review, not verified experimental evidence. Displayed comparison remains ephemeral;
individual paper summaries/synthesis are saved only when recording is enabled.

## Validation

```bash
python -m unittest discover -s tests -v
node tests/workspace_logic.cjs
# Optional browser tests; install Playwright in this Python environment first.
python tests/browser_history.py
python tests/browser_workspace.py
```

Browser tests use local fake provider responses, not real keys. They cover stored
summaries/graph restore, author/year filters, shared references, `.bib`, comparison
through the Streamlit ASGI bridge, incognito, import sanitation, FIFO, quota
rollback and browser isolation. API tests validate comparison input/credential
separation and confirm that multipart files larger than 1 MB do not roll to disk.
