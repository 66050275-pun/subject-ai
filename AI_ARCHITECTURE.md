# PaperRef Finder AI architecture

Request flow: browser → FastAPI → local PDF extraction / public academic metadata → selected AI provider → Pydantic validation → browser cards / SVG graph / chat.

## Modules and implementation order

1. `ai_engine.py`: stateless adapters for OpenAI Chat Completions, Gemini generateContent, Claude Messages, and OpenAI-compatible MaxPlus / Alibaba Cloud Model Studio; provider defaults, bounded prompts, JSON decoding and generic errors. No SDK or credential database.
2. `extractor.py`: `extract_citation_contexts` finds numbered markers, ranges, author/year heuristics and internal bibliography link positions. Returns up to three snippets per reference with page and method. `extract_bibliography_text` isolates bibliography or proposes trailing pages if no heading exists. Regex extraction remains the default.
3. `resolver.py`: `enrich_ai_references` fetches DOI abstracts with six concurrent requests; retains records when abstracts are unavailable. Crossref/OpenAlex supply abstract evidence. Existing metadata lookup also uses Semantic Scholar.
4. `ai_features.py`: multipart endpoint orchestration, Pydantic input/output schemas, evidence assembly, source extraction, validated IDs, and AI reference fallback followed by normal resolution. `main.py` mounts this router and adds contexts to `/api/references`.
5. `static/index.html`: provider/model/key inputs; citation intent buttons, reference selection, synthesis, themed graph, chat and explicit PDF fallback. Text is rendered with `textContent`.

## Endpoints

| Endpoint | Purpose | Inputs / output |
|---|---|---|
| GET `/api/ai/providers` | Default provider models | `providers` map |
| GET `/api/ai/alibaba-models` | Suggested Model Studio model names | optional regional `base_url` → `models`, `verified:false`; no key check or upstream call |
| POST `/api/summarize` | Summary | PDF or DOI → `summary` |
| POST `/api/ai/intents` | Citation intent | paper IDs + PDF contexts → `intents`, `contexts` |
| POST `/api/ai/synthesis` | Compare 3–5 references with source | 3–5 selected papers → `synthesis` |
| POST `/api/ai/clusters` | 3–5 themes | 3–100 papers → `clusters[{name,ids}]` |
| POST `/api/ai/qa` | Grounded conversation | question + up to 10 history messages + selected papers → `answer` |
| POST `/api/ai/extract-references` | Explicit paid bibliography fallback | PDF → resolved `results` with original numbering |

All source-paper POST AI requests use multipart fields `provider` (`openai`, `gemini`, `claude`, `maxplus`, `alibaba`), editable `model`, exactly one `file` or `doi`, and header `X-AI-API-Key`. MaxPlus and Alibaba also accept `base_url`, restricted to their supported official hosts. Summary also accepts the old `X-OpenAI-API-Key` for compatibility. Other features use a `payload` JSON form field:

```json
{
  "papers": [{"id": 1, "title": "Paper title", "doi": "10.1234/example", "year": "2024", "original_text": "Bibliography entry"}],
  "question": "What limitations are supported by the supplied evidence?",
  "history": [{"role": "user", "content": "Previous question"}]
}
```

`papers` is empty for bibliography fallback. `question`/`history` apply to Q&A. The interactive API documentation at `/docs` contains complete generated schemas. Endpoint handling uses one internal route `/api/ai/{feature}` with an allowed feature enum.

## Evidence, credentials and limits

An opt-in Economy profile adds multipart fields `economy_mode` (default `false`)
and `economy_interval` (default 30, 30–120 seconds) to every AI POST, including
workspace comparison. `ai_economy.py` supplies bounded local evidence selection
and a request-local serial/paced caller. Successful responses add `economy`
metadata with request counts, prompt character counts, configured output token
limits and evidence notices. The shared frontend implements a bounded page-memory
success cache and an additional gate between user actions. Detailed behavior,
quota tradeoffs and account limitations are in [AI_ECONOMY_MODE.md](AI_ECONOMY_MODE.md).

