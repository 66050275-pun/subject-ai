"""Independent citation fixtures and code-only API contracts (no live services)."""
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import pymupdf as fitz

from bibliography_parser import citation_fields, extract_doi
from extractor import _split_entries, extract_references_with_diagnostics, Reference
from main import app
from resolver import _new_result, _add_source


def pdf(text):
    with fitz.open() as doc:
        doc.new_page().insert_textbox(fitz.Rect(30, 30, 550, 810), text, fontsize=9)
        return doc.tobytes()


class CitationFieldsTests(unittest.TestCase):
    def test_apa_short_title(self):
        title, year, authors = citation_fields('Smith, J., & Doe, A. (2024). Sparse learning. Journal of Batteries, 4, 1-10.')
        self.assertEqual((title, year), ('Sparse learning', '2024'))
        self.assertEqual(authors, ('Smith, J.', 'Doe, A'))
        self.assertEqual(_new_result(Reference('', title, year, authors=authors))['title'], title)

    def test_elsevier_title_before_year(self):
        title, year, authors = citation_fields('A. Smith, B. Doe, Parameter estimation using weighted vectors, J. Power Sources 44 (2023) 100-200.')
        self.assertEqual((title, year), ('Parameter estimation using weighted vectors', '2023'))
        self.assertEqual(authors, ('A. Smith', 'B. Doe'))

    def test_vancouver(self):
        self.assertEqual(citation_fields('Smith J, Doe A. Rapid battery detection. Energy. 2024; 20: 123.'),
                         ('Rapid battery detection', '2024', ('Smith J', 'Doe A')))

    def test_journal_only_never_becomes_title(self):
        title, year, authors = citation_fields('R. Guo, F. Wang, W. Shen, J. Energy Chem. 92 (2024) 648-680.')
        self.assertEqual(title, '')
        self.assertEqual(year, '2024')
        self.assertEqual(authors, ('R. Guo', 'F. Wang', 'W. Shen'))

    def test_fail_is_journal_abbreviation(self):
        title, year, _ = citation_fields('J. Feng, P. Kvam, and Y. Tang, “Remaining useful lifetime prediction based on damage-marker model.” Eng. Fail. Anal., 70, 323 (2016).')
        self.assertEqual(title, 'Remaining useful lifetime prediction based on damage-marker model')
        self.assertEqual(year, '2016')

    def test_quotes_inside_title_are_not_a_new_title(self):
        title, _, _ = citation_fields("A. Author, Grain dimension in spring wheat ‘Banks’, J. Testing 4 (1990) 100-200.")
        self.assertEqual(title, "Grain dimension in spring wheat ‘Banks’")

    def test_author_title_year_and_trailing_year_removed(self):
        title, year, authors = citation_fields('Agarwal, A., Negahban, S. N., and Wainwright, M. J. Noisy matrix decomposition via convex relaxation. 2011. URL http://arxiv.org/abs/1012.4807.')
        self.assertEqual(title, 'Noisy matrix decomposition via convex relaxation')
        self.assertEqual(year, '2011')
        self.assertEqual(len(authors), 3)

    def test_year_in_doi_is_not_publication_year(self):
        self.assertEqual(citation_fields('A. Author, Battery analysis with sparse signals, J. Testing (2019), DOI: 10.1234/2024.1234')[1], '2019')

    def test_doi_with_wrapped_separator(self):
        self.assertEqual(extract_doi('A. Author, 2024. DOI 10.1016/j.ipl.2010.07. 026.'), '10.1016/j.ipl.2010.07.026')
        self.assertEqual(extract_doi('DOI 10.1007/s10825-022-01881- 1.'), '10.1007/s10825-022-01881-1')

    def test_unicode_and_printed_title_wrap(self):
        title, _, authors = citation_fields('L´eon Bottou. 2004. Efficient nearest neigh- bor search. Journal of Testing.')
        self.assertEqual(title, 'Efficient nearest neighbor search')
        self.assertEqual(authors, ('Léon Bottou',))

    def test_umlaut_and_circumflex_pdf_glyphs_do_not_drop_authors(self):
        title, year, authors = citation_fields('J¨org Tiedemann and Alexandre Bouchard-Cˆot´e. 2012. Parallel language tools. Journal.')
        self.assertEqual((title, year), ('Parallel language tools', '2012'))
        self.assertEqual(authors, ('Jörg Tiedemann', 'Alexandre Bouchard-Côté'))

    def test_wrapped_author_names_do_not_merge_or_split_references(self):
        source = ('Sabine Buchholz and Erwin Marsi. 2006. Parsing multilingual data. Journal.\n'
                  'Aljoscha Burchardt, Katrin Erk, Anette Frank, Andrea\n'
                  'Kowalski, Sebastian Pado, and Manfred Pinkal. 2006.\n'
                  'The SALSA corpus: a German corpus resource. Journal.\n'
                  'Michael Collins. 2002. Training hidden models. Journal.')
        rows = _split_entries(source)
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[1].startswith('Aljoscha Burchardt'))
        self.assertEqual(len(citation_fields(rows[1])[2]), 6)

    def test_location_is_continuation_not_author(self):
        source = ('A. Smith. 2024. A first research method. Proceedings,\nUppsala, Sweden.\n'
                  'Aria Haghighi and Dan Klein. 2008. A second research method. Journal.')
        rows = _split_entries(source)
        self.assertEqual(len(rows), 2)
        self.assertIn('Uppsala, Sweden', rows[0])
        self.assertTrue(rows[1].startswith('Aria'))

    def test_author_title_year_wrapping(self):
        rows = _split_entries('Agarwal, A., Negahban, S. N., and Wainwright, M. J.\nNoisy matrix decomposition via convex relaxation.\n2011.\nCandes, E. J., Li, X., and Wright, J.\nRobust principal component analysis?\n2009.')
        self.assertEqual(len(rows), 2)
        self.assertEqual([citation_fields(r)[1] for r in rows], ['2011', '2009'])

    def test_word_per_line_authors_keep_full_prefix(self):
        rows = _split_entries('A. Author. 2010. First paper title. Journal.\nMarco\nBaroni,\nGeorgiana\nDinu,\nand\nGerm´an\nKruszewski.\n2014.\nA new distributional model. Journal.')
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1].startswith('Marco Baroni'))
        self.assertEqual(citation_fields(rows[1])[2], ('Marco Baroni', 'Georgiana Dinu', 'Germán Kruszewski'))

    def test_embedded_editors_are_not_another_reference(self):
        source = 'A. Author. 2006. Embedded learning methods. In I.\nGuyon, S. R. Gunn, and L. Zadeh, editors, Feature Extraction, Springer.\nB. Doe. 2007. A second learning method. Journal.'
        rows = _split_entries(source)
        self.assertEqual(len(rows), 2)
        self.assertIn('Guyon', rows[0])
        self.assertTrue(rows[1].startswith('B. Doe'))

    def test_book_conference_editors_remain_continuation(self):
        source = 'Bates DM (2003). "Converting a Large R Package to S4 Classes."\nIn K Hornik, F Leisch, A Zeileis (eds.), "Proceedings of a Workshop", 2003.\nChambers JM (1998). Programming with Data. Springer.'
        self.assertEqual(len(_split_entries(source)), 2)

    def test_page_range_is_not_an_unnumbered_reference(self):
        rows = _split_entries('A. Author. 2024. A first research method. Journal, pages 100-\n526. Association for Computational Linguistics.\nB. Doe. 2023. A second research method. Journal.')
        self.assertEqual(len(rows), 2)
        self.assertIn('526.', rows[0])

    def test_metadata_authors_replace_printed_initials(self):
        result = _new_result(Reference('citation', 'Sparse learning', '2024', authors=('J. Smith',)))
        _add_source(result, 'Crossref', {'authors': ['Jane Smith'], 'title': 'Sparse learning'})
        self.assertEqual(result['authors'], ['Jane Smith'])


