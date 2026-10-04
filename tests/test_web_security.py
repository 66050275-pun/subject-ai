import asyncio
import base64
import hashlib
import unittest

import httpx
from main import app
from streamlit_frontend import frontend_html
from web_security import content_policy


class WebSecurityTests(unittest.TestCase):
    def test_trusted_inline_scripts_are_hashed(self):
        script = 'window.safe = true;'
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        policy = content_policy('<script>' + script + '</script><script src="/static/graph.js"></script>')
        self.assertIn("'sha256-" + digest + "'", policy)
        self.assertNotIn("'unsafe-inline'", policy.split(';')[1])
        self.assertIn("object-src 'none'", policy)
        self.assertIn('worker-src blob:', policy)
        self.assertNotIn('blob:', policy.split(';')[1])  # Worker asset must not permit arbitrary page scripts.

    def test_sensitive_and_homepage_response_headers(self):
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://paperref.test') as client:
                home = await client.get('/')
                self.assertEqual(home.status_code, 200)
                self.assertIn("frame-ancestors 'self'", home.headers['content-security-policy'])
                self.assertEqual(home.headers['referrer-policy'], 'no-referrer')
                invalid = await client.post('/api/references')
                self.assertEqual(invalid.status_code, 422)
                self.assertEqual(invalid.headers['cache-control'], 'no-store')
                self.assertEqual(invalid.headers['x-content-type-options'], 'nosniff')
        asyncio.run(check())

    def test_component_has_csp_and_no_cdn_script(self):
        html = frontend_html()
        self.assertIn('http-equiv="Content-Security-Policy"', html)
        self.assertNotIn('cdn.tailwindcss.com', html)
        self.assertNotIn('<script src=', html)
