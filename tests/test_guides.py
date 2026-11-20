"""Repo guide files: name check, 6000-character cut, missing file, helper lines, and the API."""
import http.client, json, shutil, threading, unittest

import _isolate  # noqa: F401
import relay

HEADERS = {'X-Relay': '1', 'Sec-Fetch-Site': 'same-origin', 'Content-Type': 'application/json'}


class Guides(unittest.TestCase):
    def setUp(self):
        self.addCleanup(shutil.rmtree, relay.guides_dir(), True)
        shutil.rmtree(relay.guides_dir(), True)

    def test_name_check_rejects_paths(self):
        for bad in ['', '.', '..', '...', 'a/b', 'a\\b', '../x', 'x/..', '..\\x', 'a..b', ' x', None, 5]:
            self.assertFalse(relay.guide_name_ok(bad), repr(bad))
        for good in ['myrepo', 'my-repo_2', 'repo.v2', '.hidden']:
            self.assertTrue(relay.guide_name_ok(good), good)
        with self.assertRaises(ValueError): relay.save_guide('../evil', 'x')

    def test_long_guide_is_cut_and_says_so(self):
        relay.save_guide('big', 'x' * 7000)
        got = relay.guide_for('C:/some/where/big')
        self.assertTrue(got.startswith('x' * 6000))
        self.assertNotIn('x' * 6001, got)
        self.assertIn('cut', got.lower())

    def test_short_guide_unchanged_and_missing_is_empty(self):
        relay.save_guide('small', 'hello')
        self.assertEqual(relay.guide_for('small'), 'hello')
        self.assertEqual(relay.guide_for('nothing-here'), '')
        self.assertEqual(relay.guide_for('..'), '')
        self.assertEqual(relay.guide_for(''), '')

    def test_helper_lines(self):
        t = 'intro\nTEST COMMAND: python -m unittest\nBASE FOLDER:  C:\\code\\base \nNAMES: foo, bar;baz\n'
        self.assertEqual(relay.guide_test_command(t), 'python -m unittest')
        self.assertEqual(relay.guide_base_folder(t), 'C:\\code\\base')
        self.assertEqual(relay.guide_names(t), ['foo', 'bar', 'baz'])
        self.assertEqual(relay.guide_test_command('nothing'), '')
        self.assertEqual(relay.guide_names(''), [])


class GuideApi(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(relay.guides_dir(), True); self.addCleanup(shutil.rmtree, relay.guides_dir(), True)
        server = relay.ThreadingHTTPServer(('127.0.0.1', 0), relay.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(thread.join, 3); self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def call(self, method, path, body=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        c.request(method, path, json.dumps(body) if body is not None else None, HEADERS)
        r = c.getresponse(); data = json.loads(r.read() or b'{}'); c.close()
        return r.status, data

    def test_roundtrip(self):
        self.assertEqual(self.call('GET', '/api/guides'), (200, {'guides': []}))
        self.assertEqual(self.call('GET', '/api/guides/repo1')[0], 404)
        self.assertEqual(self.call('POST', '/api/guides/repo1', {'text': 'TEST COMMAND: x'})[0], 200)
        self.assertEqual(self.call('GET', '/api/guides'), (200, {'guides': ['repo1']}))
        self.assertEqual(self.call('GET', '/api/guides/repo1')[1]['text'], 'TEST COMMAND: x')

    def test_bad_names_and_text_refused(self):
        for bad in ['..', '%2e%2e', 'a%2fb', 'a%5Cb', '...']:
            s, _ = self.call('POST', '/api/guides/' + bad, {'text': 'x'})
            self.assertEqual(s, 400, bad)
            self.assertEqual(self.call('GET', '/api/guides/' + bad)[0], 400, bad)
        self.assertEqual(self.call('POST', '/api/guides/ok', {'text': 5})[0], 400)
        self.assertEqual(self.call('GET', '/api/guides'), (200, {'guides': []}))


if __name__ == '__main__':
    unittest.main()
