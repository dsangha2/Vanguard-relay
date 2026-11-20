"""Static caching, 304s and gzip in the HTTP handler."""
import gzip
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolate  # noqa: F401
import relay


class StaticCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        p = patch.object(relay, 'DATA', self.data); p.start(); self.addCleanup(p.stop)
        server = relay.ThreadingHTTPServer(('127.0.0.1', 0), relay.Handler)
        self.port = server.server_address[1]
        self.assertNotEqual(self.port, 8940)
        t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
        self.addCleanup(server.server_close); self.addCleanup(t.join, 3); self.addCleanup(server.shutdown)
        vendor = sorted((relay.HERE / 'web/vendor/three').glob('*.js'), key=lambda f: -f.stat().st_size)[0]
        self.vendor = '/vendor/three/' + vendor.name

    def get(self, path, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        c.request('GET', path, headers=headers or {})
        r = c.getresponse(); body = r.read(); c.close()
        return r, body

    def test_gzip_index_same_bytes(self):
        plain, raw = self.get('/')
        self.assertIsNone(plain.getheader('Content-Encoding'))
        r, body = self.get('/', {'Accept-Encoding': 'gzip, deflate'})
        self.assertEqual(r.getheader('Content-Encoding'), 'gzip')
        self.assertEqual(r.getheader('Vary'), 'Accept-Encoding')
        self.assertEqual(int(r.getheader('Content-Length')), len(body))
        self.assertLess(len(body), len(raw))
        self.assertEqual(gzip.decompress(body), raw)
        self.assertEqual(int(plain.getheader('Content-Length')), len(raw))

    def test_index_etag_304_stays_fresh(self):
        r, _ = self.get('/')
        self.assertEqual(r.getheader('Cache-Control'), 'no-cache')
        r2, body = self.get('/', {'If-None-Match': r.getheader('ETag')})
        self.assertEqual(r2.status, 304); self.assertEqual(body, b'')

    def test_vendor_long_cache_and_304(self):
        r, raw = self.get(self.vendor)
        self.assertEqual(r.getheader('Cache-Control'), 'public, max-age=31536000, immutable')
        etag = r.getheader('ETag'); self.assertTrue(etag)
        r2, body = self.get(self.vendor, {'If-None-Match': etag})
        self.assertEqual(r2.status, 304); self.assertEqual(body, b'')
        self.assertEqual(r2.getheader('ETag'), etag)
        r3, _ = self.get(self.vendor, {'If-None-Match': '"other"'})
        self.assertEqual(r3.status, 200)
        r4, body4 = self.get(self.vendor, {'Accept-Encoding': 'gzip'})
        self.assertEqual(gzip.decompress(body4), raw)
        self.assertEqual(r4.getheader('ETag'), etag)
        self.assertEqual(int(r4.getheader('Content-Length')), len(body4))

    def test_api_state_no_store(self):
        r, body = self.get('/api/state')
        self.assertEqual(r.getheader('Cache-Control'), 'no-store')
        self.assertIsNone(r.getheader('ETag'))
        self.assertEqual(r.getheader('Vary'), 'Accept-Encoding')
        r2, body2 = self.get('/api/state', {'Accept-Encoding': 'gzip'})
        self.assertEqual(r2.getheader('Cache-Control'), 'no-store')
        data = gzip.decompress(body2) if r2.getheader('Content-Encoding') == 'gzip' else body2
        self.assertIn('projects', json.loads(data))

    def test_uploads_never_gzipped_or_cached(self):
        raw = b'x' * 5000
        rec = dict(id='a' * 32, name='big.txt', kind='text/plain')
        with patch.object(relay, 'upload_record', return_value=rec), \
             patch.object(relay, 'upload_dir', return_value=self.data), \
             patch.object(relay, 'contained', return_value=type('P', (), {'read_bytes': lambda s: raw})()):
            r, body = self.get('/api/uploads/' + 'a' * 32, {'Accept-Encoding': 'gzip', 'If-None-Match': '*'})
        self.assertEqual(r.status, 200)
        self.assertIsNone(r.getheader('Content-Encoding')); self.assertIsNone(r.getheader('ETag'))
        self.assertEqual(r.getheader('Cache-Control'), 'no-store')
        self.assertEqual(body, raw)


if __name__ == '__main__':
    unittest.main()