class CodeCoverageTests(unittest.TestCase):
    def test_numbered_gap_is_not_claimed_complete(self):
        data = pdf('References\n[1] A. Author, Paper one, 2024.\n[2] A. Author, Paper two, 2023.\n[4] A. Author, Paper four, 2022.')
        refs, diagnostic = extract_references_with_diagnostics(data)
        self.assertEqual(len(refs), 3)
        self.assertEqual(diagnostic['expected_reference_count'], 4)
        self.assertFalse(diagnostic['extraction_complete'])
        self.assertIn('3', diagnostic['extraction_warnings'][0])

    def test_small_numbered_bibliography(self):
        for labels in ['[1]', '1.']:
            refs, diag = extract_references_with_diagnostics(pdf('References\n' + labels + ' A. Author, Sparse learning, 2024.'))
            self.assertEqual(len(refs), 1)
            self.assertTrue(diag['extraction_complete'])

    def test_software_version_is_not_a_reference_label(self):
        refs, diag = extract_references_with_diagnostics(pdf('References\nMeyer D (2006). Visualizing categorical data. Package version\n1.0-2.\nMoshier SL (1992). Cephes mathematical library. Journal.'))
        self.assertEqual(len(refs), 2)
        self.assertIsNone(diag['expected_reference_count'])

    def test_corresponding_author_footer_excluded(self):
        refs, _ = extract_references_with_diagnostics(pdf('References\n[1] A. Author, Battery analysis, 2024.\n*corresponding author : private@example.org'))
        self.assertNotIn('private@example', refs[0].original_text)

    def test_unnumbered_is_explicitly_uncertain(self):
        _, diag = extract_references_with_diagnostics(pdf('References\nA. Smith. 2024. Sparse learning. Journal.\nB. Doe. 2023. Battery analysis. Journal.'))
        self.assertFalse(diag['extraction_complete'])
        self.assertIsNone(diag['expected_reference_count'])
        self.assertIn('ตรวจความครบ', diag['extraction_warnings'][0])


class CodeApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_code_route_no_ai_key_or_ai_request(self):
        data = pdf('References\n[1] A. Smith. 2024. Sparse learning. Journal.\n[2] B. Doe. 2023. Battery analysis. Journal.')
        async def resolve(refs):
            return [{**_new_result(r), 'paper_url': None} for r in refs]
        with patch('main.resolve_references', resolve), patch('ai_features.generate', AsyncMock(side_effect=AssertionError('Code must not use AI'))) as ai:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                result = await client.post('/api/references', files={'file': ('paper.pdf', data, 'application/pdf')})
        self.assertEqual(result.status_code, 200, result.text)
        payload = result.json()
        self.assertEqual(payload['total_references'], 2)
        self.assertTrue(payload['extraction_complete'])
        self.assertEqual(payload['parsed_title_count'], 2)
        self.assertTrue(payload['results'][0]['authors'])
        self.assertEqual(payload['results'][0]['title'], 'Sparse learning')
        ai.assert_not_called()
