# Code extraction improvements and before/after evaluation

2026-10-04. Baseline: GitHub commit `a0c312edc17a0a26db2664fd3bafdaf7c061319f`.

## Changes

`bibliography_parser.py` is a deterministic, offline parser. It adds:

- Multi-line author/date boundaries with protection for wrapped author lists, including PDF output that separates each name into individual words.
- Author-title-year boundaries for older bibliography styles that put the date at the end. Author initials and surname-first lists are kept together; editors inside an existing book/chapter citation are continuations.
- APA/Harvard-style title-after-year, Vancouver/Elsevier title-before-year and quoted titles. Short printed titles remain visible. Journal-only references keep an unknown title and use the complete original citation for academic searches.
- Printed author names before metadata resolution. A confirmed metadata record can replace printed initials with full author names. Title-search matching thresholds are retained; short/uncertain titles still use full-citation search.
- Publication-year extraction before DOI/URL text, explicit DOI separator-wrap repair, ligature/accent normalization and title word-wrap repair. Original citation text is retained for inspection.
- Rejection of page ranges, software versions, location continuations, embedded editors and corresponding-author footers as extra references.

The existing PDF column/reading-order comparison is shared by code and AI extraction. Code now returns `expected_reference_count`, `extraction_complete`, `extraction_warnings`, `parsed_title_count` and `parsed_author_count` through **the same `/api/references` endpoint**. Streamlit forwards that endpoint and uses the same UI. Single/two-reference numbered lists are supported when the labels/text are credible.

Numbered coverage refers to recognized printed labels. It does not certify title accuracy or prove that a damaged PDF has no omitted trailing references. Unnumbered bibliographies remain explicitly uncertain rather than receiving a completeness claim. This is the same distinction used in AI extraction.

The code path does not require or call an AI provider. Academic metadata/OA API searches remain necessary for DOI/name resolution and download links and retain their existing limits. No new dependencies, OCR service or server-side user-data storage are introduced.

## Evaluation

Reused the same **45 SHA256-distinct PDFs**: 42 public GROBID/Science Parse fixtures and 3 user-supplied papers. 39 produced bibliography candidates; the same 6 negative/textless fixtures remain unsupported or without a bibliography. Candidate availability is not an accuracy percentage.

Nine independent count checks passed. The three uploaded papers remain at **52, 61 and 64** references. The prior seven checks are preserved; two author-style cases were added:

| Public fixture | Code before | Code after | Verification |
| --- | ---: | ---: | --- |
| `P14-1059.pdf`: How to make words with vectors | 17 | 32 | Both bibliography pages visually counted: 13 + 19 printed entries |
| `agarwal11.pdf`: Noisy Matrix Decomposition | 3 | 12 | Bibliography text reviewed as 12 distinct author/title entries |
| `dyer12.pdf`: Discriminative models for machine translation | 33 | 46 | Detected entries; boundary examples reviewed, no independent full gold count |
| TIMP / Journal of Statistical Software | 17 | 17 | Prior independently checked count preserved |
| PLOS ONE numbered bibliography | 59 | 59 | Prior numbered count preserved |

More detected entries alone do not prove better extraction: wrapped authors, editors, software versions and page ranges were regression-tested explicitly to prevent spurious rows. [CODE_EXTRACTION_COMPARISON.json](CODE_EXTRACTION_COMPARISON.json) records before/after public-file counts and missing-title counts. Those title counts measure populated fields, not semantic correctness.

**95 Python tests passed**, including 25 added regressions for citation fields, wrapping, metadata fallback, count diagnostics and a code API call without any AI key. The API test rejects any attempt to call the AI generator. The Streamlit browser history suite also passed with the new diagnostics present in the response.

No live LLM comparison was made. This improves practical overlap with AI extraction; it does not claim equal accuracy for arbitrary PDF layouts or semantic interpretation. Scanned PDFs still require OCR, and severely damaged text layers or unsupported styles may still need the optional AI tool or manual review.

No PDFs, private document contents or credentials are committed. Public fixture provenance/hashes and the current offline results are in [PDF_CORPUS_REPORT.json](PDF_CORPUS_REPORT.json). To repeat the offline checks, use `tools/evaluate_pdfs.py` as described in [PDF_EXTRACTION_TESTS.md](PDF_EXTRACTION_TESTS.md).

Follow-up: in the lithium-battery recycling PDF, the four populated title fields were journal fragments, since the bibliography omits titles. See [TITLE_METADATA_RECOVERY.md](TITLE_METADATA_RECOVERY.md) for the correction and automatic metadata lookup. Title field coverage alone is not accuracy.
