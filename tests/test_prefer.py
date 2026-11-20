"""The prefer list: saved in config.json, read fresh, returned by /api/state, unknown ids rejected."""
import http.client, json, threading, unittest
from unittest import mock

import _isolate  # noqa: F401  (sets RELAY_DATA to a temp folder before relay is imported)
import relay

HEADERS = {'X-Relay': '1', 'Sec-Fetch-Site': 'same-origin', 'Content-Type': 'application/json'}


CONF = relay.DATA / 'config.json'


class Prefer(unittest.TestCase):
    def setUp(self):
        # the data folder is shared with other test modules (test_relay writes fake agents here): restore it afterwards
        before = CONF.read_bytes() if CONF.exists() else None
        self.addCleanup(lambda: CONF.write_bytes(before) if before is not None else CONF.unlink(missing_ok=True))
        if before is not None: CONF.unlink()
        server = relay.ThreadingHTTPServer(('127.0.0.1', 0), relay.Handler)   # port 0: never 8940
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(thread.join, 3); self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            conn.request(method, path, body=None if body is None else json.dumps(body), headers=HEADERS if method == 'POST' else {})
            r = conn.getresponse(); return r.status, json.loads(r.read())
        finally:
            conn.close()

    def test_default_is_empty(self):
        self.assertEqual(self.call('GET', '/api/state')[1]['defaults']['prefer_agents'], [])

    def test_list_is_saved_and_returned(self):
        ids = list(relay.config()['agents'])[:2][::-1]
        status, body = self.call('POST', '/api/prefer', {'agents': ids})
        self.assertEqual((status, body), (200, {'prefer_agents': ids}))
        self.assertEqual(self.call('GET', '/api/state')[1]['defaults']['prefer_agents'], ids)
        self.assertEqual(json.loads((relay.DATA / 'config.json').read_text(encoding='utf-8'))['prefer_agents'], ids)
        self.assertEqual(relay.prefer_agents(), ids)

    def test_change_on_disk_applies_without_restart(self):
        agent = next(iter(relay.config()['agents']))
        (relay.DATA / 'config.json').write_text(json.dumps({'prefer_agents': [agent]}), encoding='utf-8')
        self.assertEqual(relay.prefer_agents(), [agent])
        self.call('POST', '/api/prefer', {'agents': []})
        self.assertEqual(relay.prefer_agents(), [])

    def test_saving_keeps_other_settings(self):
        (relay.DATA / 'config.json').write_text(json.dumps({'handoff_cap': 9}), encoding='utf-8')
        self.call('POST', '/api/prefer', {'agents': []})
        self.assertEqual(relay.config()['handoff_cap'], 9)

    def test_unknown_ids_are_rejected_and_nothing_is_saved(self):
        good = next(iter(relay.config()['agents']))
        status, body = self.call('POST', '/api/prefer', {'agents': [good, 'nobody-here']})
        self.assertNotEqual(status, 200)
        self.assertIn('nobody-here', body['error'])
        self.assertTrue(body['error'].startswith('Unknown agent'))
        self.assertEqual(relay.prefer_agents(), [])

    def test_wrong_shape_is_rejected(self):
        for bad in (None, 'haiku', [1], {'a': 1}):
            with self.subTest(bad=bad):
                self.assertNotEqual(self.call('POST', '/api/prefer', {'agents': bad})[0], 200)


