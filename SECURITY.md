# Browser privacy and security

Settings use a native modal dialog with a left drawer, keyboard focus containment,
Escape/backdrop close, focus return, and reduced-motion support. The same assets
run under FastAPI and Streamlit.

## Data and consent

- AI keys stay in the page's memory and pass through the server to the chosen AI
  provider when requested. They are never written to app files, cookies or browser
  storage. Keys are hidden again when the drawer closes or the tab loses visibility.
- Optional local storage remembers only `provider` and `model`. It is off by default;
  unchecking the option removes `paperref.ai-preferences.v1`. No keys, papers or chat
  are included. This preference belongs to the browser origin, including the
  Streamlit component's origin.
- Search history stores whitelisted reference metadata and links in IndexedDB on
  this browser origin. Recording is initially enabled; users can disable new
  recording, delete individual entries or clear everything. It excludes API keys,
  PDFs, citation context excerpts and Q&A/chat history. It now includes saved AI summaries/synthesis, graph coordinates/themes and source metadata. History is not encrypted by the app; use Incognito for sensitive work. Opening a PDF history result
  restores its cards/graph; full-text AI requires uploading the original again.
  Limit: newest 50 results or 8 MB, with a maximum 1 MB per result. Older entries
  are removed when full; storage denial/quota failures do not stop searches.
  Anyone using the same browser profile can access this history. Private browsing,
  clearing site data or changing origins can remove/separate it. JSON export/import
  is local and validates/rebuilds allowed fields before writing atomically. Invalid
  imports preserve existing history. Importing a backup does not change recording
  preferences; use backups to move between devices or local/Streamlit origins.
- The app adds no tracking/advertising cookies. Streamlit/hosting may use necessary
  cookies and retain operational logs. Google Fonts receives font requests.
  Academic services receive search metadata; AI providers receive the requested
  context and credentials. Their retention policies are separate from this app.
- Clearing a key removes it from the page; a request already sent to a provider may
  continue. Refreshing/closing a tab discards in-memory files/chat and unsaved results; saved metadata can be reopened from history. Export history as a backup.

## Implemented controls

Tailwind is compiled locally instead of executing its external CDN script.
Content Security Policy allows app scripts and hashes of trusted inline scripts,
blocks arbitrary inline scripts, objects and foreign API connections from the
page, and permits Google Fonts. Streamlit's embedded version computes hashes for
its bundled scripts and adds a CSP meta policy. Host-level Streamlit headers and
iframe sandboxing are controlled by Streamlit, not FastAPI middleware.

The PDF preview uses locally bundled PDF.js and a browser-only blob worker.
`worker-src blob:` permits this worker without allowing blob page scripts, eval,
embedded objects or foreign connections. Previewing sends no PDF to the server;
document bytes and canvas images are not part of IndexedDB history. Only one page
is rendered at a time with a canvas size cap. Replacing a document disposes its
worker, clears the canvas and revokes its local file URL. The read-only preview
does not execute PDF scripts, attachments or forms. See
[static/vendor/PDFJS-NOTICE.md](static/vendor/PDFJS-NOTICE.md) for asset versions,
licenses, rebuild commands and font/image-decoder limits.

FastAPI adds `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, a
restricted Permissions Policy, API `Cache-Control: no-store`, and a homepage CSP
with `frame-ancestors 'self'`. Middleware forwards streaming frames without
buffering. External article links allow HTTP/HTTPS without embedded credentials
and use `noopener noreferrer`. Model/provider/key validation, MaxPlus host
allowlisting, PDF limits, session-scoped transport and XSRF protection on Streamlit
remain enabled.

## Deployment scope

Use HTTPS from the hosting provider. Keep keys in secrets/password inputs and
out of GitHub. These controls are a baseline, not a certification of compliance
with any legal or security standard. For a public production service, the owner
must assess access controls, quotas/abuse controls, hosting logs and retention,
provider terms and applicable privacy requirements. Do not upload confidential
or personal material without the right to share it.

## Updating styles

After changing Tailwind utility classes, rebuild `static/utilities.css` using the
pinned CLI (Node is only needed for this build, not on Streamlit):

```bash
printf '@tailwind base;\n@tailwind components;\n@tailwind utilities;\n' > /tmp/paperref-tailwind.css
npm exec --yes --package=tailwindcss@3.4.17 -- tailwindcss \
  -i /tmp/paperref-tailwind.css -o static/utilities.css \
  --content './static/index.html,./static/graph.js' --minify
```

Browser verification uses mocked AI/academic APIs and covers settings on desktop,
phone and tablet, storage opt-in/revocation, reload without key persistence, focus
containment and blocked untrusted scripts on both frontend hosts.

Multipart uploads up to the allowed limit remain in RAM: a 64 MB request-body cap sits below the configured Starlette spool threshold, including compatibility with older parser attribute names. The existing 50 MB PDF limit remains. No user-history/PDF disk storage is used. See HISTORY_WORKSPACE.md for comparative AI evidence limits.
