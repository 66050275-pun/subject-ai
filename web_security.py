"""Browser security headers without buffering streamed ASGI responses."""
import base64
import hashlib
import re
from pathlib import Path


def content_policy(html):
    hashes = ["'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"
              for attrs, script in re.findall(r'<script([^>]*)>(.*?)</script>', html, flags=re.S)
              if not re.search(r'\bsrc\s*=', attrs)]
    return ("default-src 'self'; script-src 'self' " + ' '.join(hashes) +
            "; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'")


class SecurityHeaders:
    def __init__(self, app):
        self.app = app
        html = (Path(__file__).resolve().parent / 'static/index.html').read_text()
        self.policy = content_policy(html) + "; frame-ancestors 'self'"

    async def __call__(self, scope, receive, send):
        async def secured_send(message):
            if message['type'] == 'http.response.start':
                headers = list(message.get('headers', []))
                headers.extend([(b'x-content-type-options', b'nosniff'),
                                (b'referrer-policy', b'no-referrer'),
                                (b'permissions-policy', b'camera=(), microphone=(), geolocation=()')])
                if scope.get('path', '').startswith('/api/'):
                    headers.append((b'cache-control', b'no-store'))
                if scope.get('path') == '/':
                    headers.extend([(b'content-security-policy', self.policy.encode()),
                                    (b'cache-control', b'no-store')])
                message = {**message, 'headers': headers}
            await send(message)
        await self.app(scope, receive, secured_send)


class MemoryOnlyUploads:
    """Limit API bodies below multipart spooling threshold, including chunked uploads."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        from fastapi import HTTPException
        total = 0
        async def bounded_receive():
            nonlocal total
            message = await receive()
            total += len(message.get('body', b''))
            if total > 64 * 1024 * 1024:
                raise HTTPException(413, 'Request exceeds memory upload limit')
            return message
        await self.app(scope, bounded_receive if scope.get('path', '').startswith('/api/') else receive, send)
