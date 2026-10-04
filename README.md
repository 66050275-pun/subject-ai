# PaperRef Finder

PaperRef Finder accepts a research PDF or DOI. For PDFs it extracts numbered or author-date bibliography entries, counts internal citation links when the PDF includes them, and resolves titles, DOIs, paper records, and Open Access PDFs through Crossref, OpenAlex, and Semantic Scholar. When a reference has no reliable title in the bibliography, it searches with the full citation and uses the provider's title when available. The Google Scholar fallback searches the matched title or full citation instead of a short parsing fragment.

DOI lookup displays the paper and its bibliography, plus papers that cite it when indexed. Relationship lists are combined from Crossref, OpenAlex, and Semantic Scholar; up to 100 references and 20 citing papers are shown. Each result is grouped by access status: direct PDF available, record found in an open metadata source, or Google Scholar follow-up needed. A metadata record does not guarantee that its full text is free to download.

The AI tools support OpenAI, Google Gemini, Anthropic Claude and MaxPlus AI (OpenAI-compatible) with a user-supplied API key. Features include summaries, citation intent, 3–5 reference synthesis, graph themes, paper Q&A and optional bibliography extraction. See [AI_ARCHITECTURE.md](AI_ARCHITECTURE.md) for endpoints, implementation order, evidence limits and privacy behavior.

## Run locally

### Streamlit / mobile hosting

To host directly from this GitHub repo on Streamlit Community Cloud, choose **`streamlit_app.py`** as the main file (not `main.py`). See [STREAMLIT_DEPLOY.md](STREAMLIT_DEPLOY.md) for Thai setup steps, mobile access and optional Secrets. Locally run `python -m streamlit run streamlit_app.py` after installing `requirements.txt`. Streamlit renders the exact same HTML, CSS and interactive graph as the FastAPI frontend, with responsive layouts for desktop, phone and tablet. A session-scoped custom component forwards uploads and streamed API responses to FastAPI in-process without a second backend server or resetting the page on reruns. The FastAPI frontend remains available using the commands below.

### FastAPI frontend

```bash
cd subject-ai
python3 -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>. The health endpoint is <http://127.0.0.1:8000/health> and interactive API documentation is at <http://127.0.0.1:8000/docs>.

## Use the app

1. Enter a DOI or DOI URL to find a paper and its References/Cited by lists, or select/drag in a research PDF up to 50 MB. PDF reference extraction works best with selectable text and a References, Bibliography, Works Cited, or Literature Cited section near the end.
2. Click **ค้นหารายการอ้างอิง** for a PDF or **ค้นหาด้วย DOI** for one DOI. Use the access-status chips to filter for direct PDFs, records in open metadata sources, or items that need a Google Scholar search. Bibliography lines are queried as complete citations when their title is missing or uncertain.
3. Select your AI provider, edit its model if needed and enter your API key. Choose a PDF or DOI, then invoke a tool. Select 3–5 references for synthesis. Keys stay only in the page password field until cleared, provider changes or the page closes; usage is billed to your provider account.
4. Use the graph to jump to a reference. Drag nodes to rearrange them, drag the background to pan, and use the wheel or zoom buttons to change scale. The References and Citation tabs separate the bibliography from PDF-linked citations.
5. Search the result list, then export results as CSV or JSON.

Crossref's polite-pool contact is optional. Set `PAPERREF_CONTACT_EMAIL` before starting Uvicorn to include it in Crossref's `mailto` parameter and User-Agent. The browser loads Tailwind CSS and fonts from public CDNs.

## API endpoints

- `POST /api/references` — PDF upload, bibliography extraction, and metadata resolution
- `POST /api/doi` — DOI paper, its references (up to 100), and a bounded list of citing papers (`doi` form field)
- `POST /api/open-access/search` — JSON bibliography and optional contact email; streamed repository search without an AI key
- `POST /api/summarize` — multipart PDF or DOI and optional `model` form field; provide the API key using the `X-OpenAI-API-Key` header

## Layout

- `main.py` — FastAPI endpoints, upload checks, and static app route
- `extractor.py` — PyMuPDF extraction, bibliography parsing, citation-link counting, DOI extraction, and bounded summary text
- `resolver.py` — Crossref/OpenAlex/Semantic Scholar lookup and DOI metadata retrieval
- `static/index.html` — responsive interface, interactive graph, and CSV/JSON export
- `requirements.txt` — Python dependencies

For MaxPlus, choose **MaxPlus AI**, enter its own API key and use the default base URL `https://api.maxplus-ai.cc/v1` or the documented path on the same host. Load/select a model before invoking an AI tool. Live MaxPlus compatibility remains unverified; see AI_ARCHITECTURE.md.

