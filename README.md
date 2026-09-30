# PaperRef Finder

PaperRef Finder accepts a research PDF or DOI. For PDFs it extracts numbered or author-date bibliography entries, counts internal citation links when the PDF includes them, and resolves titles, DOIs, paper records, and Open Access PDFs through Crossref, OpenAlex, and Semantic Scholar. When a reference has no reliable title in the bibliography, it searches with the full citation and uses the provider's title when available. The Google Scholar fallback searches the matched title or full citation instead of a short parsing fragment. DOI lookup queries the same scholarly metadata sources directly.

The reference graph is draggable and zoomable. AI summaries can be requested for a PDF or DOI abstract using the user's own OpenAI API key. The key is sent only for that request and is not saved by the app. A PDF's extracted body text is sent to OpenAI only after the user presses the summary button; DOI mode retrieves an abstract from Crossref/OpenAlex and sends that abstract to OpenAI. OpenAI usage is billed to the user's account.

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

1. Enter a DOI or DOI URL to find one paper, or select/drag in a research PDF up to 50 MB. PDF reference extraction works best with selectable text and a References, Bibliography, Works Cited, or Literature Cited section near the end.
2. Click **ค้นหารายการอ้างอิง** for a PDF or **ค้นหาด้วย DOI** for one DOI. Bibliography lines are queried as complete citations when their title is missing or uncertain.
3. To request an AI summary, select a PDF or enter a DOI, enter your own OpenAI API key, and click **สรุปงานวิจัย**. The key is cleared from the page after the request. DOI summaries require an abstract indexed in Crossref or OpenAlex; if none is available, provide the PDF instead. You can choose another OpenAI model in the model field.
4. Use the graph to jump to a reference. Drag nodes to rearrange them, drag the background to pan, and use the wheel or zoom buttons to change scale. The References and Citation tabs separate the bibliography from PDF-linked citations.
5. Search the result list, then export results as CSV or JSON.

Crossref's polite-pool contact is optional. Set `PAPERREF_CONTACT_EMAIL` before starting Uvicorn to include it in the Crossref User-Agent. The browser loads Tailwind CSS and fonts from public CDNs.

## API endpoints

- `POST /api/references` — PDF upload, bibliography extraction, and metadata resolution
- `POST /api/doi` — DOI metadata lookup (`doi` form field)
- `POST /api/summarize` — multipart PDF or DOI and optional `model` form field; provide the API key using the `X-OpenAI-API-Key` header

## Layout

- `main.py` — FastAPI endpoints, upload checks, and static app route
- `extractor.py` — PyMuPDF extraction, bibliography parsing, citation-link counting, DOI extraction, and bounded summary text
- `resolver.py` — Crossref/OpenAlex/Semantic Scholar lookup and DOI metadata retrieval
- `static/index.html` — responsive interface, interactive graph, and CSV/JSON export
- `requirements.txt` — Python dependencies