Bibliography extraction also accepts `retry_unavailable=false` by default. With
explicit opt-in, Gemini and Economy enabled, one upstream HTTP 503 may trigger
one extra request for the failed excerpt, with a cancellable interval/Retry-After
cooldown. The limit is one additional call across the entire action, not per
batch. No other provider, feature or error type is retried. Usage counts include
the extra call; success reports `retried_extraction_batches`. Keys and recovery
state stay request-local. Upstream 503 is returned as 503 with a safe capacity
message; numeric Retry-After seconds are allowlisted and bounded to 300.

- Users explicitly invoke each paid feature. The key remains in the password field for reuse until cleared, provider changes or the page closes; no local/session storage, server persistence, key query parameters or server fallback key. Provider request failures return generic messages, without echoing credentials or response bodies.
- Input PDFs are capped at 50 MB; source context at 18,000 characters. Long source bodies contain an explicit middle omission marker. Q&A cannot reliably answer about omitted passages; no full-text retrieval index is implemented.
- Bibliography fallback accepts up to 120,000 candidate characters and 1,000 merged references. It packs recognizable whole entries into batches of at most 6,000 characters / 16 entry units; unstructured long fragments have a 350-character overlap. MaxPlus runs one batch at a time; other providers run at most two concurrently. Each call is capped at 6,000 output tokens with a 180-second read timeout. Retries require the explicit Gemini Economy recovery option described above. Serial execution reduces simultaneous calls but does not override provider request/token quotas; HTTP 429 reports numeric Retry-After guidance when supplied.
- Clustering covers supplied bibliography records, not undiscovered papers. Cluster IDs must cover every input ID exactly once; 3–5 nonempty groups required. Topic names are AI suggestions. Colors supplement the graph; actual citation edges remain unchanged.
- Metadata abstracts are bounded to 2,500 characters per selected paper; clustering uses title 250 / abstract 400 characters per record. Missing abstracts are labelled `title/metadata only` in AI context. Synthesis gaps are hypotheses, not verified conclusions.
- Intent analysis sends one bounded snippet per record. All available snippets remain on reference cards. DOI alone does not provide in-text citation contexts, so intent becomes `Unknown` unless a PDF supplies evidence.
- Context extraction is heuristic, especially author/year citations, superscript numbers and scanned PDFs. It does not claim exhaustive citation detection; existing internal-link counts retain their meaning.
- JSON output is validated, unknown/duplicate/missing IDs rejected, missing context forces `Unknown`. AI fallback DOI values absent from the candidate bibliography are removed. AI bibliography output still requires comparison against the source PDF; title/author correctness cannot be guaranteed by JSON validation.
- Public academic requests and AI requests require network access from the server. Model availability, price and provider credentials depend on the user's account; model fields are editable. No live provider calls were made during implementation.

## Migration and local use

```bash
cd subject-ai
git pull origin main
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn main:app --reload
```

Existing `/api/references` and `/api/doi` responses remain compatible; PDF result records additionally contain `citation_contexts`. The old summary input header is supported. AI features have their own router so the public lookup flow does not depend on an API key.

## Gemini diagnostics

`GET /api/ai/gemini-models` accepts `X-AI-API-Key` and lists models supporting `generateContent` using Gemini ListModels. The UI exposes this as **ตรวจคีย์และโหลดโมเดล Gemini**. It sends no paper and generates no content. Listing models does not guarantee generation quota, billing or model access.

Model inputs accept both `gemini-2.5-flash` and `models/gemini-2.5-flash`; the prefix is normalized before constructing the API URL. Matching surrounding quotation marks are removed from pasted keys. No assumption is made about a key's prefix.

Known upstream error reasons are translated into actionable messages (invalid/expired key, API restrictions, disabled service, missing billing). HTTP 400 is returned as 400, missing model as 422, permission errors as 401/403 and quota errors as 429. Upstream temporary-unavailability HTTP 503 is returned as 503; unknown upstream service failures remain 502. Server logs contain only provider, upstream HTTP status and an allowlisted reason code, never keys or raw response bodies.