The frontend now separates **Code extraction**, **AI extraction** and **DOI database** results into individual datasets. Switch between them to inspect their own counts, selections, graph and exports. See `static/app.css` for the redesigned responsive workspace.

Large AI bibliographies are extracted in smaller batches with live progress. The extraction read timeout is 180 seconds per batch, with no automatic paid retries. The graph now groups labelled paper cards by access status or AI themes, supports drag/zoom/Fit, and opens an inspector for selection and navigation. Upstream provider timeouts can still occur.

## Find more Open Access PDFs

Both code and AI extraction now use the same expanded resolver: OpenAlex's full list of OA locations, Semantic Scholar, Unpaywall, Europe PMC and explicit arXiv IDs. Click **ค้น PDF เพิ่มทุกแหล่ง** to also search HAL, Zenodo, arXiv titles and CORE, even for references that already have a PDF. This does not use AI credits. Expand **ดู PDF ทุกฉบับ / ลิงก์สำรอง** to choose a host/version. Published, accepted and preprint versions are labelled when known. **ส่งออกลิงก์ PDF** exports URLs for the current filter, not the PDF bytes. JSON/CSV include all copies and source diagnostics. Counts/IDs and citation/AI context remain unchanged.

Unpaywall requires a contact email: enter it in the OA panel or set `PAPERREF_CONTACT_EMAIL`. The UI email is sent to Unpaywall and is not persisted by this app. CORE requires an academic API key. OpenAlex/Semantic Scholar keys may be needed for current access/quotas. These are separate from AI provider keys. Set only values you have before starting the server:

```bash
export PAPERREF_CONTACT_EMAIL='you@example.com'
export OPENALEX_API_KEY='your-openalex-key'
export SEMANTIC_SCHOLAR_API_KEY='your-semantic-scholar-key'
export CORE_API_KEY='your-core-key'
uvicorn main:app --reload
```

Missing optional credentials skip that service; other sources continue. arXiv requests are spaced at least 3 seconds apart, so large deep searches may take several minutes. See [OPEN_ACCESS.md](OPEN_ACCESS.md) for source documentation, matching rules and limits. URLs are provider-indexed, not verified downloads; redirects, unavailable hosts and expired links can still prevent downloading. The server does not download or proxy PDF files, and no coverage percentage is guaranteed.

PDF layout improvements, numbered coverage recovery and offline corpus evaluation are documented in [PDF_EXTRACTION_TESTS.md](PDF_EXTRACTION_TESTS.md). Titleless entries remain available as full citations; AI omissions are labelled separately from generated results.

The code-only extractor now handles wrapped author-date lists and additional citation styles, preserves printed authors and short titles, and reports numbered coverage/uncertainty without an AI key. See [CODE_EXTRACTION_REPORT.md](CODE_EXTRACTION_REPORT.md) for the before/after corpus results and remaining limits.

Title recovery now searches omitted-title citations by author/year/volume/first page, repairs author-only headings, and supports saved-history refresh without an AI key. See [TITLE_METADATA_RECOVERY.md](TITLE_METADATA_RECOVERY.md) for provider configuration, optional Google Scholar through SerpAPI, matching rules and live-test limits.
