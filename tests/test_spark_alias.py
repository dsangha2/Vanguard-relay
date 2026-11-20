"""The agent 'muse' is now 'spark'. Old stored data and requests that say 'muse' still work; the 'muse' provider is unchanged."""
import http.client, json, threading, unittest
from unittest import mock

import _isolate  # noqa: F401  (sets RELAY_DATA to a temp folder before relay is imported)
import relay

HEADERS = {'X-Relay': '1', 'Sec-Fetch-Site': 'same-origin', 'Content-Type': 'application/json'}
CONF = relay.DATA / 'config.json'
CHAT = relay.DATA / 'chat.json'


def old_goal(gid='gabcdef12'):
    return dict(id=gid, title='old', objective='x', repo='C:/nope', status='needs_you', reason='asked', lead='sonnet', team=['sonnet', 'muse'],
                mode='auto', handoffs=1, cap=10, test_cmd='', notes=[], sessions={'muse': 's1'}, reroutes=[], avoid={}, plan=[dict(piece='p', who='muse', why='')],
                turns=[dict(n=1, agent='sonnet', status='ok', started=1.0), dict(n=2, agent='muse', status='ok', started=__import__('time').time() - 60, ended=__import__('time').time())],
                question=dict(agent='muse', text='which?'), next=dict(agent='muse', note='', why='x'), suggestion=dict(action='pass', agent='muse'))


class SparkAlias(unittest.TestCase):
    def setUp(self):
        for f in (CONF, CHAT):
            before = f.read_bytes() if f.exists() else None
            self.addCleanup(lambda f=f, before=before: f.write_bytes(before) if before is not None else f.unlink(missing_ok=True))
            f.unlink(missing_ok=True)

    def test_default_agent_and_provider(self):
        cfg = relay.config()
        self.assertNotIn('muse', cfg['agents'])
        self.assertEqual(cfg['agents']['spark'], {'provider': 'muse', 'model': 'muse-spark-1.3', 'tier': 2})
        self.assertIn('muse', cfg['providers'])
        argv = cfg['providers']['muse']['argv']
        self.assertEqual(argv[argv.index('--provider') + 1], 'meta')

    def test_helper(self):
        self.assertEqual(relay.canon_agent('muse'), 'spark')
        self.assertEqual(relay.canon_agent('opus'), 'opus')
        self.assertEqual(relay.canon_agents(['muse', 'spark', 'grok']), ['spark', 'grok'])

    def test_config_agents_muse_merged_as_spark(self):
        CONF.write_text(json.dumps({'agents': {'muse': {'tier': 3}}, 'prefer_agents': ['muse'], 'coordinator': 'muse'}), encoding='utf-8')
        cfg = relay.config()
        self.assertEqual(cfg['agents']['spark'], {'provider': 'muse', 'model': 'muse-spark-1.3', 'tier': 3})
        self.assertNotIn('muse', cfg['agents'])
        self.assertEqual(relay.prefer_agents(), ['spark']); self.assertEqual(cfg['coordinator'], 'spark')

    def test_old_goal_loads_and_continues(self):
        d = relay.DATA / 'goals' / 'gabcdef12'; d.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(old_goal()); (d / 'goal.json').write_text(raw, encoding='utf-8')
        g = relay.load('gabcdef12')
        self.assertEqual(g['team'], ['sonnet', 'spark']); self.assertEqual(g['turns'][1]['agent'], 'spark')
        self.assertEqual(g['sessions'], {'spark': 's1'}); self.assertEqual(g['question']['agent'], 'spark')
        self.assertEqual(g['plan'][0]['who'], 'spark')
        self.assertEqual([x['id'] for x in relay.all_goals() if x['id'] == 'gabcdef12'], ['gabcdef12'])
        self.assertIn('spark', relay.usage_counts(relay.all_goals()))
        self.assertEqual((d / 'goal.json').read_text(encoding='utf-8'), raw)       # reading does not rewrite history
        with mock.patch.object(relay, 'blocked_reason', return_value=None), mock.patch.object(relay, 'limited_reason', return_value=None):
            out = relay.act('gabcdef12', dict(action='pass', agent='muse', note='go'))
        self.assertEqual(out['next']['agent'], 'spark'); self.assertEqual(out['team'], ['sonnet', 'spark'])
        self.assertEqual(out['status'], 'queued')

    def test_old_chat_state_maps(self):
        CHAT.write_text(json.dumps(dict(messages=[dict(role='coordinator', text='hi', agent='muse')], sessions={'muse': 'x'}, rules={'muse': 'h'}, busy=False)), encoding='utf-8')
        c = relay.chat_load()
        self.assertEqual(c['messages'][0]['agent'], 'spark'); self.assertEqual(c['sessions'], {'spark': 'x'}); self.assertEqual(c['rules'], {'spark': 'h'})

    def test_chat_prefer_and_api_prefer_store_spark(self):
        with mock.patch.object(relay, 'coordinator', return_value='haiku'), mock.patch.object(
                relay, 'chat_call', return_value=('ok\nACTIONS: [{"do": "prefer", "agents": ["muse"]}]', None, None)):
            relay.chat_run('use muse')
        self.assertEqual(json.loads(CONF.read_text(encoding='utf-8'))['prefer_agents'], ['spark'])
        server = relay.ThreadingHTTPServer(('127.0.0.1', 0), relay.Handler)   # port 0: never 8940
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
        conn.request('POST', '/api/prefer', body=json.dumps({'agents': ['grok', 'muse']}), headers=HEADERS)
        r = conn.getresponse(); self.assertEqual((r.status, json.loads(r.read())), (200, {'prefer_agents': ['grok', 'spark']}))
        conn.close()
        self.assertEqual(json.loads(CONF.read_text(encoding='utf-8'))['prefer_agents'], ['grok', 'spark'])

    def test_billing_and_limits_follow_the_muse_provider(self):
        self.assertEqual(relay.cfg_provider('muse'), None)
        self.assertEqual(relay.cfg_provider('spark'), 'muse')
        with mock.patch.dict(relay.LIMITED, {'muse': dict(until=__import__('time').time() + 3600, reason='limit')}, clear=False):
            self.assertTrue(relay.limited_reason('spark'))
        self.assertIn('muse', relay.billing_view()['providers'])


if __name__ == '__main__':
    unittest.main()