HTTP 402 (Payment Required) is passed through with a billing/credits explanation rather than converted to 502. This status alone does not identify whether billing setup, credit balance or model eligibility caused the rejection. The UI offers an explicit dropdown of Gemini ListModels names; it does not automatically switch models or issue generation requests.

## MaxPlus AI (OpenAI-compatible relay)

Select `maxplus` in the provider picker, enter a MaxPlus key, set the base URL (default `https://api.maxplus-ai.cc/v1`), then load/select a model. There is no assumed default model and no automatic model switching. All six AI tools use the same adapter.

- AI POST forms accept optional `base_url` for this provider. Root URLs normalize to `/v1`; a full `/chat/completions` URL is normalized to its base. The server permits only HTTPS on `api.maxplus-ai.cc`, without credentials, ports, query strings or redirects.
- `GET /api/ai/maxplus-models?base_url=...` uses `X-AI-API-Key`, requests `GET {base_url}/models` with Bearer authentication, and reads `data[].id`. Listing generates no AI answer and sends no document.
- Generation requests `POST {base_url}/chat/completions` using Bearer authentication, `messages`, `model`, and legacy-compatible `max_tokens`. It reads `choices[0].message.content`. Structured tools request JSON through the prompt and retain server-side schema validation; the adapter does not require the relay to implement `response_format`.
- Source excerpts go to MaxPlus when this provider is selected. Keys remain transient and billing belongs to the MaxPlus account. This adapter assumes OpenAI compatibility; documentation and live behavior could not be verified because the execution environment blocks this domain.
- Mocked checks passed for URL boundaries, authentication and request construction, model listing, JSON decoding and endpoint validation. No actual provider keys or paid calls were used.

## Alibaba Cloud Model Studio

Alibaba Cloud Model Studio uses provider `alibaba` and defaults to `qwen-plus`.
All six AI tools and saved-workspace comparisons use the same adapter and selected
regional endpoint. The settings dropdown supports Singapore, Beijing, Virginia
and Hong Kong. Changing provider or Alibaba region clears the key; optional
remembered settings contain only provider/model, so the region starts at Singapore
after reload. Suggested model names do not validate credentials or generation
access. See [ALIBABA_MODEL_STUDIO.md](ALIBABA_MODEL_STUDIO.md) for API details,
official sources and validation limits.

## Frontend workspace and extraction provenance

The redesigned interface has an import workspace, reference library and AI Research Lab. PDF import offers two distinct actions: **สกัดด้วยโค้ด** (no AI credentials) and **สกัดด้วย AI** (explicit paid provider request). DOI lookup remains its own source.

Code, AI and DOI results are kept as separate browser-memory datasets. Each dataset has its own count, selected references, theme colors, analysis output and Q&A history. Switching datasets changes the cards, graph, filters and exports to that dataset. AI extraction never overwrites code extraction. Cards and exported records carry `extraction_source`; filenames include `code`, `ai` or `doi`. A new PDF or successful DOI search resets the previous paper's datasets. Failed extraction leaves successful datasets available. Reloading the page clears these temporary results.

`static/app.css` supplies the new desktop/mobile layout, visible focus states and reduced-motion behavior. The reference graph is in an expandable panel so long graphs do not push the list down by default. The page remains styled when external utility/font CDNs are unavailable.

Chromium checks used mocked responses, covering separate counts and selections, exports, failure preservation, new-paper reset, DOI switching, all AI UI actions, graph zoom and mobile overflow. No real AI calls were made for this redesign.


## Large-bibliography timeout handling and paper atlas

`extractor.split_bibliography_batches` divides the candidate text before AI requests. `ai_features.extract_bibliography_result` validates each batch, merges in input order, preserves printed numbers, deduplicates overlapping entries and rejects conflicting numbered results. It reports detected bracketed reference numbers missing from the AI output as `extraction_warnings`. Unnumbered entries receive collision-free IDs. JSON validity and these checks cannot guarantee bibliographic accuracy.

