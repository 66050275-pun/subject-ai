# PDF extraction: supported layouts and evaluation

Updated 2026-10-04. This change is shared by FastAPI and the Streamlit component. The subsequent code-extraction improvements and current 95-test validation are detailed in [CODE_EXTRACTION_REPORT.md](CODE_EXTRACTION_REPORT.md). The 70-test result below describes the earlier AI-recovery release.

## What changed

- Compare native PDF content order with geometric two-column reading order, including rotated pages. Choose the candidate with the strongest numbered/author-year evidence instead of accepting the first nonempty result.
- Keep the first bibliography page when References headings repeat. Detect dense numbered bibliographies without headings; also recognize French, Spanish, German and Thai bibliography headings.
- Join detached `[n]` and `n.` labels; accept `[n]Author` without a separating space. Remove running page numbers and stop at author biographies or identifiable appendix sections.
- AI extraction retains entries whose title is absent (`title: null`), rather than discarding authors/journal/year/page-only citations. Numeric years and null author lists are normalized.
- After bounded AI batches are merged, check recognized printed reference numbers. Restore omitted entries from the **original PDF citation text**, with an `extraction_fallback` flag. Malformed/truncated generated JSON and conflicting duplicate AI entries can also be recovered when the source has unambiguous numbered entries. No automatic paid retry is made.
- Unknown AI numbers are excluded. Missing source numbers produce warnings; the app does not claim a complete sequence if source labels have gaps. An unnumbered bibliography cannot receive an automatic completeness guarantee.
- Keep resolver-provided authors when the AI author list is empty. Show counts for AI entries versus raw-text recovery; preserve recovery labels and warnings in sanitized IndexedDB snapshots.

Provider authentication, payment, rate-limit, network and timeout errors still surface explicitly. They are not silently converted into an apparently successful AI operation.

`extraction_complete` means the recognized numbered sequence is covered, **not** that every title, boundary, DOI or metadata match has been independently verified. Raw-text recovery is deliberately distinct from AI extraction in the UI.

## Evaluation

45 distinct PDFs after SHA256 deduplication: 3 user-supplied papers and 42 public fixtures downloaded from [GROBID](https://github.com/kermitt2/grobid) and [Science Parse](https://github.com/allenai/science-parse). Archives were obtained from `codeload.github.com`; a Crossref search was also performed. Direct university-host download attempts were blocked by the workspace network policy, so this is **not a direct university-repository download benchmark**.

39 PDFs produced bibliography candidates. The other 6 are a textless coordinate calibration fixture, a footnote-only excerpt, a short medical abstract without a bibliography, a synthetic story, and two dehyphenation snippets. Successfully parsing a candidate is an availability check, not a reference-accuracy score. The corpus contains research articles, long journal documents, abbreviated titleless references, author-year styles, numbered styles, multi-column layouts and negative fixtures.

Counts spot-checked against the printed labels or bibliography text:

| Document | Expected | Extracted |
| --- | ---: | ---: |
| Physics-informed learning / sparse pulse response | 52 | 52 |
| ECM model / weighted mean of vectors | 61 | 61 |
| Lithium-ion life model / electrode cracking | 64 | 64 |
| TIMP / Journal of Statistical Software (GROBID fixture) | 17 | 17 |
| Approximation Algorithms for Data Placement (Science Parse fixture) | 16 | 16 |
| Disk-based Storage for Scalable Video (Science Parse fixture) | 17 | 17 |
| PLOS ONE numbered bibliography (Science Parse fixture) | 59 | 59 |

For the actual uploaded 52-reference PDF, a mocked provider returned only the first 16 entries and empty later batches: **52 entries remained, 36 explicitly marked as raw-text recovery, with 4 calls and no retries**. The real PDF text, bibliography boundaries, citation contexts and citation links were used. AI generation and academic resolution were mocked; no live MaxPlus or other paid-provider success is claimed.

Validation: 70 Python regression tests, workspace JavaScript checks and the Streamlit browser history suite passed. Regressions cover interleaved columns, repeated headings, heading-free numbered references, detached labels, non-English headings, titleless citations, partial/malformed/truncated AI output, conflicting duplicates, unknown numbers, gaps and unnumbered uncertainty.

No uploaded PDF, public PDF binary, API key, extracted body text or user history is committed. Production uploads continue to be processed in memory. Local development evaluation files do not change production storage behavior.

## Repeat the offline evaluation

```bash
python tools/evaluate_pdfs.py /path/to/pdfs --output report.json
python tools/evaluate_pdfs.py /path/to/pdfs --expected expected.json --output report.json
```

`expected.json` maps independently checked PDF SHA256 hashes to reference counts. The tool deduplicates identical PDFs, reports numbered gaps and batch counts, makes no network/AI calls and exits nonzero when an expected count differs. A report without independent expected counts must not be presented as an accuracy benchmark.

Public fixture provenance and hashes are recorded in [PDF_CORPUS_REPORT.json](PDF_CORPUS_REPORT.json). PDFs are not redistributed in this repository.

## Remaining limits

- Scans without a text layer need OCR first; this release does not add OCR.
- Three-column text is handled best when the PDF's native order is coherent; arbitrary geometric layouts and fragmented author-year entries remain best effort.
- Chapter-specific bibliographies, handwritten references and heavily corrupted text layers may need a bibliography-only PDF and manual verification.
- Missing paper titles remain unknown until an academic metadata provider finds a reliable match. Do not treat an inferred title or PDF availability as verified evidence.

Latest omitted-title metadata recovery and live quota limits: [TITLE_METADATA_RECOVERY.md](TITLE_METADATA_RECOVERY.md).
