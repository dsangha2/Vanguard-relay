"""Phone-alert core: allowed hosts, VAPID, subscriptions, send, rate limit. No HTTP routes."""
import base64
import io
import json
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolate  # noqa: F401
import p256
import relay


def b64u_decode(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def xy_from_public(public):
    raw = b64u_decode(public)
    if len(raw) != 65 or raw[0] != 4:
        raise AssertionError('public key is not an uncompressed P-256 point')
    return int.from_bytes(raw[1:33], 'big'), int.from_bytes(raw[33:], 'big')


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(n) if n else b''
        self.server.hits.append({
            'method': self.command,
            'path': self.path,
            'headers': {k.lower(): v for k, v in self.headers.items()},
            'body_len': len(body),
        })
        self.server.entered.set()
        self.server.hold.wait(5)
        code = self.server.codes.get(self.path, self.server.status)
        self.send_response(code)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, fmt, *args):
        return


class LocalServer(HTTPServer):
    allow_reuse_address = True


def sub(endpoint, p256dh='p256dh-key', auth='auth-key'):
    return {'endpoint': endpoint, 'keys': {'p256dh': p256dh, 'auth': auth}}


class PushCore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.data_patch = patch.object(relay, 'DATA', self.data)
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        relay._PUSH_SENT.clear()
        self.addCleanup(relay._PUSH_SENT.clear)
        self.server = LocalServer(('127.0.0.1', 0), Handler)
        self.server.status = 201
        self.server.codes = {}
        self.server.hits = []
        self.server.hold = threading.Event()
        self.server.hold.set()
        self.server.entered = threading.Event()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def stop_server(self):
        self.server.hold.set()
        self.server.shutdown()
        self.server.server_close()

    def url(self, path):
        return self.base + path

    def allow_local(self):
        """The host check refuses 127.0.0.1. Sending tests allow only this server's base URL."""
        base = self.base
        real = relay.push_host_ok

        def ok(endpoint):
            return (isinstance(endpoint, str) and endpoint.startswith(base)) or real(endpoint)

        patcher = patch.object(relay, 'push_host_ok', ok)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_jwt(self, token, aud, lo, hi, contact='mailto:vanguard@example.com'):
        parts = token.split('.')
        self.assertEqual(len(parts), 3)
        header = json.loads(b64u_decode(parts[0]))
        claims = json.loads(b64u_decode(parts[1]))
        self.assertEqual(header, {'typ': 'JWT', 'alg': 'ES256'})
        self.assertEqual(claims['aud'], aud)
        self.assertGreaterEqual(claims['exp'], lo + 12 * 3600 - 5)
        self.assertLessEqual(claims['exp'], hi + 12 * 3600 + 5)
        self.assertEqual(claims['sub'], contact)
        sig = b64u_decode(parts[2])
        self.assertEqual(len(sig), 64)
        signing = (parts[0] + '.' + parts[1]).encode('ascii')
        self.assertTrue(p256.verify(xy_from_public(relay.vapid_public()), signing, sig))
        return claims

    def test_push_host_ok(self):
        yes = (
            'https://web.push.apple.com/abc',
            'https://fcm.googleapis.com/fcm/send/x',
            'https://updates.push.services.mozilla.com/wpush/v2/x',
            'https://db5p.notify.windows.com/w/?token=x',
            'https://FCM.googleapis.com/fcm/send/x',
            'https://fcm.googleapis.com:443/fcm/send/x',
        )
        no = (
            'http://web.push.apple.com/x',
            'https://evilpush.apple.com/x',
            'https://push.apple.com.evil.com/x',
            'https://127.0.0.1/x',
            'https://user@fcm.googleapis.com/x',
            'https://fcm.googleapis.com:8443/x',
            'https://evil.fcm.googleapis.com/x',
            'https://notfcm.googleapis.com/x',
            'https://a.updates.push.services.mozilla.com/x',
            None,
        )
        for endpoint in yes:
            self.assertTrue(relay.push_host_ok(endpoint), endpoint)
        for endpoint in no:
            self.assertFalse(relay.push_host_ok(endpoint), endpoint)

    def test_subscribe_stores_rejects_and_replaces(self):
        ep = 'https://fcm.googleapis.com/fcm/send/x'
        self.assertEqual(relay.push_subscribe(sub(ep)), 1)
        saved = json.loads((self.data / 'push.json').read_text(encoding='utf-8'))
        self.assertEqual([s['endpoint'] for s in saved['subs']], [ep])
        self.assertEqual(saved['subs'][0]['keys'], {'p256dh': 'p256dh-key', 'auth': 'auth-key'})
        with self.assertRaises(ValueError) as caught:
            relay.push_subscribe({'endpoint': 'https://example.com/x', 'keys': {'p256dh': 'p', 'auth': 'a'}})
        self.assertEqual(str(caught.exception), 'That push address is not a known push service.')
        self.assertNotIn('example.com', (self.data / 'push.json').read_text(encoding='utf-8'))
        self.assertEqual(relay.push_subscribe(sub(ep, p256dh='p' * 250, auth='second')), 1)
        only = relay.push_subs()
        self.assertEqual(len(only), 1)
        self.assertEqual(only[0]['keys']['p256dh'], 'p' * 200)
        self.assertEqual(only[0]['keys']['auth'], 'second')
        other = 'https://updates.push.services.mozilla.com/wpush/v2/x'
        self.assertEqual(relay.push_subscribe(sub(other)), 2)
        self.assertEqual(relay.push_unsubscribe(ep), 1)
        self.assertEqual([s['endpoint'] for s in relay.push_subs()], [other])
        for i in range(20):
            relay.push_subscribe(sub(f'https://web.push.apple.com/dev/{i}'))
        kept = [s['endpoint'] for s in relay.push_subs()]
        self.assertEqual(len(kept), 20)
        self.assertNotIn(other, kept)
        self.assertIn('https://web.push.apple.com/dev/19', kept)
        (self.data / 'push.json').write_text('nope', encoding='utf-8')
        self.assertEqual(relay.push_subs(), [])

    def test_default_push_contact(self):
        self.assertEqual(relay.DEFAULT_CONFIG['push_contact'], 'mailto:vanguard@example.com')
        self.assertFalse((self.data / 'config.json').exists())
        self.assertEqual(relay.config()['push_contact'], 'mailto:vanguard@example.com')
        token = relay.vapid_jwt('https://web.push.apple.com/abc')
        claims = json.loads(b64u_decode(token.split('.')[1]))
        self.assertEqual(claims['sub'], 'mailto:vanguard@example.com')
        # Setting absent from config: the fallback in vapid_jwt applies.
        with patch.object(relay, 'config', lambda: {}):
            token = relay.vapid_jwt('https://web.push.apple.com/abc')
        claims = json.loads(b64u_decode(token.split('.')[1]))
        self.assertEqual(claims['sub'], 'mailto:vanguard@example.com')

    def test_vapid_jwt_and_key_is_reused(self):
        endpoint = 'https://web.push.apple.com/abc/def'
        lo = time.time()
        token = relay.vapid_jwt(endpoint)
        hi = time.time()
        self.assert_jwt(token, 'https://web.push.apple.com', lo, hi)
        first = relay.vapid_keys()
        second = relay.vapid_keys()
        self.assertEqual(first['d'], second['d'])
        self.assertEqual(relay.vapid_public(), first['public'])
        stored = json.loads((self.data / 'vapid.json').read_text(encoding='utf-8'))
        self.assertEqual(len(stored['private']), 64)
        self.assertEqual(stored['public'], first['public'])
        self.assertNotIn(stored['private'], token)
        self.assertNotIn(stored['private'], first['public'])
        (self.data / 'vapid.json').write_text('{', encoding='utf-8')
        fresh = relay.vapid_keys()
        self.assertNotEqual(fresh['d'], first['d'])
        self.assertEqual(relay.vapid_keys()['d'], fresh['d'])
        relay.write_json(self.data / 'config.json', {'push_contact': 'mailto:alerts@example.invalid'})
        lo = time.time()
        custom = relay.vapid_jwt(endpoint)
        hi = time.time()
        self.assert_jwt(custom, 'https://web.push.apple.com', lo, hi, 'mailto:alerts@example.invalid')

    def test_send_posts_empty_body_with_vapid_headers(self):
        self.allow_local()
        endpoint = self.url('/push/abc')
        relay.push_subscribe(sub(endpoint))
        lo = time.time()
        self.assertTrue(relay.push('Hello', 'one line', goal='goal-1', project='proj-1', wait=True))
        hi = time.time()
        self.assertEqual(len(self.server.hits), 1)
        hit = self.server.hits[0]
        self.assertEqual(hit['method'], 'POST')
        self.assertEqual(hit['path'], '/push/abc')
        self.assertEqual(hit['body_len'], 0)
        self.assertEqual(hit['headers']['ttl'], '86400')
        self.assertEqual(hit['headers']['urgency'], 'high')
        self.assertEqual(hit['headers']['content-length'], '0')
        auth = hit['headers']['authorization']
        self.assertTrue(auth.startswith('vapid t='), auth)
        token, key = auth[len('vapid t='):].split(', k=')
        self.assertEqual(key, relay.vapid_public())
        self.assert_jwt(token, self.base, lo, hi)
        latest = relay.push_latest()
        self.assertEqual(latest['title'], 'Hello')
        self.assertEqual(latest['body'], 'one line')
        self.assertEqual(latest['goal'], 'goal-1')
        self.assertEqual(latest['project'], 'proj-1')
        self.assertGreaterEqual(latest['ts'], lo)
        self.assertLessEqual(latest['ts'], hi)

    def test_refused_host_does_not_send(self):
        def explode(*args, **kwargs):
            raise AssertionError('urlopen was called')
        with patch.object(relay, 'urlopen', explode):
            self.assertEqual(relay.push_send('https://example.com/x'), 'refused')
            self.assertEqual(relay.push_send(self.url('/nope')), 'refused')
        self.assertEqual(self.server.hits, [])

    def test_410_and_404_remove_the_subscription_without_retry(self):
        self.allow_local()
        ok = self.url('/ok')
        gone = self.url('/gone')
        missing = self.url('/missing')
        self.server.codes['/gone'] = 410
        self.server.codes['/missing'] = 404
        relay.push_subscribe(sub(ok))
        relay.push_subscribe(sub(gone))
        relay.push_subscribe(sub(missing))
        self.assertTrue(relay.push('Drop dead endpoints', 'kept the live one', goal='g-gone', wait=True))
        self.assertEqual(sorted(hit['path'] for hit in self.server.hits), ['/gone', '/missing', '/ok'])
        self.assertEqual([s['endpoint'] for s in relay.push_subs()], [ok])

    def test_other_failure_retries_once_and_does_not_log_secrets(self):
        self.allow_local()
        endpoint = self.url('/retry')
        relay.push_subscribe(sub(endpoint, p256dh='secret-p256dh', auth='secret-auth'))
        self.server.status = 500
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(relay.push_send(endpoint), 'failed')
        self.assertEqual(len(self.server.hits), 2)
        self.assertEqual(len(relay.push_subs()), 1)
        lines = [line for line in buf.getvalue().splitlines() if line]
        self.assertEqual(len(lines), 2)
        self.assertIn('retrying', lines[0])
        self.assertIn('giving up', lines[1])
        self.assertIn('127.0.0.1', lines[0])
        text = buf.getvalue()
        private = json.loads((self.data / 'vapid.json').read_text(encoding='utf-8'))['private']
        self.assertNotIn(private, text)
        self.assertNotIn('vapid', text)
        self.assertNotIn('secret-p256dh', text)
        self.assertNotIn('secret-auth', text)
        self.assertNotIn('eyJ', text)

    def test_refused_alert_keeps_the_reason_in_the_log(self):
        self.allow_local()
        endpoint = self.url('/reason')
        body = b'{"reason":"VapidPkHashMismatch"}' + b' ' * 10 + b'X' * 400
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            raise relay.HTTPError(endpoint, 403, 'Forbidden', {}, io.BytesIO(body))

        buf = io.StringIO()
        with patch.object(relay, 'urlopen', fake_urlopen), redirect_stdout(buf):
            self.assertEqual(relay.push_send(endpoint), 'failed')
        self.assertEqual(len(calls), 2)
        text = buf.getvalue()
        self.assertIn('VapidPkHashMismatch', text)
        self.assertNotIn('X' * 301, text)
        self.assertNotIn('Authorization', text)
        self.assertNotIn('vapid t=', text)
        self.assertNotIn('eyJ', text)
        private = json.loads((self.data / 'vapid.json').read_text(encoding='utf-8'))['private']
        self.assertNotIn(private, text)

    def test_rate_limit_still_updates_latest(self):
        self.allow_local()
        endpoint = self.url('/rate')
        relay.push_subscribe(sub(endpoint))
        self.assertTrue(relay.push('T' * 150, 'B' * 400, goal='g', project='p', wait=True))
        latest = relay.push_latest()
        self.assertEqual(latest['title'], 'T' * 120)
        self.assertEqual(latest['body'], 'B' * 300)
        self.assertFalse(relay.push('Second', 'new body', goal='g', project='other', wait=True))
        self.assertEqual(len(self.server.hits), 1)
        self.assertEqual(relay.push_latest()['title'], 'Second')
        self.assertEqual(relay.push_latest()['body'], 'new body')
        self.assertTrue(relay.push('Other goal', 'goes out', goal='g2', wait=True))
        self.assertEqual(len(self.server.hits), 2)
        relay._PUSH_SENT['g'] = time.time() - 61
        self.assertTrue(relay.push('After a minute', 'again', goal='g', wait=True))
        self.assertEqual(len(self.server.hits), 3)

    def test_no_subscriptions_updates_latest_and_does_not_send(self):
        self.assertIsNone(relay.push_latest())
        self.assertFalse(relay.push('Quiet', 'nobody subscribed', goal='none', project=None, wait=True))
        latest = relay.push_latest()
        self.assertEqual(latest['title'], 'Quiet')
        self.assertEqual(latest['body'], 'nobody subscribed')
        self.assertEqual(latest['goal'], 'none')
        self.assertIsNone(latest['project'])
        self.assertIn('none', relay._PUSH_SENT)
        self.assertEqual(self.server.hits, [])
        self.allow_local()
        relay.push_subscribe(sub(self.url('/late')))
        self.assertFalse(relay.push('Still quiet', 'window open', goal='none', wait=True))
        self.assertEqual(self.server.hits, [])
        self.assertEqual(relay.push_latest()['title'], 'Still quiet')

    def test_wait_false_sends_on_a_background_thread(self):
        self.allow_local()
        relay.push_subscribe(sub(self.url('/bg')))
        self.server.hold.clear()
        self.server.entered.clear()
        started = time.time()
        self.assertTrue(relay.push('Background', 'thread', goal='bg', wait=False))
        self.assertLess(time.time() - started, 2)
        self.assertTrue(self.server.entered.wait(5))
        self.server.hold.set()
        deadline = time.time() + 5
        while time.time() < deadline:
            extra = [t for t in threading.enumerate()
                     if t.is_alive() and t is not threading.current_thread() and t is not self.thread]
            if not extra:
                break
            time.sleep(0.01)
        else:
            self.fail('background push did not finish')
        self.assertEqual(len(self.server.hits), 1)
        self.assertEqual(self.server.hits[0]['body_len'], 0)


if __name__ == '__main__':
    unittest.main()
