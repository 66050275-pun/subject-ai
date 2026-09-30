# PaperRef Finder

PaperRef Finder extracts a bibliography from a text-based research PDF, looks up its records in Crossref, and searches OpenAlex and Semantic Scholar for Open Access PDFs. Results include Google Scholar links even when no direct match is found. It runs locally with Python 3.10+ and needs no paid API key.

## Run locally

```bash
cd subject-ai
python3 -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>. The health endpoint is <http://127.0.0.1:8000/health> and the interactive API documentation is at <http://127.0.0.1:8000/docs>.

## Test the workflow

1. Open the page and select or drag in a research PDF no larger than 50 MB. Use a PDF with a selectable text layer and a References, Bibliography, Works Cited, or Literature Cited heading near the end.
2. Click **ค้นหารายการอ้างอิง** and wait for Crossref/OpenAlex lookups. The results show the original citation, extracted title and year, any matched title and DOI, a direct paper link, an Open Access PDF link when available, and a Google Scholar fallback.
3. Search the result list, then use the CSV and JSON buttons to download the results.
4. To check error handling, try a non-PDF file, a PDF over 50 MB, and a scanned PDF without selectable text. The app should report each problem clearly.
5. For an API-only check, run `curl -F 'file=@/path/to/research.pdf' http://127.0.0.1:8000/api/references` in another terminal.

Crossref's polite-pool contact is optional. Set `PAPERREF_CONTACT_EMAIL` to your own contact address before starting Uvicorn to include it in the Crossref User-Agent. The default User-Agent identifies the application and does not invent an email address. The browser loads Tailwind CSS and the Noto Sans Thai/DM Sans fonts from their public CDNs.

## Layout

- `main.py` — FastAPI upload endpoint, size/type checks, and static app route
- `extractor.py` — PyMuPDF extraction, bibliography heading detection, splitting, and title/year heuristics
- `resolver.py` — bounded asynchronous Crossref/OpenAlex/Semantic Scholar lookup
- `static/index.html` — responsive Tailwind/vanilla JavaScript interface and CSV/JSON export
- `requirements.txt` — Python dependencies