The existing extraction POST returns JSON by default. With multipart `stream=true`, it returns `application/x-ndjson` events: `progress` (extracting/resolving, completed, total), `heartbeat` (15-second idle interval), `result` (payload), or `error` (status/detail). The frontend keeps the old dataset until a final result arrives. After streaming starts, HTTP status remains 200; clients must inspect error events rather than treating 200 alone as success. Failed batches cancel remaining local tasks; provider-side work already accepted may still be billed. No default automatic retries or background credential storage are introduced; the optional Gemini Economy recovery permits one additional request only with explicit opt-in.

The application read timeout is configured per extraction call as `timeout_seconds=180` in `ai_features.py`; other tools retain 90 seconds. `ai_engine.py` distinguishes local read timeouts from upstream HTTP 504. A gateway timeout imposed by MaxPlus or another provider cannot be extended by changing the app's timer. Streaming avoids a single silent browser request, but production reverse proxies must also permit long-lived responses. More batches mean more requests and additional prompt overhead. Candidate limits are enforced rather than silently truncating the bibliography. Other whole-network AI analyses retain their 100-reference input limit; synthesis and Q&A use selected references.

`static/graph.js` renders a paper atlas: source at the top, access-status or AI-theme grouping hubs, labelled paper cards and curved edges. Grouping hubs organize the display, not additional citation claims. Cards/groups/background can be dragged, zoom is cursor-anchored, and Fit shows the full network. Clicking or focusing a paper opens its full title and access status, with actions to jump to its card or select it for synthesis. Node text uses SVG textContent. All dataset/provenance behavior remains intact.

Mocked backend checks covered 64 references across multiple batches, bounded concurrency, progress events and failed-stream behavior without partial success or key exposure. Chromium checks covered the streamed UI, graph preview/selection/zoom, all existing AI actions, source switching, exports and mobile layout. Live provider timing and billing were not tested.

## Shared Open Access discovery

`open_access.py` adds multi-copy repository discovery to the existing resolver used by code, AI and DOI extraction. `oa_features.py` exposes `POST /api/open-access/search` for explicit streamed deep searches; this endpoint does not invoke AI or use AI keys. The UI preserves extraction provenance, reference IDs/counts, citation context, selection and old PDF candidates. New optional server credentials are academic-service credentials, scoped to their own API hosts. See [OPEN_ACCESS.md](OPEN_ACCESS.md) for source contracts, access requirements, matching rules and download-verification limits.

## Cluster response compatibility

Structured responses accept one complete JSON object or array, including a single
Markdown JSON fence or a prose prefix. Truncated/ambiguous JSON remains an error;
no automatic paid retry is issued. Cluster results normalize `topics`/`themes`,
`topic`/`theme`/`label`, `paper_ids`/`reference_ids`/`references` and numeric string
IDs to the canonical `clusters[{name,ids}]`. Repeated IDs within a single group are
deduplicated; unknown IDs, noninteger IDs and membership in multiple groups fail.
3–5 named semantic groups are still required. References omitted by the model are
returned as `unassigned_ids` with a warning and shown in a separate grey “ยังไม่จัดกลุ่ม”
group, rather than inferred into a theme or dropped. The frontend validates the
entire partition before replacing colours and saves successful graph themes.
Cluster requests outside the existing 3–100 reference limit are explained before
sending the PDF/key. These checks use mocked provider responses, not live keys.

## Batched graph clustering

For 13–100 references, `/api/ai/clusters` now plans 3–5 shared themes from a
bounded title catalogue, then assigns references to those fixed theme IDs in
serial batches of at most 12 records / 7,000 evidence characters. Abstracts are
bounded to 350 characters and titles to 200 per assignment. A batch returns only
paper ID/theme ID pairs; it does not repeat abstracts or reasons. Missing/null
assignments stay grey. Planned but unused themes may have empty memberships.
At most 12 references keep the previous single-call path.

`stream=true` returns NDJSON progress for metadata, planning and assigning,
heartbeats, then one final result or error. All results are merged before graph
colours change. Request count is one plan plus assignment batches (54 short
records: 6 calls). More calls have provider cost/quota implications. No paid
retry, model switch or partial graph replacement is automatic. Serial batches
reduce response size but cannot override provider quota or reasoning/token limits.
Existing JSON clients remain supported. The frontend shows progress and the final
batch/request count using the same transport as bibliography extraction.