class PickTeam(unittest.TestCase):
    """pick_team with fake agents: the real ids from config, availability faked."""
    def view(self, **flags):
        out = []
        for i, (name, a) in enumerate(relay.config()['agents'].items()):
            f = flags.get(name, {})
            out.append(dict(id=name, provider=a['provider'], model=a['model'], tier=a.get('tier', 2), installed=True,
                            blocked=bool(f.get('blocked')), blocked_reason=None, limited_until=None, limited_reason=None,
                            available=not f.get('blocked') and not f.get('limited'), exe='x'))
        return out

    def run_pick(self, prefer, **flags):
        view = self.view(**flags)
        with mock.patch.object(relay, 'agents_view', lambda: view), mock.patch.object(relay, 'coordinator', lambda: None),              mock.patch.object(relay, 'prefer_agents', lambda: prefer):
            return relay.pick_team('fix the bug in app.py', '.')

    def lowest(self):
        return min(relay.config()['agents'], key=lambda n: relay.config()['agents'][n].get('tier', 2))

    def test_auto_team_includes_preferred_and_says_so(self):
        base = self.run_pick([])
        names = [n for n in relay.config()['agents'] if n not in base['team']]
        if not names: self.skipTest('every agent is already on the default team')
        got = self.run_pick([names[0]])
        self.assertIn(names[0], got['team'])
        self.assertEqual(got['lead'], base['lead'])
        self.assertIn('preferred', got['why']); self.assertIn(names[0], got['why'])

    def test_blocked_preferred_is_left_out(self):
        base = self.run_pick([])
        names = [n for n in relay.config()['agents'] if n not in base['team']]
        if not names: self.skipTest('every agent is already on the default team')
        got = self.run_pick([names[0]], **{names[0]: {'blocked': True}})
        self.assertNotIn(names[0], got['team'])
        self.assertNotIn('preferred', got['why'])

    def test_hand_picked_team_is_unchanged(self):
        agents = list(relay.config()['agents'])
        view = self.view()
        with mock.patch.object(relay, 'agents_view', lambda: view), mock.patch.object(relay, 'prefer_agents', lambda: agents),              mock.patch.object(relay, 'pick_team', side_effect=AssertionError('hand-picked teams must not be re-picked')):
            body = dict(objective='fix the bug', repo='.', lead=agents[0], team=[agents[0]])
            automatic = body.get('lead') == 'auto' or body.get('team') == 'auto' or not body.get('lead')
            self.assertFalse(automatic)   # create_goal only calls pick_team when automatic


class Delegation(unittest.TestCase):
    """The lead's prompt gets the 'Preferred worker' rule only when a usable preferred agent is on the team."""
    RULE = 'Preferred worker: '

    def setUp(self):
        agents = list(relay.config()['agents'])
        if len(agents) < 2: self.skipTest('needs two agents')
        self.lead, self.worker = agents[0], agents[1]
        for name in ('blocked_reason', 'limited_reason'):
            p = mock.patch.object(relay, name, lambda a: None); p.start(); self.addCleanup(p.stop)

    def goal(self, team):
        return dict(id='gtest', branch='relay/gtest', objective='fix the bug', team=team, lead=self.lead,
                    test_cmd='', baseline=None, turns=[], notes=[], attachments=[])

    def prompt(self, prefer, team, agent=None, short=False):
        provider = relay.config()['agents'][agent or self.lead]['provider']
        cfg = relay.config(); cfg['providers'].setdefault(provider, {})['short_brief'] = short
        with mock.patch.object(relay, 'prefer_agents', lambda: prefer), mock.patch.object(relay, 'config', lambda: cfg):
            return relay.brief(self.goal(team), agent or self.lead, 'do the piece', False)

    def test_rule_word_for_word_when_preferred_on_team(self):
        text = self.prompt([self.worker], [self.lead, self.worker])
        self.assertIn(f'Preferred worker: {self.worker}. Hand every piece of implementation to them first, in pieces small and concrete enough '
                      'for a cheap model (exact files, exact change, how to check). Keep for yourself only planning, review and what they have '
                      'failed at twice. Do not do the work yourself to save time.', text)
        self.assertIn('fails the same piece twice', text); self.assertIn('turn summary', text)
        self.assertIn('Review their actual changes', text)   # the rest of the lead rules stay

    def test_short_brief_lead_also_gets_rule(self):
        self.assertIn(self.RULE + self.worker, self.prompt([self.worker], [self.lead, self.worker], short=True))

    def test_no_rule_without_preferred(self):
        self.assertNotIn(self.RULE, self.prompt([], [self.lead, self.worker]))

    def test_no_rule_when_preferred_not_on_team(self):
        self.assertNotIn(self.RULE, self.prompt([self.worker], [self.lead]))

    def test_no_rule_when_preferred_is_only_the_lead(self):
        self.assertNotIn(self.RULE, self.prompt([self.lead], [self.lead, self.worker]))

    def test_no_rule_for_helper_prompt(self):
        self.assertNotIn(self.RULE, self.prompt([self.worker], [self.lead, self.worker], agent=self.worker))
        self.assertNotIn(self.RULE, self.prompt([self.worker], [self.lead, self.worker], agent=self.worker, short=True))

    def test_no_rule_when_preferred_is_limited(self):
        with mock.patch.object(relay, 'limited_reason', lambda a: 'limited' if a == self.worker else None):
            self.assertNotIn(self.RULE, self.prompt([self.worker], [self.lead, self.worker]))


