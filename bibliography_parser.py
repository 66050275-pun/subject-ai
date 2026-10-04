"""Conservative, offline citation fields and wrapped author-date boundaries.

Unknown titles/authors stay unknown. This module never calls an LLM or a service.
"""
import re
import unicodedata

YEAR = re.compile(r'(?<!\d)((?:18|19|20)\d{2})([a-z])?(?!\d)')
DOI = re.compile(r'\b10\.\d{4,9}/[^\s<>\]\[{}\"\']+', re.I)
QUOTED = re.compile(r'[“\"‘]([^”\"’]{3,}?)[”\"’]')
PARTICLES = {'and', 'et', 'al', 'van', 'von', 'de', 'del', 'da', 'di', 'la', 'le', 'der', 'den', 'jr', 'Jr'}
VENUE = re.compile(
    r'^(?:Elsevier\b|Springer\b|Technical\s+report\b|'
    r'J\.\s+(?:Power|Energy|Chem|Phys|Electro(?:chem)?|Mater|Comput|Test|Stat|Sci|Appl|Clin|Med)\b|'
    r'Nat\.\s|Eng\.\s|Phys\.\s|Proc\.\s|'
    r'IEEE\s+Trans\b|ACS\s+Energy\s+Lett\b|'
    r'Renew\.\s+Sustain\b|Resour\.\s+Conserv\b|Adv\.\s+(?:Energy|Mater)\b|'
    r'Appl\.\s+(?:Energy|Phys|Sci)\b|Mater\.\s+Sci\b|'
    r'Energy\s+(?:Storage\s+Mater\b|Convers\.\s+Manage\b|Rev\.)|Energy\s+\d|'
    r'Journal\s+of\s|Proceedings\s+of\s|vol\.?\s*\d|pp?\.?\s*\d)', re.I
)
# Journal volume/date/page or article-number tails are bibliographic locators,
# including unfamiliar journals whose abbreviations are absent from VENUE.
PUBLICATION_LOCATOR = re.compile(r'\b\d+[a-z]?\s*\((?:18|19|20)\d{2}\)\s*\d+(?:\s*[-–—]\s*\d+)?\s*[.;]?$', re.I)


def normalize_text(text):
    for mark, combining in [('´', '\u0301'), ('¨', '\u0308'), ('ˆ', '\u0302'), ('`', '\u0300')]:
        text = re.sub(re.escape(mark) + r'([aeiouAEIOU])', lambda m: unicodedata.normalize('NFC', m[1] + combining), text)
    text = unicodedata.normalize('NFKC', text).replace('\u00ad', '')
    return re.sub(r'(?<=[a-zÀ-ÖØ-öø-ÿ])-\s+(?=[a-zÀ-ÖØ-öø-ÿ])', '', text)


def is_author_prefix(prefix):
    prefix = normalize_text(prefix).strip(' (,.;')
    if not prefix or len(prefix) > 450 or re.search(r'[\d:“”\"/<>]', prefix):
        return False
    words = re.findall(r'[^\W\d_]+', prefix, re.UNICODE)
    if len(words) < 2:
        return False
    if words[0] == 'In':
        return False  # Editors/venues inside a book chapter are continuations.
    if not words[0][0].isupper() and words[0] not in PARTICLES - {'and', 'et', 'al', 'jr'}:
        return False
    # Restrict prose continuations, venues and titles with lowercase words.
    if not all(w[0].isupper() or w in PARTICLES for w in words):
        return False
    parts = re.split(r'[,;]|\band\b|&', prefix)
    if any(VENUE.match(part.strip()) for part in parts):
        return False
    last = re.findall(r'[^\W\d_]+', parts[-1])
    if len(last) >= 2:
        return True
    previous = re.findall(r'[^\W\d_]+', parts[-2]) if len(parts) > 1 else []
    return bool(last and all(len(w) == 1 for w in last) and len(previous) == 1 and len(previous[0]) > 1)


def leading_author_year(text):
    text = normalize_text(text)
    match = YEAR.search(text)
    if match and is_author_prefix(text[:match.start()]):
        return match
    return None


def title_boundary(text):
    for boundary in re.finditer(r',\s+|\.\s+(?=[A-ZÀ-ÖØ-Þ])', text):
        prefix, tail = text[:boundary.start()], text[boundary.end():]
        if not is_author_prefix(prefix):
            continue
        next_part = re.split(r',\s+|\.\s+(?=[A-ZÀ-ÖØ-Þ])', tail, maxsplit=1)[0]
        if VENUE.match(tail):
            return prefix, '', boundary.end()
        surname_first = re.match(r'^[^,]+,\s*[A-Z]\.', tail)
        if re.match(r'^(?:and\b|&|[A-Z]\.)', tail) or (surname_first and is_author_prefix(tail.split(',', 1)[0] + ', A.')):
            continue  # Still inside an initial or surname-first author list.
        if not is_author_prefix(next_part) and len(re.findall(r'[^\W\d_]+', next_part)) >= 2:
            return prefix, next_part, boundary.end()
    return None


