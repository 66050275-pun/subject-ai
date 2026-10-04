"""In-memory layout regressions; no uploaded papers or paid AI requests."""
import re
import unittest
from unittest.mock import AsyncMock, patch

import pymupdf as fitz
from fastapi import HTTPException

import ai_features
from extractor import (ExtractionError, _reference_section, _split_entries,
                       extract_bibliography_text, extract_references,
                       numbered_bibliography_entries, split_bibliography_batches)


def pdf(text):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(35, 35, 550, 800), text, fontsize=9)
        return doc.tobytes()


class PdfFormatTests(unittest.TestCase):
    def test_interleaved_two_columns_read_whole_entries(self):
        with fitz.open() as doc:
            page = doc.new_page(width=600, height=800)
            page.insert_text((35, 35), 'References', fontsize=10)
            # PDF content order alternates columns, not logical reading order.
            for row in range(3):
                for x, n in ((35, row + 1), (330, row + 4)):
                    page.insert_text((x, 60 + row * 55), f'[{n}] A. Author, Research topic {n},', fontsize=9)
                    page.insert_text((x, 74 + row * 55), f'Journal of Testing (2024), pp. {n}-20.', fontsize=9)
            data = doc.tobytes()
        entries = numbered_bibliography_entries(extract_bibliography_text(data))
        self.assertEqual(list(entries), list(range(1, 7)))
        for n, text in entries.items():
            self.assertIn(f'topic {n}', text)
            self.assertIn(f'pp. {n}-20', text)
            self.assertNotIn(f'topic {n % 6 + 1}', text)

    def test_rotated_columns_keep_reading_order(self):
        with fitz.open() as doc:
            page = doc.new_page(width=600, height=800)
            page.insert_text((35, 35), 'References', fontsize=10)
            for row in range(3):
                for x, n in ((35, row + 1), (330, row + 4)):
                    page.insert_text((x, 60 + row * 55), f'[{n}] Author, Battery study {n},', fontsize=9)
                    page.insert_text((x, 74 + row * 55), f'Journal (2024), pp. {n}-20.', fontsize=9)
            page.set_rotation(90)
            data = doc.tobytes()
        refs = numbered_bibliography_entries(extract_bibliography_text(data))
        self.assertEqual(len(refs), 6)
        for n, text in refs.items():
            self.assertIn(f'pp. {n}-20', text)

    def test_encrypted_pdf_has_clear_error(self):
        with fitz.open() as doc:
            doc.new_page().insert_text((35, 35), 'References and enough text for this document')
            data = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='reader')
        with self.assertRaisesRegex(ExtractionError, 'รหัสผ่าน'):
            extract_bibliography_text(data)

    def test_plain_detached_numbers_and_year_line_are_preserved(self):
        source = 'Bibliography\n1.\nAuthor, One publication.\n2024\n2.\nAuthor, Two publications, 2023.\n3.\nAuthor, Three publications, 2022.'
        entries = numbered_bibliography_entries(_reference_section([source]))
        self.assertEqual(len(entries), 3)
        self.assertIn('2024', entries[1])

    def test_repeated_heading_does_not_drop_first_page(self):
        pages = ['References\n[1] A. Author, Paper one, 2024.\n[2] B. Author, Paper two, 2023.',
                 'References\n[3] C. Author, Paper three, 2022.\n[4] D. Author, Paper four, 2021.']
        self.assertEqual(list(numbered_bibliography_entries(_reference_section(pages))), [1, 2, 3, 4])

    def test_bios_and_appendix_are_not_bibliography(self):
        text = 'References\n' + '\n'.join(f'[{i}] A. Author, Journal of Testing 20 (2024) 100.' for i in range(1, 4))
        for suffix in ['Jane Doe received the BS degree in 2020.', 'Appendix A: Further experiments\nNoise',
                       'A. NP-HARDNESS PROOF\nIn this appendix we give a proof.',
                       'A.\nNP-HARDNESS PROOF\nIn this appendix we give a proof.']:
            with self.subTest(suffix=suffix):
                result = _reference_section([text + '\n' + suffix])
                self.assertNotIn('degree', result)
                self.assertNotIn('PROOF', result)
                self.assertNotIn('Noise', result)
                self.assertEqual(len(numbered_bibliography_entries(result)), 3)

    def test_heading_free_requires_dense_dated_entries(self):
        text = '\n'.join(f'{i}. Author, A research publication, 2024.' for i in range(1, 5))
        self.assertEqual(len(extract_references(pdf(text))), 4)
        with self.assertRaises(ExtractionError):
            _reference_section(['1. First instruction about an experiment\n2. Second instruction about an experiment\n3. Third instruction about an experiment'])

    def test_standalone_labels_and_no_space(self):
        text = 'References\n[1]\nA. Author, Battery test study, 2024.\n[2]B. Author, Battery second study, 2023.\n[3]\nC. Author, Battery third study, 2022.'
        section = _reference_section([text])
        self.assertEqual(len(numbered_bibliography_entries(section)), 3)
        chunks = split_bibliography_batches(section, max_entries=1)
        self.assertEqual(len(chunks), 3)

    def test_wrapped_page_and_doi_suffix_do_not_swallow_next_label(self):
        source = 'References\n[1] Author, Journal (2024) 648-\n680.\n[2] Author, DOI 10.1234/example-\n1.\n[3] Author, A third publication, 2024.'
        entries = numbered_bibliography_entries(_reference_section([source]))
        self.assertEqual(list(entries), [1, 2, 3])
        self.assertTrue(entries[1].endswith('680.'))
        self.assertTrue(entries[2].endswith('1.'))
        self.assertNotIn('[2]', entries[1])
        self.assertNotIn('[3]', entries[2])

    def test_plain_numbering_with_page_range_followed_by_doi(self):
        source = 'References\n1. Author, First publication, 2024.\n2. Author, Second publication, 2024.\n3. Author, Third publication, Journal (2024) 469-\n480. https://doi.org/10.1234/example'
        entries = numbered_bibliography_entries(_reference_section([source]))
        self.assertEqual(list(entries), [1, 2, 3])
        self.assertIn('480.', entries[3])

    def test_non_english_heading(self):
        for heading in ['Références', 'Referencias', 'Literaturverzeichnis', 'บรรณานุกรม']:
            with self.subTest(heading=heading):
                section = _reference_section([heading + '\n[1] Author, One publication, 2024.\n[2] Author, Two publications, 2023.\n[3] Author, Three publications, 2022.'])
                self.assertEqual(len(numbered_bibliography_entries(section)), 3)

    def test_lowercase_author_prefix_and_quoted_title_year(self):
        section = 'Golub GH (1979). "Extensions and Uses of the Algorithm for\nSolving Nonlinear Least Squares Problems." In "Army Conference 1979", pp. 1-12.\nvan Stokkum IHM (1997). "Parameter Precision in Global Analysis." Journal 4, 76.'
        entries = _split_entries(section)
        self.assertEqual(len(entries), 2)
        self.assertIn('Solving Nonlinear', entries[0])

    def test_scanned_and_damaged_fail_with_useful_error(self):
        with self.assertRaisesRegex(ExtractionError, 'Scanned PDF'):
            extract_bibliography_text(pdf(''))
        with self.assertRaises(ExtractionError):
            extract_references(b'%PDF-1.4 damaged')


class AiExtractionCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def run_extraction(self, source, generate):
        async def resolve(refs):
            return [{'title': r.title, 'original_text': r.original_text,
                     'authors': ['Resolved Author']} for r in refs]
        with patch.object(ai_features, 'generate', generate), \
             patch.object(ai_features, 'resolve_references', resolve), \
             patch.object(ai_features, 'extract_citation_contexts', return_value={}), \
             patch.object(ai_features, 'extract_citation_counts', return_value=(False, {})):
            return await ai_features.extract_bibliography_result(b'fake', source, 'maxplus', 'test', 'fake', None)

    async def test_titleless_entries_and_incomplete_ai_keep_printed_sequence(self):
        source = '\n'.join(f'[{i}] A. Author, J. Testing 20 (2024) {i}.' for i in range(1, 53))
        calls = []
        async def generate(*args, **kwargs):
            excerpt = args[3].split('Bibliography excerpt:\n')[1]
            calls.append(excerpt)
            ids = re.findall(r'\[(\d+)\]', excerpt) if len(calls) == 1 else []
            return {'references': [{'number': int(n), 'title': None, 'authors': None,
                    'year': 2024, 'doi': None, 'original_text': 'A. Author, J. Testing 20 (2024) 100.'} for n in ids]}
        result = await self.run_extraction(source, generate)
        self.assertEqual(len(calls), 4)
        self.assertEqual(result['total_references'], 52)
        self.assertTrue(result['extraction_complete'])
        self.assertEqual(result['expected_reference_count'], 52)
        self.assertEqual(result['recovered_reference_numbers'], list(range(17, 53)))
        self.assertTrue(all(r['authors'] == ['Resolved Author'] for r in result['results']))
        self.assertTrue(all(r['original_text'].endswith(f"{r['reference_number']}.") for r in result['results']))
        self.assertFalse(result['results'][0]['extraction_fallback'])
        self.assertTrue(result['results'][-1]['extraction_fallback'])

    async def test_invalid_batch_recovered_without_extra_requests(self):
        source = '\n'.join(f'[{i}] A. Author, Journal of Testing 20 (2024) 100.' for i in range(1, 34))
        generate = AsyncMock(return_value={'references': 'invalid'})
        result = await self.run_extraction(source, generate)
        self.assertEqual(generate.await_count, 3)
        self.assertEqual(result['total_references'], 33)
        self.assertEqual(len(result['recovered_reference_numbers']), 33)
        self.assertIn('รูปแบบ', result['extraction_warnings'][0])

    async def test_truncated_provider_json_recovers_source(self):
        source = '\n'.join(f'[{i}] A. Author, Journal of Testing 20 (2024) 100.' for i in range(1, 4))
        generate = AsyncMock(side_effect=ai_features.AIResponseFormatError(502, 'invalid JSON'))
        result = await self.run_extraction(source, generate)
        self.assertEqual(result['total_references'], 3)
        self.assertEqual(generate.await_count, 1)
        self.assertEqual(len(result['recovered_reference_numbers']), 3)

    async def test_missing_source_number_does_not_claim_complete(self):
        source = '\n'.join(f'[{i}] A. Author, Journal of Testing 20 (2024) 100.' for i in (1, 2, 4))
        result = await self.run_extraction(source, AsyncMock(return_value={'references': []}))
        self.assertEqual(result['total_references'], 3)
        self.assertEqual(result['expected_reference_count'], 4)
        self.assertFalse(result['extraction_complete'])
        self.assertIn('3', result['extraction_warnings'][-1])

    async def test_unknown_numbers_not_added_to_source(self):
        source = '\n'.join(f'[{i}] A. Author, Journal of Testing 20 (2024) 100.' for i in range(1, 4))
        generate = AsyncMock(return_value={'references': [{'number': 99, 'title': 'Hallucinated paper title',
                                   'authors': [], 'year': '2024', 'doi': None, 'original_text': 'Not in source bibliography'}]})
        result = await self.run_extraction(source, generate)
        self.assertEqual([r['reference_number'] for r in result['results']], [1, 2, 3])
        self.assertNotIn('Hallucinated', str(result['results']))

    async def test_conflicting_duplicate_is_recovered_from_source(self):
        source = '\n'.join(f'[{i}] A. Author, Journal of Testing 20 (2024) 100.' for i in range(1, 4))
        one = {'number': 1, 'title': 'Original AI title', 'year': '2024', 'doi': None,
               'original_text': 'A. Author, Journal of Testing 20 (2024) 100.'}
        generate = AsyncMock(return_value={'references': [one, {**one, 'title': 'Conflicting AI title'}]})
        result = await self.run_extraction(source, generate)
        self.assertEqual(result['total_references'], 3)
        self.assertTrue(result['results'][0]['extraction_fallback'])
        self.assertNotIn('Conflicting AI title', str(result['results']))
        self.assertEqual(generate.await_count, 1)

    async def test_unnumbered_invalid_batch_remains_explicit_failure(self):
        with self.assertRaises(HTTPException):
            await self.run_extraction('Author, A. (2024). A long paper title. Journal of Testing.',
                                      AsyncMock(return_value={'references': 'invalid'}))

    async def test_unnumbered_completeness_is_not_claimed(self):
        result = await self.run_extraction('Author, A. (2024). Research publication topic. Journal.',
            AsyncMock(return_value={'references': [{'number': None, 'title': 'Research publication topic',
             'authors': [], 'year': '2024', 'doi': None, 'original_text': 'Author, A. (2024). Research publication topic. Journal.'}]}))
        self.assertFalse(result['extraction_complete'])
        self.assertIsNone(result['expected_reference_count'])
        self.assertIn('ตรวจความครบ', result['extraction_warnings'][0])