class LimitedExpiry(unittest.TestCase):
    """A preferred agent marked limited (mark_limited) is skipped, and preferred again after expiry.

    Uses the real limit mark and fakes either the clock or the mark's expiry time.
    No sockets, no config.json rewrites.
    """

    def setUp(self):
        import time as _time
        self._now = _time.time()
        limits_file = relay.DATA / 'limits.json'
        before = limits_file.read_bytes() if limits_file.exists() else None
        self.addCleanup(lambda: limits_file.write_bytes(before) if before is not None else limits_file.unlink(missing_ok=True))
        relay.LIMITED.clear()
        self.addCleanup(relay.LIMITED.clear)

    def _available_world(self):
        """Patches that make every agent installed and verified, so only the limit mark decides."""
        p1 = mock.patch.object(relay, 'find_exe', lambda p: 'fake-exe'); p1.start(); self.addCleanup(p1.stop)
        p2 = mock.patch.object(relay, 'billing_status',
                               lambda provider, force=False: dict(installed=True, level='verified', how='', note='', reason=''))
        p2.start(); self.addCleanup(p2.stop)
        p3 = mock.patch.object(relay, 'strata_reachable', lambda: False); p3.start(); self.addCleanup(p3.stop)
        p4 = mock.patch.object(relay, 'coordinator', lambda: None); p4.start(); self.addCleanup(p4.stop)

    def _preferred_outside_base_team(self):
        """An agent on a provider nobody shares (limiting it touches nothing else), outside the base team."""
        counts = {}
        for a in relay.config()['agents'].values():
            counts[a['provider']] = counts.get(a['provider'], 0) + 1
        with mock.patch.object(relay, 'prefer_agents', lambda: []):
            base = relay.pick_team('fix the bug in app.py', '.')
        for name in relay.config()['agents']:
            if name not in base['team'] and counts[relay.config()['agents'][name]['provider']] == 1 and name != 'strata':
                return name, base
        self.skipTest('no uniquely-provided agent outside the base team')

    def test_pick_skips_limited_and_restores_after_expiry(self):
        self._available_world()
        name, _ = self._preferred_outside_base_team()
        provider = relay.config()['agents'][name]['provider']
        with mock.patch.object(relay, 'prefer_agents', lambda: [name]):
            relay.mark_limited(provider, self._now + 3600, 'test limit')
            got = relay.pick_team('fix the bug in app.py', '.')
            self.assertNotIn(name, got['team'])
            self.assertNotIn('preferred', got['why'])
            # Fake the clock past the reset: the same preferred agent is back on the team.
            with mock.patch.object(relay.time, 'time', return_value=self._now + 7200):
                back = relay.pick_team('fix the bug in app.py', '.')
            self.assertIn(name, back['team'])
            self.assertIn('preferred', back['why'])
            self.assertIn(name, back['why'])

    def test_rule_skips_limited_and_restores_after_expiry(self):
        lead, worker = 'sonnet', 'spark'  # distinct providers, so the mark touches only the worker
        provider = relay.config()['agents'][worker]['provider']
        self.assertNotEqual(provider, relay.config()['agents'][lead]['provider'])
        goal = dict(id='gtest', branch='relay/gtest', objective='fix the bug', team=[lead, worker], lead=lead,
                    test_cmd='', baseline=None, turns=[], notes=[], attachments=[])
        with mock.patch.object(relay, 'blocked_reason', lambda a: None), \
                mock.patch.object(relay, 'prefer_agents', lambda: [worker]):
            relay.mark_limited(provider, self._now + 3600, 'test limit')
            self.assertEqual(relay.preferred_workers(goal, lead), [])
            self.assertEqual(relay.delegation_rule(goal, lead), '')
            # Fake the mark's expiry: the rule prefers the worker again.
            relay.LIMITED[provider]['until'] = self._now - 1
            self.assertEqual(relay.preferred_workers(goal, lead), [worker])
            self.assertIn('Preferred worker: ' + worker, relay.delegation_rule(goal, lead))

    def test_limited_provider_is_never_picked_as_replacement(self):
        """The existing hand-off (re-route with the same note) avoids the limited provider: no retry loop."""
        self._available_world()
        name, _ = self._preferred_outside_base_team()
        provider = relay.config()['agents'][name]['provider']
        with mock.patch.object(relay, 'prefer_agents', lambda: [name]):
            relay.mark_limited(provider, self._now + 3600, 'test limit')
            team = [name] + [n for n in relay.config()['agents'] if n != name][:3]
            goal = dict(id='gtest', branch='relay/gtest', objective='fix the bug', team=team, lead='x', avoid={})
            self.assertIsNotNone(relay.limit_hold(goal, name))
            view = relay.agents_view()
            self.assertNotIn(name, {a['id'] for a in view if a['available']})
            # Somebody else fails: the hand-off must not send the work to the limited agent.
            other = next(n for n in team if n != name)
            rep = relay.pick_replacement(team, other, view=view)
            self.assertIsNotNone(rep)
            self.assertNotEqual(rep, name)
            self.assertNotEqual(relay.config()['agents'][rep]['provider'], provider)
            # After expiry the hold lifts and the agent is usable again.
            with mock.patch.object(relay.time, 'time', return_value=self._now + 7200):
                self.assertIsNone(relay.limit_hold(goal, name))
                self.assertIn(name, {a['id'] for a in relay.agents_view() if a['available']})


