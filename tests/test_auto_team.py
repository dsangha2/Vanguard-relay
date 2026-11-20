"""Delegation plans, automatic team selection, and answers to agent questions."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolate  # noqa: F401  (must come before relay)
import relay


class TunnelProbeTests(unittest.TestCase):
    def test_tunnel_must_return_an_http_response(self):
        with patch.object(relay, 'urlopen') as request:
            self.assertTrue(relay.strata_reachable())
            request.assert_called_once_with('http://127.0.0.1:19111/v1/models', timeout=1)
        for failure, expected in [(TimeoutError(), False),
                                  (relay.HTTPError('http://127.0.0.1', 401, 'Auth', {}, None), True),
                                  (relay.HTTPError('http://127.0.0.1', 502, 'Tunnel unavailable', {}, None), False)]:
            with self.subTest(failure=failure), patch.object(relay, 'urlopen', side_effect=failure):
                self.assertEqual(relay.strata_reachable(), expected)


class AutoTeamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'project'; (self.repo / '.git').mkdir(parents=True)
        self.cfg = copy.deepcopy(relay.DEFAULT_CONFIG)
        self.cfg['agents'] = {
            'sol': dict(model='sol', provider='codex', tier=3, good_at='Planning and reviews'),
            'terra': dict(model='terra', provider='codex', tier=2),
            'luna': dict(model='luna', provider='codex', tier=1),
            'opus': dict(model='opus', provider='claude', tier=3),
            'strata': dict(model='local', provider='strata', tier=0)}
        self.view = [dict(id=k, available=True, installed=True, blocked=False, **v) for k, v in self.cfg['agents'].items()]
        for name, value in [('DATA', self.root / 'data'), ('config', lambda: self.cfg),
                            ('agents_view', lambda: self.view), ('coordinator', lambda: 'luna'),
                            ('strata_reachable', lambda: False), ('blocked_reason', lambda _: None),
                            ('limited_reason', lambda _: None), ('git', lambda *a, **k: '')]:
            p = patch.object(relay, name, value); p.start(); self.addCleanup(p.stop)

    def create(self, **kw):
        return relay.create_goal(dict(repo=str(self.repo), objective='Make a useful feature', **kw))

    def test_brief_file_named_in_objective_is_read_only_inside_projects_root(self):
        self.cfg['projects_root'] = str(self.root)
        brief = self.root / 'BRIEF.md'; brief.write_text('x' * 5000 + 'TAIL', encoding='utf-8')
        outside = tempfile.TemporaryDirectory(); self.addCleanup(outside.cleanup)
        secret = Path(outside.name) / 'SECRET.md'; secret.write_text('secret plan', encoding='utf-8')
        answer = (json.dumps(dict(lead='sol', team=['sol'], why='Big brief.')), None, None)
        with patch.object(relay, 'chat_call', return_value=answer) as call:
            relay.pick_team(f'Carry out {brief}', self.repo)
            payload = json.loads(call.call_args.args[1].split(chr(10), 1)[1])
            self.assertEqual(payload['brief_file_start'], 'x' * 4000)
            relay.pick_team(f'Carry out {secret}', self.repo)
            self.assertNotIn('brief_file_start', json.loads(call.call_args.args[1].split(chr(10), 1)[1]))
            relay.pick_team(f'Carry out {self.root / "missing.md"}', self.repo)
            self.assertNotIn('brief_file_start', json.loads(call.call_args.args[1].split(chr(10), 1)[1]))

    def test_brief_file_size_changes_the_rules_choice(self):
        self.cfg['projects_root'] = str(self.root)
        brief = self.root / 'BRIEF.md'; brief.write_text('Change many files. ' * 100, encoding='utf-8')
        with patch.object(relay, 'chat_call', return_value=(None, None, 'down')):
            plain = relay.pick_team(f'Carry out {brief}', self.repo)
            self.cfg['projects_root'] = str(self.root / 'elsewhere')
            ignored = relay.pick_team(f'Carry out {brief}', self.repo)
        self.assertNotEqual(plain['team'], ignored['team'])

    def test_valid_coordinator_selection_and_prompt(self):
        with patch.object(relay, 'chat_call', return_value=(json.dumps(dict(lead='sol', team=['sol', 'luna'], why='Needs a careful review.')), None, None)) as call:
            got = relay.pick_team('A large change ' * 40, self.repo)
        self.assertEqual(got, dict(lead='sol', team=['sol', 'luna'], why='Needs a careful review.', by='coordinator'))
        payload = json.loads(call.call_args.args[1].split('\n', 1)[1])
        self.assertEqual(payload['candidates'][0]['good_at'], 'Planning and reviews')
        self.assertNotIn('strata', [x['id'] for x in payload['candidates']])
        self.assertEqual(call.call_args.kwargs, dict(timeout=60, include_attachments=False))

    def test_invalid_selections_use_rules(self):
        invalid = [None, [], {}, dict(lead='sol', team=['sol', 'unknown'], why='No'),
                   dict(lead='sol', team=['luna'], why='No'), dict(lead='sol', team=['sol', 'sol'], why='No'),
                   dict(lead='sol', team=['sol'], why=1), dict(lead='sol', team=['sol'], why=''),
                   dict(lead='sol', team=['sol', 'terra', 'luna', 'opus', 'strata'], why='Too many')]
        for response in invalid:
            with self.subTest(response=response), patch.object(relay, 'chat_call', return_value=(json.dumps(response), None, None)):
                got = relay.pick_team('Long change ' * 50, self.repo)
                self.assertEqual((got['by'], got['lead'], got['team']), ('rules', 'sol', ['sol', 'terra', 'luna']))
        for result in [('not json', None, None), ('', None, 'timeout')]:
            with patch.object(relay, 'chat_call', return_value=result):
                self.assertEqual(relay.pick_team('Short task', self.repo)['team'], ['terra', 'luna'])
        with patch.object(relay, 'chat_call', side_effect=OSError('Unavailable')):
            self.assertEqual(relay.pick_team('Short task', self.repo)['by'], 'rules')

    def test_candidates_and_fallback_limits(self):
        self.view[0]['available'] = False  # billing blocked or usage limited
        with patch.object(relay, 'chat_call', return_value=('{}', None, None)), patch.object(relay, 'strata_reachable', return_value=True):
            got = relay.pick_team('Long task ' * 50, self.repo)
            self.assertEqual(got['team'], ['opus', 'terra', 'luna', 'strata'])
            self.assertEqual(relay.pick_team('Edit a.py b.js c.html d.css', self.repo)['lead'], 'opus')
        self.view = []
        with self.assertRaisesRegex(ValueError, 'No agents'):
            relay.pick_team('Task', self.repo)

    def test_auto_defaults_mixed_and_manual_choices(self):
        picked = dict(lead='terra', team=['terra', 'luna'], why='Small job.', by='rules')
        with patch.object(relay, 'pick_team', return_value=picked) as choose:
            for kw in ({}, {'lead': 'auto'}, {'lead': 'sol', 'team': 'auto'}, {'lead': 'auto', 'team': ['luna']}):
                g = self.create(mode='manual', **kw)
                self.assertEqual(g['mode'], 'auto')
                self.assertEqual(g['auto_team'], dict(why='Small job.', by='rules'))
                self.assertIn(g['lead'], g['team'])
                self.assertEqual(relay.load(g['id'])['auto_team'], g['auto_team'])
            choose.reset_mock()
            g = self.create(lead='sol', team=['luna'], mode='manual')
            choose.assert_not_called()
            self.assertEqual((g['team'], g['mode']), (['sol', 'luna'], 'manual'))
            self.assertNotIn('auto_team', g)
            solo = self.create(lead='sol')
            self.assertEqual(solo['team'], ['sol'])
        with self.assertRaisesRegex(ValueError, 'Team must'):
            self.create(lead='sol', team='luna')

    def test_coordinator_action_uses_same_auto_path(self):
        with patch.object(relay, 'pick_team', return_value=dict(lead='terra', team=['terra', 'luna'], why='Small job.', by='coordinator')):
            result = relay.run_action(dict(do='create_goal', repo=str(self.repo), objective='Build a useful feature'))
        self.assertEqual(relay.load(result['goal'])['auto_team']['by'], 'coordinator')

    def test_selection_does_not_hold_the_goal_state_lock(self):
        def choose(*args):
            acquired = threading.Event()
            def reader():
                with relay.LOCK: acquired.set()
            t = threading.Thread(target=reader, daemon=True); t.start()
            self.assertTrue(acquired.wait(1), 'Selection blocked concurrent state readers')
            t.join()
            return dict(lead='terra', team=['terra', 'luna'], why='Small job.', by='rules')
        with patch.object(relay, 'pick_team', side_effect=choose):
            self.create()

    def test_plan_bounded_stored_and_ticks_only_after_plan(self):
        g = self.create(lead='sol', team=['luna'])
        raw = [dict(piece='x' * 350, who='luna', why='y' * 350)] * 15
        prop = relay.proposal('RELAY: ' + json.dumps(dict(next='pass', to='luna', note='Build it', plan=raw)))
        t = dict(n=2, agent='sol', status='ok', proposal=prop)
        g['turns'] = [dict(n=1, agent='luna'), t]
        self.assertEqual(relay.decide(g, t)[0], 'run')
        relay.save(g); g = relay.load(g['id'])
        self.assertEqual((len(g['plan']), len(g['plan'][0]['piece']), len(g['plan'][0]['why'])), (12, 300, 300))
        self.assertFalse(relay.summary(g)['plan'][0]['done'])
        g['turns'].append(dict(n=3, agent='luna'))
        self.assertTrue(relay.summary(g)['plan'][0]['done'])
        self.assertEqual(relay.summary(g)['sessions'], dict(luna=2, sol=1))
        helper = dict(n=4, agent='luna', status='ok', proposal=prop)
        relay.decide(g, helper)
        self.assertEqual(g['plan_turn'], 2)
        self.assertIsNotNone(relay.proposal('RELAY: {"next":"continue"}'))

    def test_done_without_helpers_warns_but_does_not_block(self):
        g = self.create(lead='sol', team=['luna'])
        t = dict(n=1, agent='sol', status='ok', proposal=relay.proposal('RELAY: {"next":"done","summary":{"built":["Works"]}}'))
        g['turns'] = [t]
        self.assertEqual(relay.decide(g, t)[0], 'ready')
        self.assertIn('The lead did all the work itself; helpers were not used.', g['summary']['watch_out'])
        g['turns'].insert(0, dict(agent='luna'))
        t['proposal']['summary']['watch_out'] = []
        relay.decide(g, t)
        self.assertEqual(g['summary']['watch_out'], [])

    def test_question_survives_run_and_answer_returns_to_asker(self):
        g = self.create(lead='sol', team=['luna'])
        def turn(goal, *args):
            t = dict(n=1, agent='luna', status='ok', proposal=relay.proposal('RELAY: {"next":"ask","note":"Blue or green?"}'))
            goal['turns'].append(t); relay.save(goal)
            return t
        with patch.object(relay, 'run_turn', side_effect=turn), patch.object(relay, 'limit_hold', return_value=None), patch.object(relay, 'notify'), patch.object(relay, 'chat_post'):
            relay.run_goal(g['id'])
        g = relay.load(g['id'])
        self.assertEqual(g['status'], 'needs_you')
        self.assertEqual(relay.summary(g)['question'], dict(agent='luna', text='Blue or green?'))
        g = relay.act(g['id'], dict(action='continue', agent='sol', note='Blue please'))
        self.assertEqual((g['status'], g['next']['agent'], g['next']['note']), ('queued', 'luna', 'Blue please'))
        self.assertNotIn('question', g)

    def test_delegation_briefs_and_optional_plan(self):
        g = self.create(lead='sol', team=['luna'])
        first = relay.brief(g, 'sol', '', False)
        self.assertIn('Before any big edit', first)
        self.assertIn('cheapest team member', first)
        self.assertIn('"plan"', first)
        helper = relay.brief(g, 'luna', 'Only edit the icon', False)
        self.assertIn('Do not widen scope', helper)
        self.assertIn('End with planner', helper)
        g['turns'] = [dict(agent='sol')]
        self.assertNotIn('Before any big edit', relay.brief(g, 'sol', '', True))


if __name__ == '__main__':
    unittest.main()
