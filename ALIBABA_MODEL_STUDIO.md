# Alibaba Cloud Model Studio in PaperRef Finder

## Connect from the app

1. Open **⚙ Settings → AI** and choose **Alibaba Cloud Model Studio (Qwen)**.
2. Select the region where your **Model Studio Standard** API key was issued.
3. Enter that key and a text-generation model available to your project. The default is `qwen-plus`; model names remain editable.
4. Choose a PDF or DOI and invoke the desired AI tool. Summaries, citation intent, literature synthesis, graph themes, Q&A, bibliography extraction and saved-paper comparisons share this configuration.

The **suggested models** button returns an app-maintained suggestion, not a live
account catalog or key check. It requires no key, sends no document and makes no
Alibaba request. Successful generation still depends on region, model access,
quota and billing. No free-tier or subscription entitlement is assumed. Coding
Plan keys/endpoints are a separate product and are not supported by this adapter.

## Regional endpoints

| Settings region | Allowed base URL |
|---|---|
| Singapore / International (default) | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` |
| Beijing / Mainland China | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| Virginia / US | `https://dashscope-us.aliyuncs.com/compatible-mode/v1` |
| Hong Kong | `https://cn-hongkong.dashscope.aliyuncs.com/compatible-mode/v1` |

Keys must match the chosen region. Endpoint region alone does not establish where
inference runs; consult Alibaba's deployment/model rules for your project. The
frontend clears keys and suggestions when the provider or Alibaba region changes.
Optional remembered preferences store only provider/model. After reload the
region starts at Singapore, so select your region again before entering its key.

## Backend contract

- Provider ID: `alibaba`; default model: `qwen-plus`.
- Generation: `POST {base_url}/chat/completions`, Bearer authentication,
  `messages`, `model`, and `max_tokens`. The adapter reads
  `choices[0].message.content` and uses the existing feature-specific validators.
- For the recommended `qwen-plus` alias, requests set `enable_thinking:false`;
  structured tools also request `response_format:{"type":"json_object"}`.
  `qwen3.8-max` and `qwen3.8-flash` use their supported
  `reasoning_effort:"none"` control instead. Other model names receive no assumed
  thinking options. Models besides `qwen-plus` keep prompt-based structured
  output and server validation without assuming JSON-mode support. Thinking-only
  models can still exceed output/time limits; use a compatible text model.
- AI multipart forms accept `base_url`. It defaults to Singapore and permits
  only the exact regional compatible base paths or their `/chat/completions`
  suffix. Other hosts, HTTP, embedded credentials, ports, query strings and
  fragments are rejected before processing documents. Redirects are not followed.
- `GET /api/ai/alibaba-models?base_url=...` returns
  `{models:["qwen-plus"],base_url,verified:false,source:"suggested",warning:...}`.
  No authentication is needed for this local suggestion. The app does not assume
  an undocumented OpenAI-compatible `/models` endpoint exists.
- All existing AI routes and `/api/ai/compare` accept this provider. The Streamlit
  transport forwards its local model-suggestion route and the existing AI forms.

Keys remain transient in the page and request headers; the app does not add server
keys, credentials to URLs, browser-storage keys or history fields. Necessary
research excerpts/metadata pass through the server to the selected Alibaba
endpoint when the user invokes AI. PDFs/history are not written to server disk.
Existing batching, progress events, bounded prompts, timeouts, schema checks and
no automatic paid retries remain in effect. Upstream 401/403 and 404 errors explain
region/key/model checks; 429 retains quota/Retry-After guidance without exposing
raw provider responses.

## Evidence and verification

Implementation uses Alibaba/Qwen's official sources:

- [Qwen Model Studio Standard presets](https://github.com/QwenLM/qwen-code/blob/292c49ec4a86416d7884cce0d355cc955fe69e50/packages/core/src/providers/presets/alibaba-standard.ts): compatible API protocol and four regional endpoints.
- [DashScope SDK README](https://github.com/dashscope/dashscope-sdk-python/blob/2cd356a499e7d70dc28035b34fd9ee1ad2d12572/README.md): regional keys, `qwen-plus`, and deployment differences.
- [Qwen request tests](https://github.com/QwenLM/qwen-code/blob/292c49ec4a86416d7884cce0d355cc955fe69e50/packages/cli/src/commands/batch-docs.test.ts): `qwen-plus` request shape with `max_tokens` and thinking disabled.
- [DashScope generation API](https://github.com/dashscope/dashscope-sdk-python/blob/2cd356a499e7d70dc28035b34fd9ee1ad2d12572/dashscope/aigc/generation.py): JSON response format; support remains model-dependent.

Backend tests mock upstream HTTP and cover endpoint restrictions, authentication,
request/response shapes, structured output, safe errors, feature routing and
Streamlit forwarding. `tests/browser_alibaba.py` exercises all seven AI actions
through the actual Streamlit component with mocked provider responses, plus
region/key changes, safe remembered preferences and desktop/tablet/phone layout.
No real API key or paid Alibaba generation was used; account-specific model
availability, costs and production quotas remain unverified.