if __name__ == '__main__':
    unittest.main()


CHAT = relay.DATA / 'chat.json'


class ChatPrefer(unittest.TestCase):
    """The coordinator chat sees the prefer list in LIVE STATUS and sets or clears it with a prefer action."""

    def setUp(self):
        for f in (CONF, CHAT):     # shared data folder: put both files back exactly as they were
            before = f.read_bytes() if f.exists() else None
            self.addCleanup(lambda f=f, before=before: f.write_bytes(before) if before is not None else f.unlink(missing_ok=True))
        self.agent = next(iter(relay.config()['agents']))
        relay.save_prefer([])

    def say(self, text, reply):
        asked = []
        def fake_call(name, prompt, session, **k):
            asked.append(prompt); return reply, None, None
        with mock.patch.object(relay, 'coordinator', return_value=self.agent), mock.patch.object(relay, 'chat_call', side_effect=fake_call):
            relay.chat_run(text)
        return asked[-1], relay.chat_load()['messages'][-1]

    def test_status_carries_the_list(self):
        relay.save_prefer([self.agent])
        self.assertEqual(relay.status_block()['prefer_agents'], [self.agent])
        prompt, _ = self.say('which agents are preferred?', 'Preferred: ' + self.agent)
        self.assertIn('"prefer_agents": ["%s"]' % self.agent, prompt)

    def test_chat_sets_and_clears(self):
        _, msg = self.say('use %s as much as possible' % self.agent, 'Done.\nACTIONS: [{"do": "prefer", "agents": ["%s"]}]' % self.agent)
        self.assertEqual(relay.prefer_agents(), [self.agent])
        self.assertTrue(msg['actions'][0]['ok']); self.assertIn(self.agent, msg['actions'][0]['text'])
        self.assertEqual(msg['text'], 'Done.')
        _, msg = self.say('stop preferring %s' % self.agent, 'Cleared.\nACTIONS: [{"do": "prefer", "agents": []}]')
        self.assertEqual(relay.prefer_agents(), [])
        self.assertEqual(msg['actions'][0], dict(ok=True, prefer_agents=[], text='No agents are preferred now'))

    def test_unknown_agent_refused(self):
        relay.save_prefer([self.agent])
        _, msg = self.say('use nobody', 'ACTIONS: [{"do": "prefer", "agents": ["nobody-xyz"]}]')
        self.assertFalse(msg['actions'][0]['ok']); self.assertIn('Unknown agent: nobody-xyz', msg['actions'][0]['text'])
        self.assertEqual(relay.prefer_agents(), [self.agent])

    def test_rules_explain_prefer(self):
        self.assertIn('"do": "prefer"', relay.COORD_RULES)
