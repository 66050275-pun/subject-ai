# Open Access discovery

| Source / documentation | Evidence and coverage | Configuration |
| --- | --- | --- |
| [Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) | Metadata/DOIs; newly discovered DOIs are reused for exact downstream searches | Optional contact email |
| [OpenAlex](https://docs.openalex.org/) | All `is_oa` locations with `pdf_url`, not only the best location | `OPENALEX_API_KEY` when required by provider |
| [Semantic Scholar](https://www.semanticscholar.org/product/api) | `openAccessPdf`; external arXiv ID for another copy | Optional `SEMANTIC_SCHOLAR_API_KEY` |
| [Unpaywall](https://unpaywall.org/products/api) | All DOI-linked OA locations, including university repositories and publishers | Required contact email in UI or `PAPERREF_CONTACT_EMAIL` |
| [Europe PMC](https://europepmc.org/RestfulWebService) / PMC | Matched `isOpenAccess=Y` records only; OA PDF links and PMC PDF route | None |
| [arXiv](https://info.arxiv.org/help/api/user-manual.html) | Explicit ID or conservative title search, labelled preprint | None; 3-second spacing |
| [HAL](https://api.archives-ouvertes.fr/docs/search/) | Matched DOI/title and `fileMain_s` author deposit | None; deep search |
| [Zenodo](https://developers.zenodo.org/) | Open publication records, exact DOI or same-work/version DOI relationship; PDF files only | None; deep search |
| [CORE](https://core.ac.uk/services/api) | DOI/title and repository `downloadUrl` | `CORE_API_KEY`; deep search |

University repositories are covered through aggregators rather than a hardcoded list. A metadata record alone does not count as a PDF. Repository versions may differ from the cited published version; licensing/reuse conditions come from the source.

## API and matching

Code extraction, AI extraction and DOI relationship resolution all call `resolver.resolve_references`. The basic pass adds Unpaywall/Europe PMC and explicit arXiv IDs. `POST /api/open-access/search` searches all sources, including Semantic Scholar when a PDF already exists and arXiv title search/HAL/Zenodo/optional CORE. No AI request is made.

Input: `{papers: [{reference_number, title, matched_title, original_text, doi, year}], contact_email?}`; 1–1,000 entries, unique IDs. NDJSON events: `progress`, `item` (`result`, `completed`, `total`), `heartbeat`, `done`, `error`. HTTP 200 means streaming started, not guaranteed success. Results add `pdf_locations` with URL, source, version, license, landing URL, host type, `matched_by`, and `verification=provider_indexed`. `oa_pdf_url` remains the preferred link for existing clients. `oa_search` records source failures/skipped configuration. The UI updates its original dataset by ID, preserving old PDF candidates, citation context, intent and selection.

Exact DOI equality takes priority. A conflicting DOI is rejected even with an identical title. Title-only matches need at least 20 normalized characters and similarity >= 0.93. Existing metadata title matching is tightened. Zenodo records that merely cite the DOI and datasets are excluded. DOI-linked versions use `matched_by=related_doi`, not a citation edge. PDF URLs are deduplicated without fragments; prefer published, accepted, then submitted versions when explicitly known. Unknown versions remain unknown.

## Limits and privacy

- Four deep reference lookups run concurrently. Each new source permits at most two calls (arXiv one), with per-session request memoization. arXiv waits at least 3 seconds between calls. Large searches can take minutes.
- New-source 401/403/429 stops further calls to that source for that session. No retries. Other tabs/processes still share provider quotas. Each API request has an 8-second timeout.
- Keys are scoped to their own API hosts. The UI contact email is used only for the lookup session. No credentials or cache are persisted to disk.
- Only fixed academic API URLs are fetched. Returned PDF/landing URLs are browser links and never followed by the server. This code has no arbitrary-site scraping, server PDF proxy, publisher login or paywall bypass. New-source API redirects are not followed.
- Provider-listed PDF links can be blocked, expire or fail. This release does not verify PDF bytes or guarantee any coverage percentage. A source outage is not proof that no PDF exists.

## Validation

Run `.venv/bin/python -m unittest discover -s tests -v`. Mocked contracts cover alternate copies, null locations, discovered DOI reuse, wrong-DOI rejection, closed PMC entries, Zenodo citing records/datasets, arXiv version labels, source circuit/memoization, credential scoping, streamed IDs and provider failures. Browser checks cover deep-search updates, alternate links/export, original selection and mobile layout.

Live API contracts/download yield could not be validated in this managed development workspace: outbound hosts are restricted and the allowed academic API connections also failed. Check the linked provider documentation when formats/access requirements change. No measured increase in successful downloads is claimed.
