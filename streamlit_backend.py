"""Call the existing ASGI app in-process, including incremental NDJSON events.

No second server, public port, browser-to-localhost fetch or persisted API key.
This module does not import Streamlit, so the adapter can be tested offline.
"""
import asyncio
import json
from urllib.parse import urlsplit

import httpx

from main import app


class APIError(Exception):
    def __init__(self, status, detail):
        self.status = status
        super().__init__(detail if isinstance(detail, str) else 'ข้อมูลคำขอไม่ถูกต้อง')


async def _call_api(path, *, method='POST', data=None, files=None, headers=None,
                    json_body=None, on_event=None):
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith('/api/'):
        raise ValueError('Only local /api/ routes are supported')
    try:
        request = httpx.Request(method, 'http://paperref.internal' + path, data=data,
                                files=files, headers=headers, json=json_body)
    except (ValueError, UnicodeError, TypeError):
        raise APIError(422, 'รูปแบบคำขอหรือ API key ไม่ถูกต้อง กรุณาตรวจค่าที่กรอก') from None
    body = request.read()
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
             'http_version': '1.1', 'method': method, 'scheme': 'http',
             'path': parsed.path, 'raw_path': parsed.path.encode(),
             'query_string': parsed.query.encode(), 'root_path': '',
             'headers': [(k.lower(), v) for k, v in request.headers.raw], 'server': ('paperref.internal', 80),
             'client': ('127.0.0.1', 0)}
    sent_body = False
    disconnected = asyncio.Event()
    status = 500
    response_headers = {}
    chunks = []
    pending = b''
    events = []

    async def receive():
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {'type': 'http.request', 'body': body, 'more_body': False}
        await disconnected.wait()
        return {'type': 'http.disconnect'}

    def consume(line):
        if not line.strip():
            return
        event = json.loads(line)
        events.append(event)
        if on_event:
            on_event(event)

    async def send(message):
        nonlocal status, response_headers, pending
        if message['type'] == 'http.response.start':
            status = message['status']
            response_headers = {k.decode().lower(): v.decode() for k, v in message['headers']}
        elif message['type'] == 'http.response.body':
            chunk = message.get('body', b'')
            if 'application/x-ndjson' in response_headers.get('content-type', ''):
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    consume(line)
                if not message.get('more_body') and pending:
                    consume(pending); pending = b''
            else:
                chunks.append(chunk)

    try:
        await app(scope, receive, send)
    except Exception:
        raise APIError(500, 'แอปประมวลผลไม่สำเร็จ กรุณาตรวจการตั้งค่าหรือลองใหม่ ผลชุดเดิมยังอยู่') from None
    if events:
        for event in events:
            if event['type'] == 'error':
                raise APIError(event.get('status', 502), event.get('detail', 'การค้นหาไม่สำเร็จ'))
        result = next((e['payload'] for e in events if e['type'] == 'result'), None)
        if result is not None:
            return result
        if events[-1]['type'] != 'done':
            raise APIError(502, 'การเชื่อมต่อจบก่อนประมวลผลครบ')
        return {'results': [e['result'] for e in events if e['type'] == 'item'],
                'total': events[-1].get('total', 0)}
    try:
        payload = json.loads(b''.join(chunks))
    except ValueError:
        raise APIError(502, 'ผลตอบกลับมีรูปแบบไม่ถูกต้อง') from None
    if status >= 400:
        raise APIError(status, payload.get('detail', 'คำขอไม่สำเร็จ'))
    return payload


def call_api(path, **kwargs):
    """Run on the Streamlit script thread; callbacks can update st.status safely."""
    return asyncio.run(_call_api(path, **kwargs))
