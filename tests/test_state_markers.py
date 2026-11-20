"""Change markers in /api/state: chat_n for the chat and 'updated' on each goal and project summary."""
import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolate  # noqa: F401
import relay


class StateMarkers(unittest.TestCase):
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

    def state(self):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        c.request('GET', '/api/state')
        r = c.getresponse(); body = r.read(); c.close()
        self.assertEqual(r.status, 200)
        return json.loads(body)

    def goal(self):
        gid = 'g0123abcd'
        g = dict(id=gid, title='Markers', objective='x', status='running', repo=str(self.data), lead='opus', team=['opus'],
                 turns=[], handoffs=0, cap=5, mode='auto', created=time.time(), notes=[])
        relay.save(g)
        return gid

    def summary_of(self, gid):
        return next(x for x in self.state()['goals'] if x['id'] == gid)

    def test_existing_fields_kept(self):
        s = self.state()
        for k in ('version', 'chat_n', 'stale', 'restarting', 'projects', 'goals', 'agents', 'billing', 'repos', 'defaults', 'now'):
            self.assertIn(k, s)
        self.assertEqual(s['version'], relay.LOADED_VERSION)

    def test_chat_n_changes_on_message_busy_and_live(self):
        n0 = self.state()['chat_n']
        relay.chat_post('you', 'hello')
        n1 = self.state()['chat_n']
        self.assertNotEqual(n1, n0)
        relay.chat_post('vanguard', 'hi')
        n2 = self.state()['chat_n']
        self.assertNotEqual(n2, n1)
        c = relay.chat_load(); c['busy'] = True; relay.write_json(self.data / 'chat.json', c)
        n3 = self.state()['chat_n']
        self.assertNotEqual(n3, n2)
        (self.data / 'chat-live.txt').write_text('thinking…', encoding='utf-8')
        n4 = self.state()['chat_n']
        self.assertNotEqual(n4, n3)
        relay._clear_chat_busy()
        self.assertNotEqual(self.state()['chat_n'], n4)

    def test_chat_n_steady_without_changes(self):
        relay.chat_post('you', 'hello')
        self.assertEqual(self.state()['chat_n'], self.state()['chat_n'])

    def test_goal_updated_changes_with_status_turns_and_live(self):
        gid = self.goal()
        u0 = self.summary_of(gid)['updated']
        self.assertIsNotNone(u0)
        g = relay.load(gid); g['status'] = 'needs_you'; relay.save(g)
        u1 = self.summary_of(gid)['updated']
        self.assertGreater(u1, u0)
        g = relay.load(gid); g['status'] = 'running'
        g['turns'].append(dict(n=1, agent='opus', status='running', started=time.time(), log='turn-1.log'))
        relay.save(g)
        u2 = self.summary_of(gid)['updated']
        self.assertGreater(u2, u1)
        self.assertEqual(self.summary_of(gid)['updated'], u2)  # nothing changed, so the marker holds
        log = relay.goal_path(gid) / 'turn-1.log'
        time.sleep(0.05); log.write_text('working on it', encoding='utf-8')
        u3 = self.summary_of(gid)['updated']
        self.assertGreater(u3, u2)

    def test_back_to_back_saves_still_differ(self):
        gid = self.goal()
        stamps = []
        for i in range(5):
            g = relay.load(gid); g['handoffs'] = i; relay.save(g); stamps.append(relay.load(gid)['updated'])
        self.assertEqual(len(set(stamps)), 5)
        self.assertEqual(stamps, sorted(stamps))

    def test_project_updated_changes(self):
        p = dict(id='p0123abcd', title='Proj', status='running', repo=str(self.data), base_branch='main', branch='relay/project-p0123abcd',
                 lead='opus', created=time.time(), pieces=[dict(n=1, title='One', what='do it', status='pending', depends_on=[])],
                 objective='x', test_cmd='true', mode='auto')
        relay.save_project(p)
        u0 = next(x for x in self.state()['projects'] if x['id'] == p['id'])['updated']
        p = relay.load_project(p['id']); p['pieces'][0]['status'] = 'running'; relay.save_project(p)
        u1 = next(x for x in self.state()['projects'] if x['id'] == p['id'])['updated']
        self.assertGreater(u1, u0)


if __name__ == '__main__':
    unittest.main()
