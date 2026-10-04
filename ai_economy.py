"""Opt-in compact evidence and request-local AI pacing. No persistent user state."""
import asyncio
import math
import re
import time

SOURCE_LIMIT = 6000
CONTEXT_NOTICE = 'โหมดประหยัดใช้ข้อความคัดย่อและ abstract บางส่วน คำตอบอาจไม่ครอบคลุมทั้งเปเปอร์ โปรดตรวจต้นฉบับ'
OUTPUT_LIMITS = {'summary': 1600, 'qa': 1400, 'synthesis': 2000, 'compare': 2200,
                 'intents': 2200, 'clusters': 1600, 'extract-references': 4000}
_OMISSION = '\n[Economy mode: other source passages omitted]\n'
_STOP = {'what', 'which', 'how', 'this', 'that', 'with', 'from', 'paper', 'research',
         'does', 'have', 'were', 'about', 'their', 'these', 'there', 'please', 'explain',
         'the', 'and', 'for', 'are', 'was', 'is', 'of', 'to', 'in', 'on', 'as', 'by'}


def _blocks(text):
    """Preserve source order; split long paragraphs into small local retrieval units."""
    units = []
    for part in re.split(r'\n\s*\n', text):
        part = part.strip()
        while len(part) > 800:
            boundary = max(part.rfind('\n', 400, 800), part.rfind(' ', 400, 800))
            end = boundary if boundary > 0 else 800
            units.append(part[:end].strip())
            part = part[end:].strip()
        if part:
            units.append(part)
    return units


def compact_source(text, *, feature='summary', question='', max_chars=SOURCE_LIMIT):
    """Select bounded, labelled excerpts without claiming full-paper retrieval."""
    text = str(text).strip()
    if len(text) <= max_chars:
        return text
    if max_chars < len(_OMISSION) + 100:
        raise ValueError('Source budget too small')
    blocks = _blocks(text)
    terms = {w for w in re.findall(r'[^\W_]{3,}', question.casefold()) if w not in _STOP}
    ranked = []
    headings = re.compile(r'\b(abstract|conclusions?|results?|limitations?|methods?|methodology|discussion)\b', re.I)
    for index, block in enumerate(blocks):
        folded = block.casefold()
        relevance = sum(min(3, folded.count(term)) for term in terms)
        heading_score = bool(headings.search(block[:140]))
        ranked.append((relevance * 10 + int(heading_score) * 3, index))
    # Source beginning/end supply basic context; question matches get priority.
    candidates = []
    if feature == 'qa' and terms and any(score >= 10 for score, _ in ranked):
        for score, index in sorted(ranked, reverse=True):
            if score < 10:
                continue
            candidates.extend([index, max(0, index - 1), min(len(blocks) - 1, index + 1)])
    candidates.extend([0, len(blocks) - 1, 1 if len(blocks) > 1 else 0])
    candidates.extend(index for _, index in sorted(ranked, reverse=True))
    selected = set()
    used = len(_OMISSION)
    for index in dict.fromkeys(candidates):
        cost = len(blocks[index]) + len(_OMISSION) + 1
        if used + cost <= max_chars:
            selected.add(index)
            used += cost
    # Every selected excerpt is literal source text and stays in source order.
    snippets, previous = [], -2
    for index in sorted(selected):
        if index != previous + 1:
            snippets.append(_OMISSION)
        snippets.append(blocks[index] + '\n')
        previous = index
    snippets.append(_OMISSION)
    result = ''.join(snippets)
    if len(result) > max_chars:
        raise ValueError('Selected evidence exceeded source budget')
    return result


def compact_papers(papers, *, feature='synthesis'):
    """Keep every ID, distributing a small abstract budget across all records."""
    per_abstract = min(250 if feature == 'clusters' else 600, 3000 // max(1, len(papers)))
    title_limit = 120 if feature == 'clusters' else 160
    rows = []
    for paper in papers:
        title = str(paper.get('matched_title') or paper.get('title') or '')[:title_limit]
        abstract = str(paper.get('abstract') or '')[:per_abstract]
        row = {'id': paper['id'], 'title': title,
               'evidence_level': 'abstract excerpt' if abstract else 'title/metadata only'}
        if paper.get('doi'):
            row['doi'] = paper['doi']
        if abstract:
            row['abstract'] = abstract
        rows.append(row)
    return rows


def compact_history(messages):
    return [{'role': message['role'], 'content': str(message['content'])[:600]}
            for message in messages[-4:]]


class EconomyCaller:
    """A serial, cancellable caller belonging to one API action, never a user cache."""
    def __init__(self, generator, feature, interval_seconds=30, progress=None, *, clock=None, sleeper=None):
        self.generator, self.feature = generator, feature
        self.interval_seconds = interval_seconds
        self.progress = progress
        self._clock, self._sleep = clock, sleeper
        self._last_started = None
        self._lock = asyncio.Lock()
        self.requests = 0
        self.prompt_characters = 0
        self.output_token_limits = []

    def _now(self):
        return (self._clock or time.monotonic)()

    async def _wait(self):
        if self._last_started is None:
            return
        remaining = self.interval_seconds - (self._now() - self._last_started)
        last_report = None
        while remaining > .001:
            seconds = math.ceil(remaining)
            if self.progress and (last_report is None or last_report - seconds >= 5):
                await self.progress({'type': 'progress', 'stage': 'waiting', 'wait_seconds': seconds,
                                     'economy_mode': True})
                last_report = seconds
            await (self._sleep or asyncio.sleep)(min(1, remaining))
            remaining = self.interval_seconds - (self._now() - self._last_started)

    async def __call__(self, *args, **kwargs):
        async with self._lock:
            await self._wait()
            args = list(args)
            prompt = args[3] if len(args) > 3 else kwargs['prompt']
            instruction = ('\nEconomy mode: respond concisely using only supplied evidence. '
                           'Source passages/abstracts may be omitted; explicitly state insufficient evidence. '
                           'For structured output return ONLY the requested JSON; preserve every supplied ID, '
                           'use short reasons, and do not repeat evidence or add explanations.')
            prompt += instruction
            if len(args) > 3:
                args[3] = prompt
            else:
                kwargs['prompt'] = prompt
            limit = min(kwargs.get('max_output_tokens', 6000), OUTPUT_LIMITS[self.feature])
            kwargs.update(max_output_tokens=limit, economy_mode=True)
            self._last_started = self._now()
            self.requests += 1
            self.prompt_characters += len(prompt)
            self.output_token_limits.append(limit)
            return await self.generator(*args, **kwargs)

    def metadata(self, context_notice=CONTEXT_NOTICE):
        return {'enabled': True, 'requests': self.requests, 'prompt_characters': self.prompt_characters,
                'interval_seconds': self.interval_seconds, 'context_notice': context_notice,
                'output_token_limits': list(self.output_token_limits)}
