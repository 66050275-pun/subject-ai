# Local PDF preview assets

These browser-only bundles use Mozilla **pdfjs-dist 6.4.299**, licensed under
Apache-2.0. The full license is in `PDFJS-LICENSE.txt`; bundled notices are retained.
There are no runtime CDN requests. No PDF is sent to the server for preview.

Source: https://www.npmjs.com/package/pdfjs-dist/v/6.4.299

Rebuild with Node and esbuild (used here: esbuild 0.28.2):

```sh
npm install --prefix /tmp/paperref-pdf-build --ignore-scripts --no-audit --no-fund pdfjs-dist@6.4.299 esbuild@0.28.2
/tmp/paperref-pdf-build/node_modules/.bin/esbuild /tmp/paperref-pdf-build/node_modules/pdfjs-dist/legacy/build/pdf.mjs --bundle --format=iife --global-name=pdfjsLib --minify --legal-comments=eof --outfile=static/vendor/pdf.min.js
/tmp/paperref-pdf-build/node_modules/.bin/esbuild /tmp/paperref-pdf-build/node_modules/pdfjs-dist/legacy/build/pdf.worker.mjs --bundle --format=iife --global-name=pdfjsWorker --minify --legal-comments=eof --outfile=static/vendor/pdf.worker.min.js
cp /tmp/paperref-pdf-build/node_modules/pdfjs-dist/LICENSE static/vendor/PDFJS-LICENSE.txt
```

The preview renders one page at a time in a canvas (maximum 2.4 million pixels).
Its classic worker uses an explicit port and a local blob URL; CSP allows blob
workers but does not allow blob page scripts, eval or embedded objects. Streamlit
packages the same trusted worker in an inert base64 template. PDF bytes, canvas
images, local file URLs and worker state are never added to history.

The viewer is read-only: PDF scripting, attachments, links and forms are not
executed. Password-protected/corrupt files offer an option to open the original
file. Supplementary CMaps, standard-font files and JPX/JBIG2 decoders are not
bundled; unusual fonts or image encodings may need that option too. Extraction
continues to use the existing backend independently of preview availability.
