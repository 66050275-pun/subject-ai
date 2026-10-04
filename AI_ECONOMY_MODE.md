# AI Economy Mode

Open **⚙ Settings → AI → โหมดประหยัด API**. Select 30, 60 or 120 seconds between
calls (default 30). The mode is off by default and lasts only for the current
page; it does not automatically change the provider/model or invoke AI.

It aims to reduce evidence/output size and quota bursts. Provider model access,
regional eligibility, free quotas and billing remain account-specific. A 404
model/endpoint error or a key permission error still needs its own correction.
No paid/free account entitlement is assumed or checked by enabling this mode.

## What changes

| Tool | Economy behavior |
|---|---|
| Summary | At most 6,000 characters of selected source excerpts, concise answer capped at 1,600 output tokens. |
| Paper Q&A | Locally searches up to 90,000 body characters using question terms and neighboring excerpts; sends at most 6,000 source characters plus compact selected references. Keeps four prior messages, at most 600 characters each; answer cap 1,400 tokens. |
| Literature synthesis | Sends at most 6,000 source characters and short reference abstracts; answer cap 2,000 tokens. |
| Graph themes | Retains every input reference ID, sends compact titles/abstracts without the main paper body, and serially plans/assigns themes using smaller output budgets. Missing or failed assignments stay explicitly grey. |
| Citation intent | Sends only witnessed citation snippets in batches of eight. Records without contexts become `Unknown` locally without an AI call. |
| Bibliography extraction | Uses 3,000-character / six-entry batches, with whole recognized numbered entries permitted beyond the soft character budget. Numbered outputs need not repeat the original citation; the server attaches it from the source. Unnumbered output retains the original-text schema. |
| Saved-paper comparison | Compacts the supplied metadata, abstracts, saved summaries and references; answer cap 2,200 tokens. |

Compact reference evidence preserves all IDs, caps titles at 120–160 characters
and distributes at most 3,000 abstract characters across the records. Source
omissions are labelled in prompts; responses show evidence notices. These are
excerpt-based analyses, not full-paper reading. Local Q&A selection is lexical,
not a semantic search index; questions without shared source terms may use general
source excerpts and cannot establish missing evidence. An insufficient-context
answer is preferable to inventing methods/results.

Bibliography input is never truncated to reduce the total reference count.
Recognized individual entries over 20,000 characters are rejected before AI with
a clear instruction to use a cleaner bibliography or ordinary mode. Unstructured
fragments still overlap to retain boundary text. Existing original-text recovery,
number checks, DOI checks and source-comparison warnings remain in effect.

Smaller batches can **increase the number of requests** even while reducing text
per request. Pacing helps per-minute limits; it cannot restore exhausted daily
quota or credits. No automatic retry after 429, failed JSON or timeout is added.

## Gemini temporarily unavailable (HTTP 503)

The local app and the Streamlit component use the same extraction handler and
Economy options. A `503` in a streamed error is an HTTP response from the upstream
service, not proof that Streamlit lost the Economy setting or that billing is
required. Separate attempts may encounter different service availability or
network routes. Increasing the app's read timeout does not repair an explicit
upstream 503.

In **Settings → AI → Economy Mode**, the additional checkbox
**พักแล้วลองซ้ำเมื่อ Gemini ไม่พร้อม (503)** is off by default. It is available
only with Gemini and Economy enabled, and applies only to bibliography extraction.
When selected, it permits at most **one extra provider request for the entire
extraction action**. Only the failed excerpt is repeated; completed excerpts are
not repeated. The app waits at least the selected Economy interval or a valid
numeric `Retry-After` delay (up to 300 seconds), and displays the
countdown. The actual extra request is included in the Economy usage meter.

This option may consume quota or incur provider charges. It does not retry 429,
key/billing/model errors, timeouts or invalid JSON. A second 503 ends the action
and preserves the previous result dataset. The setting is not persisted and is
cleared when the provider/mode no longer qualifies. Changing the option cancels
a frontend action still waiting to be sent, but does not change an extraction
already running on the backend. Backend waits respond to task cancellation. This is temporary-error
recovery, not a guarantee that Google's service will be available.

After deploying a new GitHub version to Community Cloud, refresh the browser to
load the new component UI, then enable Economy and the recovery option again.
If the old UI remains, verify the deployed branch/entry point and reboot the app
from Streamlit's management panel before refreshing. No API key should be posted
in logs or shared when reporting these errors.

## Pacing, reuse and privacy

`EconomyCaller` spaces provider-call starts within an action using a cancellable
async wait and processes Economy batches serially. The frontend prevents
simultaneous AI actions and conservatively waits the selected interval after an
earlier action finishes. Streaming actions continue sending progress/heartbeat
events during waits; the UI displays the countdown. Other browser tabs/apps using
the same account still consume its shared quota.

Successful matching results can be reused in the current page. The cache is
bounded to 12 results / 2 MB, with a 30-minute reuse lifetime. Its fingerprint
includes the source/dataset, feature, provider/model/endpoint, mode settings,
the extraction recovery option and relevant references/question/conversation.
Credentials are never part of cache keys. Credential/source/settings changes invalidate reuse; pending stale actions
are rejected before sending. Failed or incomplete outputs are not remembered.

The cache contains no PDF binary, headers or API keys and never uses IndexedDB,
local storage or server disk. Existing opt-in browser history remains independent.
No server-wide user cache or credential-indexed locks are introduced. Economy
mode and wait settings are not stored in remembered AI preferences.

## API and verification

All seven AI POST actions accept `economy_mode=true` and `economy_interval=30`
(integer 30–120). Existing requests that omit them retain ordinary behavior.
Only bibliography extraction uses `retry_unavailable=true`; it is ignored when
Gemini or Economy is not selected. Its default is false. Successful recovered
extractions include `retried_extraction_batches` with the repeated part number.
Successful Economy responses include `economy.enabled`, `requests`,
`prompt_characters`, `interval_seconds`, `output_token_limits` and
`context_notice`. Character counts describe prompts passed to the adapter, not
provider-billed tokens or exact cost; output limits are configured ceilings.

For exact `gemini-2.5-flash` and `gemini-2.5-flash-lite` model aliases only, Economy
requests set `thinkingConfig:{"thinkingBudget":0}` to keep optional thinking from
using the smaller output budget. Unknown, Pro and Gemini 3.x model settings are
not inferred from their names. Official examples:
[thinking controls](https://github.com/google-gemini/cookbook/blob/archive/generate-content-api/quickstarts/Get_started_thinking.ipynb)
and [REST format](https://github.com/google-gemini/cookbook/blob/3f6cdf049c3efee9b91c26ab2cb8fe4395c6603c/quickstarts/Get_started_thinking_REST.ipynb).

Tests use mocked providers and virtual clocks to verify compact evidence, middle
passage retrieval, complete reference IDs, original-citation preservation,
serial spacing, waiting events, cancellation, safe cache reuse and unchanged
ordinary behavior. No real user key or paid generation is used, so actual
free-account success, provider timing, quota and costs remain unverified.

A local check with the previously supplied 52-reference battery-recycling PDF
reduced summary source excerpts from 18,000 to 5,671 characters (68.5%). This is
one document's source-text measurement, excludes system/schema overhead, uses no
AI call, and is not a measured token/cost saving or a general coverage guarantee.
