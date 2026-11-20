"""Push HTTP routes and the alerts that call push beside notify."""
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

HEADERS = {'X-Relay': '1', 'Sec-Fetch-Site': 'same-origin', 'Content-Type': 'application/json'}
EP = 'https://fcm.googleapis.com/fcm/send/abc'
GID = 'g0aa11bb2'
PID = 'p0aa11bb2'


class PushRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        data = patch.object(relay, 'DATA', self.data)
        data.start()
        self.addCleanup(data.stop)
        relay._PUSH_SENT.clear()
        self.addCleanup(relay._PUSH_SENT.clear)
        server = relay.ThreadingHTTPServer(('127.0.0.1', 0), relay.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def raw(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        data = None if body is None else json.dumps(body).encode()
        c.request(method, path, data, {} if headers is None else headers)
        r = c.getresponse()
        raw, status, ctype = r.read(), r.status, r.getheader('Content-Type')
        c.close()
        return status, raw, ctype

    def call(self, method, path, body=None, headers=None):
        status, raw, ctype = self.raw(method, path, body, HEADERS if headers is None else headers)
        return status, json.loads(raw or b'{}'), ctype

    def test_post_without_page_headers_is_refused(self):
        status, got, _ = self.call('POST', '/api/push/subscribe', {'endpoint': EP}, {'Content-Type': 'application/json'})
        self.assertEqual(status, 403)
        self.assertEqual(got['error'], 'Changes must come from the Vanguard Relay page')
        self.assertEqual(relay.push_subs(), [])

    def test_foreign_host_is_refused(self):
        status, got, _ = self.call('POST', '/api/push/subscribe', {'endpoint': EP}, dict(HEADERS, Host='evil.example'))
        self.assertEqual(status, 403)
        self.assertEqual(relay.push_subs(), [])

    def test_unknown_push_service_is_refused_and_stores_nothing(self):
        status, got, _ = self.call('POST', '/api/push/subscribe', {'endpoint': 'https://example.com/x'})
        self.assertEqual(status, 400)
        self.assertEqual(got['error'], 'That push address is not a known push service.')
        self.assertEqual(relay.push_subs(), [])
        self.assertFalse((self.data / 'push.json').exists())

    def test_subscribe_and_unsubscribe(self):
        sub = {'endpoint': EP, 'keys': {'p256dh': 'pk', 'auth': 'ak'}}
        status, got, _ = self.call('POST', '/api/push/subscribe', sub)
        self.assertEqual((status, got), (200, {'ok': True, 'count': 1}))
        self.assertEqual([s['endpoint'] for s in relay.push_subs()], [EP])
        status, got, _ = self.call('POST', '/api/push/unsubscribe', {'endpoint': EP})
        self.assertEqual((status, got), (200, {'ok': True, 'count': 0}))
        self.assertEqual(relay.push_subs(), [])

    def test_key_returns_only_the_public_key(self):
        status, raw, _ = self.raw('GET', '/api/push/key')
        text = raw.decode()
        got = json.loads(text)
        private = json.loads((self.data / 'vapid.json').read_text(encoding='utf-8'))['private']
        self.assertEqual(status, 200)
        self.assertEqual(set(got), {'public'})
        self.assertEqual(got['public'], relay.vapid_public())
        self.assertNotIn(private, text)
        self.assertNotIn('private', text)
        self.assertNotIn('"d"', text)

    def test_latest_is_empty_then_shows_the_push(self):
        self.assertEqual(self.call('GET', '/api/push/latest')[:2], (200, {}))
        started = time.time()
        relay.push('Desk title', 'Desk body', goal=GID)
        status, got, _ = self.call('GET', '/api/push/latest')
        self.assertEqual(status, 200)
        self.assertEqual(got['title'], 'Desk title')
        self.assertEqual(got['body'], 'Desk body')
        self.assertEqual(got['goal'], GID)
        self.assertGreaterEqual(got['ts'], started)
        self.assertLessEqual(got['ts'], time.time() + 1)

    def test_push_test_with_no_subscription_writes_the_alert(self):
        status, got, _ = self.call('POST', '/api/push/test', {})
        self.assertEqual((status, got), (200, {'ok': True, 'sent': False}))
        latest = relay.push_latest()
        self.assertEqual(latest['title'], 'Phone alerts are on')
        self.assertEqual(latest['body'], 'This is a test from Vanguard')
        self.assertIsNone(latest['goal'])
        self.assertIsNone(latest['project'])
        self.assertIsInstance(latest['ts'], (int, float))

    def test_push_test_sends_inside_the_window_and_does_not_arm_it(self):
        self.assertEqual(self.call('POST', '/api/push/subscribe', {'endpoint': EP, 'keys': {'p256dh': 'pk', 'auth': 'ak'}})[0], 200)
        now = time.time()
        relay._PUSH_SENT['general'] = now
        relay._PUSH_SENT[GID] = now
        snap = dict(relay._PUSH_SENT)
        with patch.object(relay, 'push_send', return_value='failed') as send:
            status, got, _ = self.call('POST', '/api/push/test', {})
        self.assertEqual((status, got), (200, {'ok': True, 'sent': True}))
        send.assert_called_once_with(EP)
        self.assertEqual(relay._PUSH_SENT, snap)
        latest = relay.push_latest()
        self.assertEqual(latest['title'], 'Phone alerts are on')
        self.assertEqual(latest['body'], 'This is a test from Vanguard')
        self.assertIsNone(latest['goal'])
        self.assertIsNone(latest['project'])
        with patch.object(relay, 'push_send', return_value='ok') as send:
            self.assertTrue(relay.push('Later goal', 'still going', goal='g9999abcd', wait=True))
        send.assert_called_once_with(EP)

    def test_sw_js_is_served(self):
        status, raw, ctype = self.raw('GET', '/sw.js')
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith('application/javascript'))
        text = raw.decode()
        for bit in ('/api/push/latest', 'showNotification', 'Vanguard needs you', 'notificationclick', 'openWindow'):
            self.assertIn(bit, text)
        root = self.data / 'empty'
        root.mkdir()
        with patch.object(relay, 'HERE', root):
            status, _, ctype = self.raw('GET', '/sw.js')
        self.assertEqual(status, 404)
        self.assertIn('application/json', ctype)


class PushHooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data = patch.object(relay, 'DATA', Path(self.tmp.name))
        data.start()
        self.addCleanup(data.stop)
        relay._PUSH_SENT.clear()
        self.addCleanup(relay._PUSH_SENT.clear)
        self.pushes = []
        self.notes = []

        def fake_push(title, body='', goal=None, project=None, wait=False):
            self.pushes.append((title, body, goal, project))
            return True

        def fake_notify(title, body=''):
            self.notes.append((title, body))

        push = patch.object(relay, 'push', fake_push)
        notify = patch.object(relay, 'notify', fake_notify)
        push.start(); notify.start()
        self.addCleanup(push.stop); self.addCleanup(notify.stop)

    def goal(self, **extra):
        g = dict(id=GID, title='G' * 70, status='running', test_cmd='',
                 next=dict(agent='opus', note='', why=''), handoffs=0, cap=6, turns=[], notes=[],
                 mode='auto', team=['opus'])
        g.update(extra)
        relay.save(g)
        return g

    def project(self, **extra):
        p = dict(id=PID, title='P' * 70, status='running', reason='', phase='', pieces=[],
                 objective='Build the thing', repo='C:/nowhere', base_branch='main',
                 branch='relay/project-' + PID, test_cmd='', auto_start=False)
        p.update(extra)
        relay.save_project(p)
        return p

    def test_needs_you_pushes_the_goal(self):
        title = 'N' * 70
        self.goal(title=title)
        relay.needs_you(GID, 'Look at this')
        expect = ('Vanguard needs you: ' + title[:60], 'Look at this')
        self.assertEqual(self.notes, [expect])
        self.assertEqual(self.pushes, [(*expect, GID, None)])

    def test_goal_ready_pushes_the_goal(self):
        title = 'R' * 70
        why = 'Lead says the goal is met and tests are no worse than before'
        self.goal(title=title)

        def turn(goal, agent, note, why_):
            return dict(n=1, agent=agent, status='ok', note='')

        with patch.object(relay, 'run_turn', turn), patch.object(relay, 'decide', return_value=('ready', 'opus', '', why)):
            relay.run_goal(GID)
        expect = ('Vanguard finished: ' + title[:60], why)
        self.assertEqual(self.notes, [expect])
        self.assertEqual(self.pushes, [(*expect, GID, None)])

    def test_project_needs_you_pushes_the_project(self):
        title = 'Q' * 70
        self.project(title=title)
        relay.project_needs_you(PID, 'A piece is stuck')
        expect = ('Vanguard needs you: ' + title[:60], 'A piece is stuck')
        self.assertEqual(self.notes, [expect])
        self.assertEqual(self.pushes, [(*expect, None, PID)])

    def test_project_ready_pushes_the_project(self):
        title = 'F' * 70
        self.project(title=title)
        relay.finish_project(PID)
        body = 'Every piece is finished. No test command set. Ready to merge into main.'
        expect = ('Vanguard finished a project: ' + title[:60], body)
        self.assertEqual(self.notes, [expect])
        self.assertEqual(self.pushes, [(*expect, None, PID)])

    def test_plan_ready_pushes_without_adding_notify(self):
        title = 'L' * 70
        self.project(title=title, status='planning')
        reply = json.dumps({'pieces': [
            {'title': 'One', 'what': 'Do the first piece of the work.', 'depends_on': []},
            {'title': 'Two', 'what': 'Do the second piece of the work.', 'depends_on': [1]},
            {'title': 'Three', 'what': 'Do the third piece of the work.', 'depends_on': [2]},
        ]})
        with patch.object(relay, 'project_lead', return_value='opus'), patch.object(relay, 'chat_call', return_value=(reply, None, None)):
            relay.plan_project(PID)
        body = '3 pieces planned. Check the plan, then press Start.'
        self.assertEqual(self.notes, [])
        self.assertEqual(self.pushes, [('Vanguard plan is ready: ' + title[:60], body, None, PID)])

    def test_usage_limit_switch_pushes_the_goal(self):
        until = 1900000000
        self.goal()

        def turn(goal, agent, note, why_):
            relay.update(goal['id'], status='stopped')
            return dict(n=1, agent=agent, status='ok', note='')

        with patch.object(relay, 'run_turn', turn), \
             patch.object(relay, 'decide', return_value=('run', 'opus', 'keep going', 'still on it')), \
             patch.object(relay, 'limit_hold', return_value=dict(until=until, reason='weekly limit')), \
             patch.object(relay, 'pick_replacement', return_value='grok'):
            relay.run_goal(GID)
        when = relay.fmt_reset(until)
        why = f"opus is at its claude usage limit (resets {when}); continuing with grok"
        self.assertEqual(self.notes, [])
        self.assertEqual(self.pushes, [('Usage limit reached, switched to grok', why, GID, None)])
        self.assertEqual(relay.load(GID)['next']['agent'], 'grok')


if __name__ == '__main__':
    unittest.main()
