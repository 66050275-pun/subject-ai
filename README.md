# PaperRef Finder

PaperRef Finder accepts a research PDF or DOI. For PDFs it extracts numbered or author-date bibliography entries, counts internal citation links when the PDF includes them, and resolves titles, DOIs, paper records, and Open Access PDFs through Crossref, OpenAlex, and Semantic Scholar. When a reference has no reliable title in the bibliography, it searches with the full citation and uses the provider's title when available. The Google Scholar fallback searches the matched title or full citation instead of a short parsing fragment.

DOI lookup displays the paper and its bibliography, plus papers that cite it when indexed. Relationship lists are combined from Crossref, OpenAlex, and Semantic Scholar; up to 100 references and 20 citing papers are shown. Each result is grouped by access status: direct PDF available, record found in an open metadata source, or Google Scholar follow-up needed. A metadata record does not guarantee that its full text is free to download.

The AI tools support OpenAI, Google Gemini, Anthropic Claude and MaxPlus AI (OpenAI-compatible) with a user-supplied API key. Features include summaries, citation intent, 3–5 reference synthesis, graph themes, paper Q&A and optional bibliography extraction. See [AI_ARCHITECTURE.md](AI_ARCHITECTURE.md) for endpoints, implementation order, evidence limits and privacy behavior.

## Run locally

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

Crossref's polite-pool contact is optional. Set `PAPERREF_CONTACT_EMAIL` before starting Uvicorn to include it in the Crossref User-Agent. The browser loads Tailwind CSS and fonts from public CDNs.

## API endpoints

- `POST /api/references` — PDF upload, bibliography extraction, and metadata resolution
- `POST /api/doi` — DOI paper, its references (up to 100), and a bounded list of citing papers (`doi` form field)
- `POST /api/summarize` — multipart PDF or DOI and optional `model` form field; provide the API key using the `X-OpenAI-API-Key` header

## Layout

- `main.py` — FastAPI endpoints, upload checks, and static app route
- `extractor.py` — PyMuPDF extraction, bibliography parsing, citation-link counting, DOI extraction, and bounded summary text
- `resolver.py` — Crossref/OpenAlex/Semantic Scholar lookup and DOI metadata retrieval
- `static/index.html` — responsive interface, interactive graph, and CSV/JSON export
- `requirements.txt` — Python dependencies

For MaxPlus, choose **MaxPlus AI**, enter its own API key and use the default base URL `https://api.maxplus-ai.cc/v1` or the documented path on the same host. Load/select a model before invoking an AI tool. Live MaxPlus compatibility remains unverified; see AI_ARCHITECTURE.md.

The frontend now separates **Code extraction**, **AI extraction** and **DOI database** results into individual datasets. Switch between them to inspect their own counts, selections, graph and exports. See `static/app.css` for the redesigned responsive workspace.
