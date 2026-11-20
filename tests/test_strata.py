import io, json, os, sys, tempfile, threading, unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import _isolate
import relay, strata_run

KEY = 'sk-test-secret-123'
MODEL = strata_run.MODEL


class Fake(BaseHTTPRequestHandler):
    replies, seen = [], []
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        Fake.seen.append((self.headers.get('Authorization'), body))
        text = Fake.replies.pop(0)
        out = json.dumps({'model': MODEL, 'choices': [{'message': {'content': text}}]}).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(out))); self.end_headers(); self.wfile.write(out)


def edits(*ops, summary='done'):
    return json.dumps({'edits': list(ops), 'summary': summary})


GOOD_FN = 'def add(a, b):\n    return a + b\n'
TEST = ('import unittest\nfrom m import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n'
        'if __name__ == "__main__":\n    unittest.main()\n')


class StrataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(('127.0.0.1', 0), Fake); cls.url = f'http://127.0.0.1:{cls.srv.server_port}/v1'
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
    @classmethod
    def tearDownClass(cls): cls.srv.shutdown()

    def setUp(self):
        Fake.replies, Fake.seen = [], []; os.environ['STRATA_API_KEY'] = KEY
        self.work = Path(tempfile.mkdtemp()); self.brief = self.work / 'brief.md'
        self.brief.write_text('TASK\nAdd add(a, b) in m.py and a test in test_m.py\n\nFILES\n- m.py\n- test_m.py\n\n'
                              f'TESTS: `"{sys.executable}" test_m.py`\n\nWHEN YOU STOP...\n', encoding='utf-8')

    def go(self, endpoint=None):
        out = io.StringIO()
        with redirect_stdout(out):
            try: code = strata_run.main(['--prompt-file', str(self.brief), '--work', str(self.work), '--endpoint', endpoint or self.url])
            except SystemExit as e: code = e.code
        lines = [l for l in out.getvalue().splitlines() if l.startswith('{"type"')]
        return code, (json.loads(lines[-1])['result'] if lines else '') + out.getvalue()

    def test_reads_stay_inside_working_folder(self):
        outside = Path(tempfile.mkdtemp()); (outside / 'secret.txt').write_text('TOPSECRET', encoding='utf-8')
        (self.work / 'ok.txt').write_text('fine-content', encoding='utf-8')
        tests = [str(Path('..') / outside.name / 'secret.txt'), str(outside / 'secret.txt')]
        link = self.work / 'link'
        try: os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError): pass
        else: tests.append('link/secret.txt')
        for rel in tests:
            with self.subTest(rel=rel):
                block = strata_run.files_block(self.work, [rel])
                self.assertIn('(left out: outside the working folder)', block)
                self.assertNotIn('TOPSECRET', block)
        block = strata_run.files_block(self.work, ['ok.txt'])
        self.assertIn('fine-content', block); self.assertNotIn('left out', block)

    def test_config_is_tier_zero_guarded_local(self):
        cfg = relay.DEFAULT_CONFIG; a = cfg['agents']['strata']
        self.assertEqual((a['tier'], a['provider']), (0, 'strata'))
        b = cfg['providers']['strata']['billing']
        self.assertTrue(b['guarded']); self.assertEqual(b['note'], 'Local model, no account')
        self.assertEqual(relay.run_billing_check('strata', cfg['providers']['strata'])['level'], 'guarded')

    def test_short_brief_has_no_history(self):
        goal = dict(objective='Big goal. ' * 300, test_cmd='python -m unittest', lead='sonnet', team=['sonnet', 'strata'],
                    turns=[dict(n=1, agent='sonnet', status='ok', files=['old/x.py'], final='SECRET HISTORY ' * 50)], notes=[], branch='b')
        text = relay.short_brief(goal, 'Write add() in m.py with a test in test_m.py')
        self.assertIn('- old/x.py', text); self.assertIn('- m.py', text); self.assertIn('- test_m.py', text)
        self.assertIn('RELAY:', text); self.assertIn('TESTS: `python -m unittest`', text)
        self.assertNotIn('SECRET HISTORY', text); self.assertNotIn('TEAM', text); self.assertLess(len(text), 2500)
        self.assertEqual(relay.brief(goal, 'strata', 'x in m.py', False).count('GOAL'), 0)

    def test_edits_applied_tests_run_key_never_printed(self):
        Fake.replies = [edits({'op': 'create', 'path': 'm.py', 'content': GOOD_FN}, {'op': 'create', 'path': 'test_m.py', 'content': TEST})]
        code, out = self.go()
        self.assertEqual(code, 0); self.assertIn('RELAY: {"next": "planner"', out); self.assertNotIn(KEY, out)
        self.assertEqual((self.work / 'm.py').read_text(), GOOD_FN)
        self.assertEqual(Fake.seen[0][0], 'Bearer ' + KEY); self.assertLess(len(json.dumps(Fake.seen[0][1])), 9000)

    def test_failing_tests_get_a_fix_round(self):
        bad = 'def add(a, b):\n    return a - b\n'
        Fake.replies = [edits({'op': 'create', 'path': 'm.py', 'content': bad}, {'op': 'create', 'path': 'test_m.py', 'content': TEST}),
                        edits({'op': 'replace_symbol', 'path': 'm.py', 'symbol': 'add', 'new': GOOD_FN})]
        code, out = self.go()
        self.assertEqual(code, 0); self.assertEqual(len(Fake.seen), 2); self.assertIn('failed', json.dumps(Fake.seen[1][1]))
        self.assertIn('return a + b', (self.work / 'm.py').read_text())

    def test_only_listed_files_may_change(self):
        Fake.replies = [edits({'op': 'create', 'path': 'other.py', 'content': 'x=1\n'}, {'op': 'create', 'path': '../evil.py', 'content': 'x=1\n'})] * 3
        code, out = self.go()
        self.assertEqual(code, 1); self.assertFalse((self.work / 'other.py').exists()); self.assertFalse((self.work.parent / 'evil.py').exists())
        self.assertIn('RELAY: {"next": "planner"', out)

    def test_non_loopback_endpoint_refused_and_empty_files_refused(self):
        code, out = self.go('http://example.com/v1'); self.assertNotEqual(code, 0)
        self.brief.write_text('TASK\nx\n\nFILES\n(none named)\n', encoding='utf-8')
        code, out = self.go(); self.assertNotEqual(code, 0)

    def test_endpoint_down_reports_and_hands_back(self):
        code, out = self.go('http://127.0.0.1:1/v1')
        self.assertEqual(code, 1); self.assertIn('could not be used', out); self.assertIn('"next": "planner"', out); self.assertNotIn(KEY, out)


if __name__ == '__main__':
    unittest.main()
