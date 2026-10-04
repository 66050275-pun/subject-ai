"""Printed titles stay separate from author-only/journal-only citations."""
import unittest

from bibliography_parser import citation_fields


class OmittedCitationTitleTests(unittest.TestCase):
    def test_elsevier_omitted_titles_keep_names_and_publication_year(self):
        # Several scientific publishers intentionally omit article titles from
        # the bibliography. Journal names must not be supplied as guessed titles.
        cases = [
            ('Q. Qi, W. Liu, Z. Deng, J. Li, Z. Song, X. Hu, J. Energy Chem. 92 (2024) 605–618.',
             '2024', ('Q. Qi', 'W. Liu', 'Z. Deng', 'J. Li', 'Z. Song', 'X. Hu')),
            ('C. Xu, P. Behrens, M.M. Hu, Nat. Commun. 14 (2023) 119.',
             '2023', ('C. Xu', 'P. Behrens', 'M.M. Hu')),
            ('Y. Hua, X. Liu, S. Zhou, Resour. Conserv. Recycl. 168 (2021) 105249.',
             '2021', ('Y. Hua', 'X. Liu', 'S. Zhou')),
            ('R. Xiong, Y. Pan, W. Shen, H. Li, F. Sun, Renew. Sustain. Energy Rev. 131 (2020) 110048.',
             '2020', ('R. Xiong', 'Y. Pan', 'W. Shen', 'H. Li', 'F. Sun')),
            ('Y. Liao, H. Zhang, Y. Peng, Adv. Energy Mater. 14 (2024) 2304295.',
             '2024', ('Y. Liao', 'H. Zhang', 'Y. Peng')),
            ('S. Khaleghi, M.S. Hosen, J. Van Mierlo, Appl. Energy 308 (2022) 118348.',
             '2022', ('S. Khaleghi', 'M.S. Hosen', 'J. Van Mierlo')),
            ('J. Wen, X. Chen, X. Li, Y. Li, Energy 261 (2022) 125234.',
             '2022', ('J. Wen', 'X. Chen', 'X. Li', 'Y. Li')),
            ('X. Bian, Z. Wei, D.U. Sauer, IEEE Trans. Power Electron. 37 (2022) 2226–2236.',
             '2022', ('X. Bian', 'Z. Wei', 'D.U. Sauer')),
            ('P. Liu, Y. Wu, C. She, Z. Wang, Z. Zhang, IEEE Trans. Power Electron. 37 (2022) 12563–12576.',
             '2022', ('P. Liu', 'Y. Wu', 'C. She', 'Z. Wang', 'Z. Zhang')),
            ('C. Lin, J. Xu, J. Hou, D. Jiang, Energy Storage Mater. 63 (2023) 102967.',
             '2023', ('C. Lin', 'J. Xu', 'J. Hou', 'D. Jiang')),
            ('J. Tian, R. Xiong, W. Shen, IEEE Trans. Power Electron. 35 (2020) 10363–10373.',
             '2020', ('J. Tian', 'R. Xiong', 'W. Shen')),
            ('Y. Yan, B. Wang, C. Wang, Energy Convers. Manage. 314 (2024) 118685.',
             '2024', ('Y. Yan', 'B. Wang', 'C. Wang')),
            ('J. Rhyu, D. Zhuang, M.Z. Bazant, R.D. Braatz, J. Electrochem. Soc. 171 (2024) 070544.',
             '2024', ('J. Rhyu', 'D. Zhuang', 'M.Z. Bazant', 'R.D. Braatz')),
            ('R. Hausbrand, G. Cherkashinin, H. Ehrenberg, Mater. Sci. Eng. B 192 (2015) 3–25.',
             '2015', ('R. Hausbrand', 'G. Cherkashinin', 'H. Ehrenberg')),
        ]
        for citation, year, authors in cases:
            with self.subTest(citation=citation):
                self.assertEqual(citation_fields(citation), ('', year, authors))

    def test_journal_before_parenthesized_year_is_not_an_author(self):
        citation = ('X. Huang, S. Tao, C. Liang, Nat. Commun. (2026). '
                    'https://doi.org/10.1038/s41467-026-69369-1.')
        self.assertEqual(citation_fields(citation), ('', '2026', ('X. Huang', 'S. Tao', 'C. Liang')))

    def test_unrecognized_volume_year_pages_are_not_a_title(self):
        title, year, _ = citation_fields('A. Author, B. Doe, Unusual Research Journal 42 (2024) 110001.')
        self.assertEqual((title, year), ('', '2024'))

    def test_actual_title_with_energy_words_is_kept(self):
        citation = ('A. Author, B. Doe, Energy storage and rapid degradation detection, '
                    'Energy Storage Mater. 63 (2023) 102967.')
        self.assertEqual(citation_fields(citation),
                         ('Energy storage and rapid degradation detection', '2023', ('A. Author', 'B. Doe')))

    def test_compressed_initials_with_actual_title_are_kept(self):
        citation = ('M.Z. Bazant, R.D. Braatz, Physics-informed battery recycling with sparse pulse data, '
                    'J. Electrochem. Soc. 171 (2024) 070544.')
        self.assertEqual(citation_fields(citation),
                         ('Physics-informed battery recycling with sparse pulse data', '2024',
                          ('M.Z. Bazant', 'R.D. Braatz')))

    def test_author_only_citation_does_not_produce_a_title(self):
        self.assertEqual(citation_fields('M.Z. Bazant, R.D. Braatz (2024).'),
                         ('', '2024', ('M.Z. Bazant', 'R.D. Braatz')))


if __name__ == '__main__':
    unittest.main()
