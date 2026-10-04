# Automatic recovery of omitted reference titles

## What the uploaded PDF contains

The Physics-informed lithium-battery recycling paper has 52 bibliography entries. They give author names, journals, volume, publication year and pages/article identifiers, but omit article titles. Earlier code populated 4 title fields with journal fragments; those are now correctly empty. The reference count stays 52 and printed authors are now parsed for all 52 entries.

## Implemented flow

New PDF extraction (code or AI) already runs the resolver. The resolver now clears author-only/venue-only headings and searches full bibliographic citations through Crossref, then OpenAlex and Semantic Scholar even when the title/DOI is missing. Omitted-title matches require corroboration from author, publication year, volume, first page or article identifier, and venue when supplied. Two distinct matching records are left unresolved. Known titles and DOI lookups keep their existing paths.

Metadata titles have source provenance and separate `verified` / `from_pdf` / `unresolved` states. The UI and graph use a neutral reference-number label when the title remains missing; the original citation is still displayed on its card. A verified title becomes the Google Scholar link query, including short titles. PDF-access status remains separate: a real title can be known while a PDF still needs a Scholar search.

AI preprocessing recovers missing titles before planning. Recovered titles propagate back to cards, graph and privacy-aware browser history. References without any title or abstract stay neutral/unassigned rather than receiving a theme inferred from author names. When all evidence is missing, no paid AI call is made.

The new **เติมชื่อเรื่องจริง** button calls `POST /api/metadata/search` with current or saved result metadata. It needs neither a PDF upload nor an AI key. It streams progress and completed records, keeps existing PDFs/context/intent, and saves only allowed fields to IndexedDB (unless Incognito/history disabled). Restoring history itself still does not call APIs. FastAPI and the Streamlit transport share this endpoint.

Academic requests have per-operation memoization, serial per-source pacing (1s; Semantic Scholar 1.5s), and a circuit breaker on 401/403/429. No global user cache, server history, PDF persistence, raw credential logging, or automatic paid retries are introduced.

## Google Scholar directly (optional)

Google Scholar has no public official search API for this use. No Scholar HTML scraping or CAPTCHA bypass is implemented. The optional third-party SerpAPI integration uses its `google_scholar` engine and the server-only `SERPAPI_API_KEY`. It runs only after primary metadata sources fail to recover a title. Omitted-title results linked to a DOI are checked against bibliographic metadata; unverified snippets are not presented as confirmed titles. This integration was tested with mocked responses, not a live paid key.

For local Uvicorn, set only keys you actually have before starting:

```bash
export PAPERREF_CONTACT_EMAIL='your-real-contact-email'
export OPENALEX_API_KEY='your-openalex-key'
export SEMANTIC_SCHOLAR_API_KEY='your-semantic-scholar-key'
# Optional, third-party usage may be billed:
export SERPAPI_API_KEY='your-serpapi-key'
python -m uvicorn main:app --reload
```

On Streamlit Community Cloud, set these in Cloud Secrets; see `.streamlit/secrets.toml.example`. Do not commit real keys. AI provider keys stay in the user's existing AI field; they cannot replace academic-service keys. Shared server keys share quota/billing across app users. The base metadata lookup works without an AI key or SerpAPI key; provider quotas still apply.

## Validation and real limits

- 145 Python tests pass, with the uploaded 52-reference PDF enabled as an optional local test fixture. Tests include wrong author/year/venue/volume/first page, competing DOI matches, fallback providers, source429 handling, SerpAPI key isolation, metadata streaming without an AI call, and neutral clustering for missing evidence.
- Browser checks verify metadata refresh, source provenance, preserved PDFs/contexts/intents, updated AI graph names, offline history, Incognito, partial/error streams and source-change cancellation. Existing clustering/browser and workspace checks pass.
- 45 distinct PDFs retain their reference counts; all 9 independent count checks pass after parser changes.
- Live public-API spot checks recovered titles and DOIs for references 1, 2 and 15 from Crossref. Examples: reference 1 “Battery pack capacity estimation for electric vehicles based on enhanced machine learning and field data”, DOI 10.1016/j.jechem.2024.01.047; reference 2 “Electric vehicle batteries alone could satisfy short-term grid storage demand by as early as 2030”, DOI 10.1038/s41467-022-35393-0; reference 15 “Lithium-ion battery aging mechanisms and diagnosis method for automotive applications: Recent advances and perspectives”, DOI 10.1016/j.rser.2020.110048.
- Bulk live checks of all 52 references were quota-limited in this shared execution environment: 5 titles recovered initially, then 1 in a paced follow-up while Crossref/OpenAlex/Semantic Scholar returned 429. Pacing does not restore exhausted quota. These runs do **not** demonstrate complete 52/52 live recovery. Configure your own academic keys/contact where needed, retain successful results and refresh later; completeness depends on indexed records and provider availability.

No uploaded PDFs, API keys or raw unpublished document text are committed.