def author_year_span(lines, index):
    """Return final author/year line, protecting wrapped author names from splitting."""
    joined = ''
    for end in range(index, min(len(lines), index + 12)):
        line = lines[end].strip()
        if not line or re.match(r'^\[\d+\]', line):
            break
        if end > index and lines[end - 1].strip().endswith('.') and line[:1].isalpha():
            last_word = re.findall(r'[^\W\d_]+', lines[end - 1])
            if last_word and len(last_word[-1]) > 1:
                # A venue/location sentence belongs to the preceding reference.
                break
        joined += (' ' if joined else '') + line
        if len(joined) > 500:
            break
        match = leading_author_year(joined)
        if match:
            return end
        # Older author-title-year styles put the date at the end, not after names.
        boundary = title_boundary(joined)
        if boundary and boundary[1] and (',' in boundary[0] or re.search(r'\b[A-Z]\.', boundary[0])):
            return end
        if YEAR.search(joined):
            break
    return None


def parse_authors(prefix):
    if not is_author_prefix(prefix):
        return ()
    # Preserve the printed order and names (including surname-first APA names).
    parts = re.split(r'\s*(?:\band\b|&)\s*|\s*;\s*|\s*,\s*', prefix.strip(' (,.;'))
    result = []
    for part in parts:
        part = part.strip(' ,;')
        if not part or part in ('et al.', 'et al'):
            continue
        words = re.findall(r'[^\W\d_]+', part)
        if result and words and all(len(w) == 1 for w in words):
            result[-1] += ', ' + part
        else:
            result.append(part)
    return tuple(result[:40])


def extract_doi(text):
    match = DOI.search(text)
    if not match:
        return None
    value = match.group()
    offset = match.end()
    # Repair explicit wrapped separators inside a DOI, not ordinary prose.
    for _ in range(3):
        following = re.match(r'\s+([a-z0-9][^\s<>\]\[{}\"\']*)', text[offset:])
        if value[-1:] not in ('.', '/', '-') or not following:
            break
        value += following.group(1)
        offset += following.end()
    return value.rstrip('.,;:)')


def citation_fields(text):
    clean = re.sub(r'\s+', ' ', normalize_text(text)).strip()
    clean = re.sub(r'^\s*(?:\[\d{1,4}\]|\(?\d{1,4}[.)])\s*', '', clean)
    searchable = re.split(r'https?://|\bdoi\s*:|\b10\.\d{4,9}/', clean, maxsplit=1, flags=re.I)[0]
    dated = leading_author_year(searchable)
    quoted = QUOTED.search(searchable)
    authors = ()
    title = ''
    if dated:
        year = dated.group(1)
        authors = parse_authors(searchable[:dated.start()])
        tail = searchable[dated.end():].lstrip(' )].,;:')
        title = re.split(r'\.\s+(?=[A-ZÀ-ÖØ-Þ])', tail, maxsplit=1)[0]
    else:
        publication = re.search(r'\(((?:18|19|20)\d{2})\)', searchable)
        years = list(YEAR.finditer(searchable))
        year = publication.group(1) if publication else (years[-1].group(1) if years else None)
        # Author-list then comma-delimited title (Elsevier), or sentence title.
        boundary = title_boundary(searchable)
        if boundary:
            authors = parse_authors(boundary[0])
            title = boundary[1]
    quoted_prefix = searchable[:quoted.start()].strip(' ,.;') if quoted else ''
    quoted_title = quoted and (is_author_prefix(quoted_prefix) or (
        dated and not searchable[dated.end():quoted.start()].strip(' )].,;:')
    ))
    if quoted_title:
        title = quoted.group(1)
        if is_author_prefix(quoted_prefix):
            authors = parse_authors(quoted_prefix)
    title = title.strip(' \t\r\n.,;:()[]{}“”\"')
    # A trailing publication date is not part of an author-title-year title.
    title = re.sub(r'[.!?]\s*(?:18|19|20)\d{2}[a-z]?\s*$', '', title).strip()
    title = re.sub(r'(?<=[a-zÀ-ÖØ-öø-ÿ])-\s+(?=[a-zÀ-ÖØ-öø-ÿ])', '', title)
    # Require a real title segment, never a journal abbreviation or page number.
    if not re.search(r'[^\W\d_]{3}', title) or VENUE.match(title) or PUBLICATION_LOCATOR.search(title) or re.search(
        r'https?://|10\.\d{4,9}/|\b(?:vol(?:ume)?|pp?|pages?)\.?\s*\d|\b\d{2,4}\s*[-–—]\s*\d{2,4}\b', title, re.I
    ):
        title = ''
    return title[:1200], year, authors
