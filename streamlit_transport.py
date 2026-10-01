"""Session-scoped browser fetch -> ASGI transport for the shared frontend.

Only API routes are forwarded. No socket listener or global user-data cache.
Response frames remain queued until the browser acknowledges their sequence.
"""
import asyncio
import base64
import binascii
from collections import deque
import re
import threading
from urllib.parse import urlsplit

import httpx

from main import app

MAX_UPLOAD = 50 * 1024 * 1024
ROUTES = {
    'GET': re.compile(r'^/api/ai/(providers|gemini-models|maxplus-models)$'),
    'POST': re.compile(r'^/api/(references|doi|summarize|open-access/search|ai/(intents|synthesis|clusters|qa|compare|extract-references))$'),
}


def make_request(message):
    path = message.get('path', '')
    parsed = urlsplit(path)
    method = message.get('method', 'GET').upper()
    if parsed.scheme or parsed.netloc or not ROUTES.get(method, re.compile(r'(?!)')).fullmatch(parsed.path):
        raise ValueError('Unsupported API route')
    headers = {k: v for k, v in message.get('headers', {}).items()
               if k.lower() in ('x-ai-api-key', 'x-openai-api-key', 'content-type')}
    payload = message.get('body') or {}
    options = {'headers': headers}
    if payload.get('kind') == 'form':
        fields, files = {}, {}
        size = 0
        for entry in payload.get('entries', []):
            if entry.get('file'):
                encoded = entry.get('data', '')
                if len(encoded) > (MAX_UPLOAD + 2) * 4 // 3:
                    raise ValueError('Upload too large')
                content = base64.b64decode(encoded, validate=True)
                size += len(content)
                if size > MAX_UPLOAD:
                    raise ValueError('Upload too large')
                files[entry['name']] = (entry.get('filename', 'paper.pdf'), content, entry.get('mime', 'application/pdf'))
            else:
                value = str(entry.get('value', ''))
                size += len(value.encode())
                if size > MAX_UPLOAD + 1024 * 1024:
                    raise ValueError('Request too large')
                fields[entry['name']] = value
        options['headers'] = {k: v for k, v in headers.items() if k.lower() != 'content-type'}
        options.update(data=fields, files=files or None)
    elif payload.get('kind') == 'text':
        content = payload.get('text', '').encode()
        if len(content) > 8 * 1024 * 1024:
            raise ValueError('Request too large')
        options['content'] = content
    return httpx.Request(method, 'http://paperref.internal' + path, **options)


async def forward(request, emit):
    body = request.read()
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
             'http_version': '1.1', 'method': request.method, 'scheme': 'http',
             'path': request.url.path, 'raw_path': request.url.raw_path.split(b'?')[0],
             'query_string': request.url.query, 'root_path': '',
             'headers': [(k.lower(), v) for k, v in request.headers.raw],
             'server': ('paperref.internal', 80), 'client': ('127.0.0.1', 0)}
    sent = False
    wait = asyncio.Event()

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {'type': 'http.request', 'body': body, 'more_body': False}
        await wait.wait()
        return {'type': 'http.disconnect'}

    async def send(message):
        if message['type'] == 'http.response.start':
            emit({'kind': 'start', 'status': message['status'],
                  'headers': {k.decode(): v.decode() for k, v in message['headers']}})
        elif message['type'] == 'http.response.body':
            content = message.get('body', b'')
            for offset in range(0, len(content), 48 * 1024):
                emit({'kind': 'chunk', 'data': base64.b64encode(content[offset:offset + 48 * 1024]).decode()})
            if not message.get('more_body', False):
                emit({'kind': 'end'})
    await app(scope, receive, send)


class Job:
    def __init__(self):
        self.loop = None
        self.task = None
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        if self.loop and self.task and not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:
                pass  # The loop may have finished between the check and cancel.


class BrowserSession:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}
        self.seen = set()
        self.seen_order = deque()
        self.events = deque()
        self.sequence = 0
        self.frame = None
        self.needs_page = True
        self.last_message = None

    def emit(self, request_id, event):
        with self.lock:
            self.sequence += 1
            self.events.append({**event, 'id': request_id, 'seq': self.sequence})

    def submit(self, message):
        request_id = message.get('id')
        if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,80}', request_id):
            return
        with self.lock:
            if request_id in self.seen:
                return
            self.seen.add(request_id); self.seen_order.append(request_id)
            while len(self.seen_order) > 512:
                self.seen.discard(self.seen_order.popleft())
            if len(self.jobs) >= 4:
                self.emit(request_id, {'kind': 'error', 'detail': 'รอคำขอปัจจุบันเสร็จก่อน'})
                return
            job = Job(); self.jobs[request_id] = job

        def run():
            async def execute():
                job.loop = asyncio.get_running_loop(); job.task = asyncio.current_task()
                if job.cancelled:
                    return
                request = make_request(message)
                # Owner/provider timeouts still apply inside each API request.
                await forward(request, lambda event: self.emit(request_id, event))
            try:
                asyncio.run(execute())
            except asyncio.CancelledError:
                self.emit(request_id, {'kind': 'error', 'detail': 'ยกเลิกคำขอแล้ว'})
            except (ValueError, UnicodeError, TypeError, KeyError, binascii.Error):
                self.emit(request_id, {'kind': 'error', 'detail': 'ข้อมูลคำขอหรือ API key ไม่ถูกต้อง'})
            except Exception:
                self.emit(request_id, {'kind': 'error', 'detail': 'ประมวลผลไม่สำเร็จ ผลชุดเดิมยังอยู่'})
            finally:
                with self.lock:
                    self.jobs.pop(request_id, None)
        threading.Thread(target=run, name='paperref-api', daemon=True).start()

    def accept(self, message):
        if not isinstance(message, dict) or message.get('nonce') == self.last_message:
            return
        self.last_message = message.get('nonce')
        frame = message.get('frame')
        if message.get('type') == 'boot' and frame != self.frame:
            with self.lock:
                for job in self.jobs.values(): job.cancel()
                self.frame = frame; self.events.clear(); self.needs_page = True
            return
        if frame != self.frame:
            return
        if message.get('type') == 'ready':
            self.needs_page = False
        acknowledged = message.get('ack', 0)
        if isinstance(acknowledged, int):
            with self.lock:
                while self.events and self.events[0]['seq'] <= acknowledged:
                    self.events.popleft()
        for request in message.get('requests', []):
            if isinstance(request, dict): self.submit(request)
        with self.lock:
            for request_id in message.get('cancel', []):
                if request_id in self.jobs: self.jobs[request_id].cancel()

    def snapshot(self):
        with self.lock:
            return {'frame': self.frame, 'events': list(self.events), 'accepted': list(self.seen)}
