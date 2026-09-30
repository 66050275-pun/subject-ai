"""Bounded discovery of legal full-text copies through public academic APIs.

Links are provider-indexed, not byte-verified downloads. No arbitrary web
scraping, publisher credentials, paywall bypass or server PDF proxy is used.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import time
from difflib import SequenceMatcher
from urllib.parse import quote, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import httpx


def safe_url(value):
    if not isinstance(value, str) or len(value) > 4000:
        return None
    try:
        p = urlsplit(value)
        if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
            return None
        if p.port not in (None, 80, 443) or any(c.isspace() for c in value):
            return None
        # Links are opened by the browser only, never fetched by this module.
        if p.hostname in ('localhost', 'metadata.google.internal') or ':' in p.hostname:
            return None
        try:
            if not ipaddress.ip_address(p.hostname).is_global:
                return None
        except ValueError:
            pass
        return urlunsplit((p.scheme, p.netloc, p.path, p.query, ''))
    except ValueError:
        return None


def doi_key(value):
    value = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', str(value or ''), flags=re.I)
    return value.strip().casefold()


def normalized_title(value):
    return re.sub(r'\W+', '', str(value or '').casefold())


def matches(record_doi, record_title, doi, title):
    """A conflicting DOI never wins on fuzzy title similarity."""
    if doi and record_doi:
        return doi_key(record_doi) == doi_key(doi)
    a, b = normalized_title(title), normalized_title(record_title)
    return len(a) >= 20 and len(b) >= 20 and SequenceMatcher(None, a, b).ratio() >= .93


def location(url, source, *, landing=None, version=None, license=None, host_type=None, matched_by='doi'):
    url = safe_url(url)
    if not url:
        return None
    return {'url': url, 'source': source, 'landing_url': safe_url(landing),
            'version': version or 'unknown', 'license': license or None,
            'host_type': host_type or None, 'matched_by': matched_by,
            'verification': 'provider_indexed'}


def merge_locations(result, locations):
    existing = result.setdefault('pdf_locations', [])
    for item in locations:
        if not isinstance(item, dict) or not safe_url(item.get('url')):
            continue
        found = next((old for old in existing if safe_url(old.get('url')) == safe_url(item['url'])), None)
        if found:
            for field in ('version', 'license', 'landing_url', 'host_type'):
                if found.get(field) in (None, '', 'unknown') and item.get(field):
                    found[field] = item[field]
            continue
        existing.append({**item, 'url': safe_url(item['url'])})
    rank = {'publishedVersion': 0, 'acceptedVersion': 1, 'submittedVersion': 2, 'unknown': 3}
    existing.sort(key=lambda x: rank.get(x.get('version'), 3))
    if existing:
        result['oa_pdf_url'] = existing[0]['url']


def openalex_locations(work):
    locations = [work.get('best_oa_location'), *(work.get('locations') or [])]
    return [location(row.get('pdf_url'), 'OpenAlex', landing=row.get('landing_page_url'),
                     version=row.get('version'), license=row.get('license'),
                     host_type=(row.get('source') or {}).get('type'))
            for row in locations if isinstance(row, dict) and row.get('is_oa') is True and row.get('pdf_url')]


def openalex_records(work):
    records = []
    for row in [work.get('best_oa_location'), *(work.get('locations') or [])]:
        if not isinstance(row, dict) or row.get('is_oa') is not True:
            continue
        url = safe_url(row.get('landing_page_url'))
        source = row.get('source') or {}
        name = source.get('display_name') if isinstance(source, dict) else None
        if url and not any(record['url'] == url for record in records):
            records.append({'name': 'Open Access · ' + (name or 'repository'), 'url': url})
    return records


class OADiscovery:
    """One lookup session: bounded per-source calls, memoization and 429 circuit breaker."""
    def __init__(self, client, contact_email=None):
        self.client = client
        self.email = contact_email or os.getenv('PAPERREF_CONTACT_EMAIL', '').strip()
        self.core_key = os.getenv('CORE_API_KEY', '').strip()
        self.gates = {}
        self.cache = {}
        self.blocked = {}
        self.next_arxiv_request = 0.0

    async def request(self, source, url, params=None, headers=None, xml=False):
        key = (source, url, tuple(sorted((params or {}).items())))
        if key in self.cache:
            return await self.cache[key]
        if len(self.cache) >= 128:
            for old_key, old_task in list(self.cache.items()):
                if old_task.done():
                    del self.cache[old_key]
                    if len(self.cache) < 128:
                        break

        async def fetch():
            gate = self.gates.setdefault(source, asyncio.Semaphore(1 if source == 'arXiv' else 2))
            async with gate:
                if source in self.blocked:
                    return None, self.blocked[source]
                if source == 'arXiv':
                    await asyncio.sleep(max(0, self.next_arxiv_request - time.monotonic()))
                    self.next_arxiv_request = time.monotonic() + 3
                try:
                    response = await self.client.get(url, params=params, headers=headers,
                                                     follow_redirects=False, timeout=8)
                    if response.status_code in (401, 403, 429):
                        self.blocked[source] = f'HTTP {response.status_code}'
                    if response.is_error or response.is_redirect:
                        return None, f'HTTP {response.status_code}'
                    if len(response.content) > 2 * 1024 * 1024:
                        return None, 'response too large'
                    return (response.text if xml else response.json()), None
                except (httpx.HTTPError, ValueError):
                    return None, 'unavailable'
        task = asyncio.create_task(fetch())
        self.cache[key] = task
        return await task

    async def unpaywall(self, doi, title, original):
        if not doi or not self.email:
            return [], [], 'requires contact email' if not self.email else None
        data, error = await self.request('Unpaywall', 'https://api.unpaywall.org/v2/' + quote(doi, safe=''), {'email': self.email})
        copies, records = [], []
        if isinstance(data, dict) and doi_key(data.get('doi')) == doi_key(doi):
            for row in [data.get('best_oa_location'), *(data.get('oa_locations') or [])]:
                if not isinstance(row, dict):
                    continue
                copies.append(location(row.get('url_for_pdf'), 'Unpaywall', landing=row.get('url_for_landing_page'),
                                       version=row.get('version'), license=row.get('license'), host_type=row.get('host_type')))
                if safe_url(row.get('url_for_landing_page')):
                    records.append({'name': 'Unpaywall · ' + str(row.get('host_type') or 'Open Access'),
                                    'url': safe_url(row['url_for_landing_page'])})
        return copies, records, error

    async def europe_pmc(self, doi, title, original):
        if not doi and not title:
            return [], [], None
        query = 'DOI:"' + doi + '"' if doi else 'TITLE:"' + title.replace('"', '') + '"'
        data, error = await self.request('Europe PMC', 'https://www.ebi.ac.uk/europepmc/webservices/rest/search',
                                         {'query': query, 'format': 'json', 'resultType': 'core', 'pageSize': 5})
        copies, records = [], []
        for row in ((data or {}).get('resultList') or {}).get('result', []) if isinstance(data, dict) else []:
            if not isinstance(row, dict) or not matches(row.get('doi'), row.get('title'), doi, title):
                continue
            pmcid = str(row.get('pmcid') or '')
            if not re.fullmatch(r'PMC\d+', pmcid) or row.get('isOpenAccess') != 'Y':
                continue
            landing = 'https://europepmc.org/articles/' + pmcid
            records.append({'name': 'Europe PMC · Open Access', 'url': landing})
            for link in (row.get('fullTextUrlList') or {}).get('fullTextUrl', []) or []:
                if isinstance(link, dict) and str(link.get('documentStyle', '')).lower() == 'pdf' and link.get('availabilityCode') == 'OA':
                    copies.append(location(link.get('url'), 'Europe PMC', landing=landing, version='publishedVersion'))
            copies.append(location('https://pmc.ncbi.nlm.nih.gov/articles/' + pmcid + '/pdf/',
                                   'PubMed Central', landing=landing, version='publishedVersion', host_type='repository'))
        return copies, records, error

    async def arxiv(self, doi, title, original, search_title=False):
        ids = re.findall(r'(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+/\d{7}(?:v\d+)?)', original or '', re.I)
        if not ids and not search_title:
            return [], [], None
        if not ids and len(normalized_title(title)) < 20:
            return [], [], None
        params = {'id_list': ids[0]} if ids else {'search_query': 'ti:"' + title.replace('"', '') + '"', 'max_results': 3}
        data, error = await self.request('arXiv', 'https://export.arxiv.org/api/query', params, xml=True)
        copies, records = [], []
        if isinstance(data, str) and '<!DOCTYPE' not in data.upper() and '<!ENTITY' not in data.upper():
            try:
                root = ET.fromstring(data)
                ns = {'a': 'http://www.w3.org/2005/Atom', 'x': 'http://arxiv.org/schemas/atom'}
                for entry in root.findall('a:entry', ns):
                    record_title = ' '.join(entry.findtext('a:title', '', ns).split())
                    record_doi = entry.findtext('x:doi', '', ns)
                    # Explicit ID can stand alone when no usable title/DOI exists.
                    if not (ids and not title and not doi) and not matches(record_doi, record_title, doi, title):
                        continue
                    arxiv_id = entry.findtext('a:id', '', ns).rsplit('/abs/', 1)[-1]
                    if not re.fullmatch(r'\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+/\d{7}(?:v\d+)?', arxiv_id):
                        continue
                    landing = 'https://arxiv.org/abs/' + arxiv_id
                    records.append({'name': 'arXiv · preprint', 'url': landing})
                    copies.append(location('https://arxiv.org/pdf/' + arxiv_id, 'arXiv', landing=landing,
                                           version='submittedVersion', host_type='repository', matched_by='doi' if doi and record_doi else 'title' if not ids else 'arxiv_id'))
            except ET.ParseError:
                error = 'invalid response'
        return copies, records, error

    async def hal(self, doi, title, original):
        query = 'doiId_s:"' + doi + '"' if doi else 'title_t:"' + title.replace('"', '') + '"'
        data, error = await self.request('HAL', 'https://api.archives-ouvertes.fr/search/',
            {'q': query, 'fl': 'title_s,doiId_s,fileMain_s,uri_s', 'rows': 5, 'wt': 'json'})
        copies, records = [], []
        for row in ((data or {}).get('response') or {}).get('docs', []) if isinstance(data, dict) else []:
            if not isinstance(row, dict):
                continue
            title_value = row.get('title_s') or []
            candidate_title = title_value[0] if isinstance(title_value, list) and title_value else title_value
            if not matches(row.get('doiId_s'), candidate_title, doi, title):
                continue
            if safe_url(row.get('uri_s')):
                records.append({'name': 'HAL · author deposit', 'url': safe_url(row['uri_s'])})
            copies.append(location(row.get('fileMain_s'), 'HAL', landing=row.get('uri_s'), host_type='repository', matched_by='doi' if doi and row.get('doiId_s') else 'title'))
        return copies, records, error

    async def zenodo(self, doi, title, original):
        query = '(doi:"' + doi + '" OR related_identifiers.identifier:"' + doi + '")' if doi else 'metadata.title:"' + title.replace('"', '') + '"'
        data, error = await self.request('Zenodo', 'https://zenodo.org/api/records', {'q': query, 'size': 5})
        copies, records = [], []
        for row in ((data or {}).get('hits') or {}).get('hits', []) if isinstance(data, dict) else []:
            if not isinstance(row, dict):
                continue
            meta = row.get('metadata') or {}
            primary = meta.get('doi') or row.get('doi')
            related = any(isinstance(r, dict) and doi_key(r.get('identifier')) == doi_key(doi)
                          and r.get('relation') in ('isVersionOf', 'isIdenticalTo', 'isPreprintOf', 'isPreviousVersionOf')
                          for r in meta.get('related_identifiers', []) or []) if doi else False
            if not (related or matches(primary, meta.get('title'), doi, title)):
                continue
            if meta.get('access_right') != 'open' or meta.get('resource_type', {}).get('type') != 'publication':
                continue
            landing = safe_url((row.get('links') or {}).get('html'))
            if landing:
                records.append({'name': 'Zenodo · author deposit', 'url': landing})
            for file in row.get('files', []) or []:
                if isinstance(file, dict) and str(file.get('key') or '').lower().endswith('.pdf'):
                    copies.append(location((file.get('links') or {}).get('self'), 'Zenodo', landing=landing,
                        license=(meta.get('license') or {}).get('id'), host_type='repository', matched_by='related_doi' if related else 'doi' if doi else 'title'))
        return copies, records, error

    async def core(self, doi, title, original):
        if not self.core_key:
            return [], [], 'requires CORE_API_KEY'
        query = 'doi:"' + doi + '"' if doi else 'title:"' + title.replace('"', '') + '"'
        data, error = await self.request('CORE', 'https://api.core.ac.uk/v3/search/works', {'q': query, 'limit': 5},
                                         headers={'Authorization': 'Bearer ' + self.core_key})
        copies, records = [], []
        for row in data.get('results', []) if isinstance(data, dict) else []:
            if not isinstance(row, dict) or not matches(row.get('doi'), row.get('title'), doi, title):
                continue
            landing = 'https://core.ac.uk/works/' + str(row['id']) if str(row.get('id', '')).isdigit() else None
            if landing:
                records.append({'name': 'CORE · repository record', 'url': landing})
            copies.append(location(row.get('downloadUrl'), 'CORE', landing=landing, host_type='repository', matched_by='doi' if doi and row.get('doi') else 'title'))
        return copies, records, error

    async def enrich(self, result, deep=False):
        doi = result.get('doi')
        title = result.get('matched_title') or result.get('title') or ''
        original = result.get('original_text') or ''
        if result.get('arxiv_id'):
            original += ' arXiv:' + str(result['arxiv_id'])
        jobs = [('Unpaywall', self.unpaywall), ('Europe PMC', self.europe_pmc)]
        if deep:
            jobs += [('HAL', self.hal), ('Zenodo', self.zenodo), ('CORE', self.core)]
        async def run(source, function):
            try:
                copies, records, error = await function(doi, title, original)
                return source, copies, records, error
            except (ValueError, TypeError, AttributeError, KeyError):
                return source, [], [], 'invalid response'
        outcomes = await asyncio.gather(*(run(name, fn) for name, fn in jobs),
            run('arXiv', lambda d, t, o: self.arxiv(d, t, o, search_title=deep)))
        for source, copies, records, error in outcomes:
            merge_locations(result, copies)
            for record in records:
                if record not in result.setdefault('source_links', []):
                    result['source_links'].append(record)
                if source not in result.setdefault('metadata_sources', []):
                    result['metadata_sources'].append(source)
            result.setdefault('oa_search', {})[source] = error or ('found' if any(copies) or records else 'not found')
        return result
