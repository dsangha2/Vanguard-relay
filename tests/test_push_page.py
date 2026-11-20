"""The phone-alerts control on the page, and the installable manifest."""
import http.client
import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolate  # noqa: F401
import relay


class PushPage(unittest.TestCase):
    def test_settings_control_is_in_the_page(self):
        text = (relay.HERE / 'web' / 'index.html').read_text(encoding='utf-8')
        for bit in (
            'id="push-on"',
            'id="push-help"',
            'id="push-test-btn"',
            'Phone alerts',
            'Send test alert',
            'Add to Home Screen',
            "register('/sw.js'",
            '/api/push/subscribe',
            '/api/push/unsubscribe',
            '/api/push/key',
            '/api/push/test',
            'userVisibleOnly:true',
            'requestPermission',
        ):
            self.assertIn(bit, text)
        menu = text.split('id="colour-control"', 1)[1].split('id="reset-colour-btn"', 1)[0]
        self.assertIn('id="push-on"', menu)
        self.assertIn('id="push-test-btn"', menu)

    def test_manifest_is_a_standalone_app(self):
        server = relay.ThreadingHTTPServer(('127.0.0.1', 0), relay.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        try:
            conn.request('GET', '/manifest.webmanifest')
            response = conn.getresponse()
            status, body = response.status, json.loads(response.read())
        finally:
            conn.close()
        self.assertEqual(status, 200)
        self.assertEqual(body['display'], 'standalone')
        self.assertGreaterEqual(len(body.get('icons') or []), 1)
