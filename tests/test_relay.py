import json, os, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import _isolate
TMP = tempfile.mkdtemp()
import relay

FAKE = {'exe': [sys.executable], 'stdin': True, 'chat_argv': ['{exe}', str(ROOT / 'tests/fake_agent.py'), '{model}'], 'argv': ['{exe}', str(ROOT / 'tests/fake_agent.py'), '{model}'], 'resume': ['--resume', '{session}'],
        'billing': {'guarded': True, 'note': 'test'}, 'limit': {'patterns': [r"(?i)hit your[\w\s-]{0,30}limit"], 'minutes': 60}}
NAMES = ('coord', 'lead', 'helper', 'breaker', 'silent', 'crash', 'asker', 'sleeper', 'overage')
# fake2 and fakeL are two more "providers" that behave the same, so a usage limit on one can be rerouted to another.
EXTRA = {'limlead': ('fakeL', 'limited', 2), 'limhelper': ('fakeL', 'limited', 2), 'limcoord': ('fakeL', 'limited', 3), 'limone': ('fakeL', 'limited', 1), 'warner': ('fake', 'warner', 2),
         'alt1': ('fake2', 'helper', 2), 'alt2': ('fake2', 'finisher', 2), 'alt3': ('fake2', 'coord', 3)}
relay.write_json(relay.DATA / 'config.json', {'projects_root': TMP, 'coordinator': 'coord',
    # Config merges defaults: prevent coordinator status checks from launching real tools.
    'providers': {**{k: {'exe': ['relay-test-provider-not-installed']} for k in relay.DEFAULT_CONFIG['providers']},
                  'fake': FAKE, 'fake2': FAKE, 'fakeL': FAKE}, 'handoff_cap': 6,
    'agents': {**{n: {'provider': 'fake', 'model': n, 'tier': 1} for n in NAMES}, **{n: {'provider': pv, 'model': m, 'tier': t} for n, (pv, m, t) in EXTRA.items()}}})
TEST = f'"{sys.executable}" -c "import os,sys; print(\'FAIL: test_old (t.T)\'); print(\'FAIL: test_new (t.T)\') if os.path.exists(\'broken.flag\') else None; sys.exit(1)"'


def repo(name):
    r = Path(TMP) / name; r.mkdir()
    for a in (['init', '-q', '-b', 'main'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'base']):
        subprocess.run(['git', '-C', str(r), *a], check=True)
    return r


def run(goal):
    relay.run_goal(goal['id']); return relay.load(goal['id'])


def wait_coordinator(n, timeout=30):
    """Wait until the chat is idle and at least n coordinator replies are saved.
    A momentary unreadable chat.json is not 'idle': reading it used to look like an empty chat."""
    deadline = time.time() + timeout
    last = ''
    while time.time() < deadline:
        try:
            c = relay.chat_load()
        except (OSError, ValueError):
            time.sleep(0.05)
            continue
        msgs = [m for m in c.get('messages') or [] if isinstance(m, dict) and m.get('role') == 'coordinator']
        last = json.dumps(c)[:800]
        if not c.get('busy') and len(msgs) >= n:
            return msgs
        time.sleep(0.05)
    raise AssertionError('coordinator did not answer: ' + last)


class RelayTests(unittest.TestCase):
    def test_lead_passes_to_helper_then_confirms_done_and_merges(self):
        r = repo('a'); g = run(relay.create_goal(dict(objective='Add the feature file', repo=str(r), lead='lead', team=['helper'], test_cmd=TEST)))
        self.assertEqual([t['agent'] for t in g['turns']], ['lead', 'helper', 'lead'])
        self.assertEqual(g['status'], 'ready'); self.assertEqual(g['handoffs'], 2)
        self.assertTrue(g['turns'][2]['resumed']); self.assertIn('resumed', g['turns'][2]['final'])   # same session continued
        self.assertEqual(g['turns'][1]['files'], ['feature.txt'])
        self.assertEqual(g['turns'][1]['tests']['verdict'], 'no_worse')      # the base already failed test_old
        self.assertFalse((r / 'feature.txt').exists())                       # nothing reaches the branch before Merge
        relay.merge_goal(g['id']); self.assertTrue((r / 'feature.txt').exists())

    def _ready_goal(self, name):
        r = repo(name); g = run(relay.create_goal(dict(objective='Add the feature file', repo=str(r), lead='lead', team=['helper'])))
        self.assertEqual(g['status'], 'ready'); return r, g

    def _commit(self, r, msg):
        subprocess.run(['git', '-C', str(r), 'add', '-A'], check=True)
        subprocess.run(['git', '-C', str(r), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', msg], check=True)

    def test_merge_moves_untracked_files_in_the_way_then_retries(self):
        r, g = self._ready_goal('mu')
        (r / 'feature.txt').write_text('mine, untracked', encoding='utf-8')
        g = relay.merge_goal(g['id'])
        self.assertEqual(g['status'], 'merged')
        self.assertIn(f"Moved 1 file that was in the way to data/merge-backup/{g['id']}", g['reason'])
        saved = relay.DATA / 'merge-backup' / g['id'] / 'feature.txt'
        self.assertEqual(saved.read_text(encoding='utf-8'), 'mine, untracked')     # moved, never deleted
        self.assertNotEqual((r / 'feature.txt').read_text(encoding='utf-8'), 'mine, untracked')
        self.assertIn('would be overwritten', (relay.goal_path(g['id']) / 'merge.log').read_text(encoding='utf-8'))   # raw git text kept in the log

    def test_merge_with_uncommitted_tracked_changes_is_plain_and_not_retried(self):
        r, g = self._ready_goal('md')
        (r / 'base.txt').write_text('v1', encoding='utf-8'); self._commit(r, 'base file')
        (r / 'base.txt').write_text('v2 not committed', encoding='utf-8')
        with self.assertRaises(ValueError) as cm: relay.merge_goal(g['id'])
        msg = str(cm.exception)
        self.assertIn('base.txt', msg); self.assertIn('Commit or discard', msg); self.assertNotIn('git ', msg.lower().replace('git does not', ''))
        self.assertEqual(relay.load(g['id'])['status'], 'ready')
        self.assertEqual((r / 'base.txt').read_text(encoding='utf-8'), 'v2 not committed')
        self.assertFalse((relay.DATA / 'merge-backup' / g['id']).exists())

    def test_merge_conflict_is_plain_leaves_folder_clean_and_keeps_raw_text_in_log(self):
        r, g = self._ready_goal('mc')
        (r / 'feature.txt').write_text('different content', encoding='utf-8'); self._commit(r, 'clash')
        with self.assertRaises(ValueError) as cm: relay.merge_goal(g['id'])
        msg = str(cm.exception)
        self.assertIn('feature.txt', msg); self.assertIn('Continue', msg); self.assertNotIn('CONFLICT', msg)
        self.assertEqual(subprocess.run(['git', '-C', str(r), 'status', '--porcelain'], capture_output=True, text=True).stdout.strip(), '')   # merge aborted
        self.assertIn('CONFLICT', (relay.goal_path(g['id']) / 'merge.log').read_text(encoding='utf-8'))
        self.assertEqual(relay.load(g['id'])['status'], 'ready')

    def test_summary_carries_the_latest_test_verdict(self):
        g = run(relay.create_goal(dict(objective='Break things', repo=str(repo('sv')), lead='breaker', test_cmd=TEST)))
        self.assertEqual(relay.summary(g)['tests']['verdict'], 'worse'); self.assertIn('new failing', relay.summary(g)['tests']['summary'])
        self.assertEqual((relay.summary(g)['last_agent'], relay.summary(g)['team']), ('breaker', g['team']))
        g = run(relay.create_goal(dict(objective='Add the feature file', repo=str(repo('sw')), lead='lead', team=['helper'], mode='manual')))
        self.assertEqual(relay.summary(g)['suggestion'], {'action': 'pass', 'agent': 'helper'})
        self.assertIn((relay.summary(g)['tests'] or {}).get('verdict'), (None, 'none'))   # no test command, no verdict

    def test_worse_tests_block_done_and_merge(self):
        r = repo('b'); g = run(relay.create_goal(dict(objective='Break things', repo=str(r), lead='breaker', test_cmd=TEST)))
        self.assertEqual(g['status'], 'needs_you'); self.assertIn('worse', g['reason'])
        self.assertEqual(g['turns'][0]['tests']['new'], ['test_new (t.T)'])
        relay.act(g['id'], dict(action='done'))
        with self.assertRaises(ValueError): relay.merge_goal(g['id'])

    def test_manual_mode_stops_after_each_turn_and_person_chooses(self):
        r = repo('c'); g = run(relay.create_goal(dict(objective='Add the feature file', repo=str(r), lead='lead', team=['helper'], mode='manual')))
        self.assertEqual((g['status'], len(g['turns']), g['suggestion']), ('needs_you', 1, {'action': 'pass', 'agent': 'helper'}))
        relay.act(g['id'], dict(action='pass', agent='helper', note='also be quick'))
        g = relay.load(g['id']); self.assertEqual(g['status'], 'queued'); self.assertIn('Create feature.txt', g['next']['note'])
        g = run(g); self.assertEqual(g['turns'][-1]['agent'], 'helper'); self.assertIn('also be quick', g['turns'][-1]['note'])

    def test_question_crash_silence_and_cap(self):
        g = run(relay.create_goal(dict(objective='Pick a colour', repo=str(repo('d')), lead='asker')))
        self.assertIn('Blue or green?', g['reason'])
        g = run(relay.create_goal(dict(objective='Crash twice', repo=str(repo('e')), lead='crash')))
        self.assertEqual((g['status'], len(g['turns'])), ('needs_you', 2)); self.assertIn('twice', g['reason'])
        g = run(relay.create_goal(dict(objective='Say nothing', repo=str(repo('f')), lead='silent')))
        self.assertIn('without saying', g['reason'])
        g = run(relay.create_goal(dict(objective='Add the feature file', repo=str(repo('g')), lead='lead', team=['helper'], cap=1)))
        self.assertEqual((g['status'], len(g['turns'])), ('needs_you', 1)); self.assertIn('hand-off limit', g['reason'])

    def test_idle_agent_is_stopped(self):
        relay.DEFAULT_CONFIG['idle_minutes'] = 0.02
        try: g = run(relay.create_goal(dict(objective='Sleep forever', repo=str(repo('h')), lead='sleeper', mode='manual')))
        finally: relay.DEFAULT_CONFIG['idle_minutes'] = 20
        self.assertEqual(g['turns'][0]['status'], 'timeout')

    def test_coordinator_answers_and_starts_a_goal(self):
        repo('chat')
        had = len([m for m in relay.chat_load().get('messages') or [] if m.get('role') == 'coordinator'])
        msgs = None
        for n, text in enumerate(('what is running?', 'start the feature please'), 1):
            relay.chat_send(text)
            msgs = wait_coordinator(had + n)
        self.assertEqual(msgs[had]['text'], 'Nothing is running.'); self.assertEqual(msgs[had + 1]['text'], 'On it.')
        started, missing = msgs[had + 1]['actions']
        self.assertTrue(started['ok']); self.assertFalse(missing['ok'])      # a bad action is reported, not hidden
        g = relay.load(started['goal']); self.assertEqual((g['lead'], g['team'], g['status']), ('lead', ['lead', 'helper'], 'queued'))
        relay.update(g['id'], status='stopped')

    def test_one_goal_error_is_contained(self):
        g = relay.create_goal(dict(objective='Add the feature file', repo=str(repo('i')), lead='lead'))
        (relay.goal_path(g['id']) / 'goal.json').write_text(json.dumps({**g, 'next': {}}))
        g = run(g); self.assertEqual(g['status'], 'needs_you'); self.assertIn('Vanguard error', g['reason'])

    def test_output_shapes_of_each_tool(self):
        def read(*events):
            s = dict(session=None, final=None, texts=[], error=None)
            for e in events: relay.read_event(json.dumps(e), s)
            return s
        # Codex: a retried error does not fail a turn that completes; a failed turn does.
        s = read({'type': 'thread.started', 'thread_id': 't1'}, {'type': 'error', 'message': 'Reconnecting 1/5'},
                 {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'hi'}}, {'type': 'turn.completed'})
        self.assertEqual((s['session'], s['texts'], s['error']), ('t1', ['hi'], None))
        self.assertIn('quota', read({'type': 'turn.failed', 'error': {'message': 'quota'}})['error'])
        # muse exec --json
        s = read({'stream': {'kind': 'session', 'id': 'm1'}, 'payload_type': 'run.terminal.completed', 'payload': {'terminal': 'completed', 'text': 'done'}})
        self.assertEqual((s['session'], s['final'], s['error']), ('m1', 'done', None))
        self.assertTrue(read({'stream': {}, 'payload_type': 'run.terminal.failed', 'payload': {'terminal': 'failed', 'reason': 'x'}})['error'])
        # agy stream-json: an empty answer after denied tools is a failure
        s = read({'event': 'init', 'conversation_id': 'a1', 'init': {}}, {'event': 'result', 'result': {'conversation_id': 'a1', 'status': 'SUCCESS', 'response': 'ok'}})
        self.assertEqual((s['session'], s['final'], s['error']), ('a1', 'ok', None))
        s = read({'event': 'result', 'result': {'status': 'SUCCESS', 'response': '', 'denied_actions': [{'action': 'command', 'display_name': 'RunCommand'}]}})
        self.assertIn('RunCommand', s['error'])

    def checker(self, text, code=0, **b):
        py = sys.executable
        script = f'import sys; print({text!r}); sys.exit({code})'
        return {'exe': [py], 'billing': {'check': ['{exe}', '-c', script], 'ok': 'ChatGPT', 'bad': 'API key', 'note': 'n', **b}}

    def test_billing_levels(self):
        chk = relay.run_billing_check
        self.assertEqual(chk('x', self.checker('Logged in using ChatGPT'))['level'], 'verified')
        r = chk('x', self.checker('Logged in using an API key')); self.assertEqual(r['level'], 'blocked'); self.assertIn('API key', r['reason'])
        self.assertEqual(chk('x', self.checker('Logged in using ChatGPT and an API key'))['level'], 'blocked')   # bad wins
        self.assertEqual(chk('x', self.checker('hello'))['level'], 'blocked')                                    # cannot confirm
        self.assertEqual(chk('x', self.checker('Logged in using ChatGPT', code=1))['level'], 'blocked')          # failed command
        self.assertEqual(chk('x', {'exe': [sys.executable], 'billing': {'check': ['{exe}', '-c', 'import sys; sys.exit(0)'], 'ok': 'x'}})['level'], 'blocked')
        self.assertEqual(chk('x', {'exe': [sys.executable], 'billing': {'check': ['no-such-program-xyz'], 'ok': 'x'}})['level'], 'blocked')
        self.assertEqual(chk('x', {'exe': [sys.executable], 'billing': {'guarded': True, 'note': 'n'}})['level'], 'guarded')
        self.assertEqual(chk('x', {'exe': [sys.executable]})['level'], 'blocked')                                 # unknown is blocked
        self.assertEqual(chk('x', {'exe': [sys.executable], 'billing': {'note': 'n'}})['level'], 'blocked')
        self.assertFalse(chk('x', {'exe': ['no-such-program-xyz'], 'billing': {'guarded': True}})['installed'])

    def test_stripped_environment_and_codex_login_flag(self):
        for k in ('CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'ANTHROPIC_BASE_URL', 'OPENAI_BASE_URL', 'CODEX_API_KEY',
                  'GOOGLE_APPLICATION_CREDENTIALS', 'GOOGLE_CLOUD_PROJECT', 'GOOGLE_GENAI_USE_VERTEXAI'):
            self.assertIn(k, relay.API_KEYS)
        codex = relay.DEFAULT_CONFIG['providers']['codex']
        for key in ('argv', 'resume_argv', 'chat_argv', 'chat_resume_argv'):
            self.assertEqual(codex[key][1:3], ['-c', 'forced_login_method=chatgpt'])

    def test_cache_and_recheck(self):
        relay.billing_recheck(); relay.billing_status('fake'); t = relay.BILL_CACHE['fake'][0]
        relay.billing_status('fake'); self.assertEqual(relay.BILL_CACHE['fake'][0], t)       # cached
        relay.billing_status('fake', force=True); self.assertGreaterEqual(relay.BILL_CACHE['fake'][0], t)
        relay.billing_recheck(); self.assertEqual(relay.BILL_CACHE, {})
        self.assertEqual(relay.billing_view()['providers']['fake']['level'], 'guarded')

    def test_blocked_agent_cannot_be_chosen_or_started(self):
        r = repo('bill1'); g = relay.create_goal(dict(objective='Add the feature file', repo=str(r), lead='lead', team=['helper']))
        relay.BILL_STOPPED['fake'] = 'test block'
        try:
            view = {a['id']: a for a in relay.agents_view()}
            self.assertTrue(view['lead']['installed'] and view['lead']['blocked']); self.assertFalse(view['lead']['available'])
            with self.assertRaises(ValueError): relay.create_goal(dict(objective='Another goal', repo=str(r), lead='lead'))
            g = run(g); t = g['turns'][0]
            self.assertEqual((g['status'], t['status']), ('needs_you', 'blocked')); self.assertIn('test block', g['reason'])
            self.assertIn('test block', (relay.goal_path(g['id']) / t['log']).read_text()); self.assertEqual(t['files'], [])
            with self.assertRaises(ValueError): relay.act(g['id'], dict(action='pass', agent='helper'))
            text, sid, err = relay.chat_call('coord', 'hi', None); self.assertIn('test block', err); self.assertEqual(text, '')
            self.assertNotEqual(relay.coordinator(), 'coord')
        finally:
            relay.billing_recheck()
        self.assertEqual(relay.coordinator(), 'coord')

    def test_overage_stops_turn_and_blocks_provider(self):
        g = run(relay.create_goal(dict(objective='Draw on extra usage', repo=str(repo('bill2')), lead='overage')))
        try:
            self.assertEqual(g['turns'][0]['status'], 'blocked'); self.assertEqual(g['status'], 'needs_you')
            self.assertIn('paid extra usage', g['reason']); self.assertLess(g['turns'][0]['ended'] - g['turns'][0]['started'], 20)
            self.assertEqual(relay.billing_view()['providers']['fake']['level'], 'blocked')
            self.assertFalse(any(a['available'] for a in relay.agents_view() if a['provider'] == 'fake'))
        finally:
            relay.billing_recheck()
        s = dict(session=None, final=None, texts=[], error=None)
        relay.read_event('{"isUsingOverage": false}', s); self.assertFalse(s.get('overage'))


class LimitTests(unittest.TestCase):
    """Usage limits. Only the fake providers exist while these run, so a reroute can never start a real agent."""

    def setUp(self):
        self.orig = relay.config
        def only_fakes():
            c = self.orig(); keep = ('fake', 'fake2', 'fakeL')
            c['agents'] = {k: v for k, v in c['agents'].items() if v['provider'] in keep}
            c['providers'] = {k: v for k, v in c['providers'].items() if k in keep}
            return c
        relay.config = only_fakes; relay.billing_recheck()

    def tearDown(self):
        relay.config = self.orig; relay.billing_recheck(); os.environ.pop('FAKE_HELPER', None)

    def test_detect_limit_and_parse_reset_time(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo('America/Chicago'); msg = "You've hit your 5-hour limit \u00b7 resets 5:40am (America/Chicago)"
        at = lambda h, m, d=11: datetime(2026, 11, d, h, m, tzinfo=tz).timestamp()
        self.assertEqual(relay.parse_reset(msg, at(3, 0)), at(5, 40))                  # later today
        self.assertEqual(relay.parse_reset(msg, at(6, 0)), at(5, 40, 12))              # already past: tomorrow
        self.assertEqual(relay.parse_reset("hit your limit \u00b7 resets 12:10am (America/Chicago)", at(23, 0)), at(0, 10, 12))
        self.assertEqual(relay.parse_reset("resets 5pm (America/Chicago)", at(3, 0)), at(17, 0))
        self.assertEqual(relay.parse_reset("resets 17:30 (America/Chicago)", at(3, 0)), at(17, 30))
        self.assertIsNone(relay.parse_reset('hit your limit, try later', at(3, 0)))
        self.assertIsNone(relay.parse_reset('resets 13:99am', at(3, 0)))
        relay.config = self.orig; now = time.time()
        # claude's built-in patterns (the real wording from a real run)
        hit = relay.detect_limit('claude', msg, now=at(3, 0)); self.assertEqual(hit['until'], at(5, 40)); self.assertIn('5-hour limit', hit['reason'])
        self.assertEqual(relay.detect_limit('claude', "You've hit your 5-hour limit", now=at(3, 0))['until'], at(3, 0) + 3600)   # no time given: 60 minutes
        self.assertEqual(relay.detect_limit('claude', "hit your 5-hour limit", hint=now + 500, now=now)['until'], now + 500)    # the tool's own reset time wins
        self.assertIsNone(relay.detect_limit('claude', 'The tests hit a timeout. Nothing else wrong.'))
        self.assertIsNone(relay.detect_limit('codex', "You've hit your 5-hour limit"))      # no confirmed wording, so no default pattern
        for prov in ('codex', 'grok', 'muse', 'gemini'):
            self.assertEqual(relay.DEFAULT_CONFIG['providers'][prov].get('limit', {}).get('patterns', []), [])
        # a rate_limit_event: the real field names from a real run
        s = dict(session=None, final=None, texts=[], error=None)
        relay.read_event(json.dumps({'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed', 'resetsAt': 1794373800, 'utilization': 0.8}}), s)
        self.assertNotIn('warn', s)
        relay.read_event(json.dumps({'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed_warning', 'resetsAt': 1794373800,
                                                                                       'rateLimitType': 'five_hour', 'utilization': 0.93}}), s)
        self.assertEqual((s['warn']['until'], s['warn']['window']), (1794373800.0, 'five_hour'))

    def test_provider_marked_expires_and_recheck_clears(self):
        r = repo('lim1'); g = relay.create_goal(dict(objective='Add the feature file', repo=str(r), lead='lead'))
        relay.mark_limited('fake', time.time() + 3600, 'hit your 5-hour limit')
        view = {a['id']: a for a in relay.agents_view()}
        self.assertFalse(view['lead']['available']); self.assertFalse(view['coord']['available']); self.assertTrue(view['alt1']['available'])
        self.assertTrue(view['lead']['limited_until'] > time.time()); self.assertIn('5-hour', view['lead']['limited_reason'])
        with self.assertRaises(ValueError): relay.create_goal(dict(objective='Another goal', repo=str(r), lead='lead'))
        with self.assertRaises(ValueError): relay.create_goal(dict(objective='Another goal', repo=str(r), lead='alt1', team=['helper']))
        turn = relay.run_turn(g, 'lead', 'a note', 'test')           # not started at all
        self.assertEqual((turn['status'], turn['files'], turn['note']), ('limited', [], 'a note'))
        self.assertIn('Not started', (relay.goal_path(g['id']) / turn['log']).read_text())
        g = relay.load(g['id']); relay.update(g['id'], status='needs_you')
        with self.assertRaises(ValueError): relay.act(g['id'], dict(action='continue', agent='lead'))
        self.assertEqual(relay.coordinator(), 'limone')                # the chat falls back: equal tier (1), other provider
        relay.LIMITED['fake']['until'] = time.time() - 1               # the time passes: clears by itself
        self.assertTrue({a['id']: a for a in relay.agents_view()}['lead']['available']); self.assertNotIn('fake', relay.LIMITED)
        relay.mark_limited('fake', time.time() + 3600, 'x'); relay.billing_recheck()   # "Recheck sign-ins"
        self.assertEqual(relay.LIMITED, {}); self.assertTrue({a['id']: a for a in relay.agents_view()}['lead']['available'])

    def test_limits_survive_restart_and_drop_expired(self):
        relay.LIMITED.clear()
        relay.mark_limited('fake', time.time() + 3600, 'hit your limit'); relay.mark_limited('old', time.time() + 5, 'soon')
        saved = json.loads((relay.DATA / 'limits.json').read_text(encoding='utf-8'))
        self.assertEqual(set(saved), {'fake', 'old'})
        saved['old']['until'] = time.time() - 1
        (relay.DATA / 'limits.json').write_text(json.dumps(saved), encoding='utf-8')
        relay.LIMITED.clear(); relay.load_limits()                      # "restart"
        self.assertEqual(set(relay.LIMITED), {'fake'}); self.assertEqual(relay.LIMITED['fake']['reason'], 'hit your limit')
        relay.billing_recheck()
        self.assertEqual(json.loads((relay.DATA / 'limits.json').read_text(encoding='utf-8')), {})

    def test_reroute_order_equal_then_lower_then_higher_team_first(self):
        def view(*rows): return [dict(id=i, provider=p, tier=t, available=ok) for i, p, t, ok in rows]
        relay.config = lambda: {'agents': {'me': {'provider': 'a', 'tier': 2}}, 'providers': {'b': {'chat_argv': ['x']}, 'c': {'chat_argv': ['x']}, 'a': {}}}
        v = view(('me', 'a', 2, True), ('same2', 'a', 2, True), ('hi', 'b', 3, True), ('lo', 'b', 1, True), ('eq', 'c', 2, True))
        pick = lambda team, **kw: relay.pick_replacement(team, 'me', view=v, **kw)
        self.assertEqual(pick(()), 'eq')                                                    # equal tier, another provider
        self.assertEqual(relay.pick_replacement((), 'me', view=v[:4]), 'lo')                 # no equal: one lower before higher
        self.assertEqual(relay.pick_replacement((), 'me', view=v[:3]), 'hi')                 # only higher left
        self.assertEqual(pick(('hi',)), 'hi')                                               # a team member beats a better tier outside the team
        self.assertEqual(pick((), exclude=('eq',)), 'lo'); self.assertEqual(pick((), skip_providers=('c',)), 'lo')
        self.assertIsNone(relay.pick_replacement((), 'me', view=view(('me', 'a', 2, True), ('same2', 'a', 2, True))))   # same provider never
        self.assertIsNone(relay.pick_replacement((), 'me', view=view(('me', 'a', 2, True), ('eq', 'c', 2, False))))     # not available
        relay.config = lambda: {'agents': {'me': {'provider': 'a', 'tier': 3}}, 'providers': {}}
        v2 = view(('me', 'a', 3, True), ('l1', 'b', 2, True), ('l2', 'c', 1, True), ('h', 'c', 3, True))
        self.assertEqual(relay.pick_replacement((), 'me', view=v2), 'h')                     # equal (3) wins
        self.assertEqual(relay.pick_replacement((), 'me', view=v2[:3]), 'l1')                # one lower (2) before two lower (1)

    def test_limit_failure_reroutes_with_same_note_and_does_not_count_as_handoff(self):
        os.environ['FAKE_HELPER'] = 'limhelper'
        g = run(relay.create_goal(dict(objective='Add the feature file', repo=str(repo('lim2')), lead='lead', team=['limhelper', 'alt1'])))
        self.assertEqual([t['agent'] for t in g['turns']], ['lead', 'limhelper', 'alt1', 'lead'])
        t = g['turns'][1]; self.assertEqual((t['status'], t['limited']['provider']), ('failed', 'fakeL')); self.assertIn('5-hour', t['error'])
        self.assertIn('Create feature.txt containing hello', g['turns'][2]['note'])        # alt1 asserts it saw the same instructions
        self.assertEqual(g['turns'][2]['status'], 'ok'); self.assertEqual(g['turns'][2]['files'], ['feature.txt'])
        self.assertEqual(g['status'], 'ready'); self.assertEqual(g['handoffs'], 2)          # lead->limhelper, alt1->lead; the reroute is free
        self.assertEqual(len(g['reroutes']), 1); self.assertEqual((g['reroutes'][0]['from_agent'], g['reroutes'][0]['to']), ('limhelper', 'alt1'))
        self.assertIn('usage limit', g['reroutes'][0]['why']); self.assertEqual(sum(1 for x in g['turns'] if x['agent'] == 'limhelper'), 1)   # never retried
        self.assertIn('fakeL', relay.LIMITED); self.assertNotIn('fake', relay.LIMITED)

    def test_reroute_adds_a_non_team_agent_to_the_team(self):
        g = run(relay.create_goal(dict(objective='Do a thing', repo=str(repo('lim3')), lead='limone')))
        self.assertEqual([t['agent'] for t in g['turns']], ['limone', 'asker'])             # equal tier (1), another provider, outside the team
        self.assertIn('asker', g['team']); self.assertIn('Blue or green?', g['reason']); self.assertEqual(g['handoffs'], 0)

    def test_nobody_available_goes_to_needs_you_with_reset_time(self):
        g = relay.create_goal(dict(objective='Do a thing', repo=str(repo('lim4')), lead='limlead'))
        relay.mark_limited('fake', time.time() + 10800, 'x'); relay.mark_limited('fake2', time.time() + 10800, 'x')
        g = run(g)
        self.assertEqual((g['status'], len(g['turns']), g['suggestion']), ('needs_you', 1, None))
        self.assertIn('resets', g['reason']); self.assertIn('nobody else', g['reason'])
        self.assertIsNone(relay.summary(g)['suggestion'])

    def test_early_warning_routes_next_turn_elsewhere(self):
        g = run(relay.create_goal(dict(objective='Add the feature file', repo=str(repo('lim5')), lead='warner', team=['alt2'])))
        self.assertEqual([t['agent'] for t in g['turns']], ['warner', 'alt2'])               # warner finished its turn, then was not started again
        self.assertEqual(g['turns'][0]['status'], 'ok'); self.assertEqual(g['turns'][0]['limit_warning']['utilization'], 0.93)
        self.assertIn('Create feature.txt', g['turns'][1]['note']); self.assertIn('note seen: yes', g['reason'])
        self.assertEqual((g['handoffs'], len(g['reroutes'])), (0, 1))
        self.assertNotIn('fake', relay.LIMITED)                                               # a warning is not a hit: other goals keep using it
        self.assertIn('fake', g['avoid'])

    def test_manual_mode_suggests_the_reroute_target(self):
        os.environ['FAKE_HELPER'] = 'limhelper'
        g = run(relay.create_goal(dict(objective='Add the feature file', repo=str(repo('lim6')), lead='lead', team=['limhelper', 'alt1'], mode='manual')))
        relay.act(g['id'], dict(action='pass', agent='limhelper')); g = run(relay.load(g['id']))
        self.assertEqual((g['status'], g['turns'][-1]['agent']), ('needs_you', 'limhelper'))
        self.assertEqual(g['suggestion'], {'action': 'pass', 'agent': 'alt1'})              # never the limited agent
        relay.act(g['id'], dict(action='pass', agent='alt1'))
        self.assertIn('Create feature.txt', relay.load(g['id'])['next']['note'])

    def test_locked_chat_file_is_not_replaced_with_an_empty_one(self):
        path = relay.DATA / 'chat.json'
        before = path.read_bytes() if path.exists() else None
        relay.write_json(path, {'messages': [{'role': 'you', 'text': 'keep', 'ts': 1}], 'sessions': {}, 'busy': True})
        saved = path.read_bytes()
        real = Path.read_text

        def flaky(self, *a, **k):
            if Path(self) == path:
                raise PermissionError('locked')
            return real(self, *a, **k)

        try:
            with patch.object(Path, 'read_text', flaky):
                try:
                    relay.chat_run('hello')
                except (OSError, ValueError):
                    pass
                self.assertEqual(path.read_bytes(), saved)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['messages'][0]['text'], 'keep')
        finally:
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(before)

    def test_chat_falls_back_when_its_model_is_limited(self):
        c = relay.config; relay.config = lambda: {**c(), 'coordinator': 'limcoord'}
        before = len(relay.chat_load()['messages'])
        had = len([m for m in relay.chat_load().get('messages') or [] if m.get('role') == 'coordinator'])
        relay.chat_send('what is running?')
        reply = wait_coordinator(had + 1)[-1]
        self.assertGreater(len(relay.chat_load()['messages']), before)
        self.assertEqual((reply.get('text'), reply.get('agent'), reply.get('failed')), ('Nothing is running.', 'alt3', None), msg=json.dumps(reply)[:600])
        self.assertIn('fakeL', relay.LIMITED)
        relay.write_json(relay.DATA / 'chat.json', dict(messages=[], sessions={}, busy=False))   # leave the chat as found

    def test_failed_turn_keeps_its_note(self):
        os.environ['FAKE_HELPER'] = 'crash'; note = 'Create feature.txt containing hello'
        g = run(relay.create_goal(dict(objective='Add the feature file', repo=str(repo('lim7')), lead='lead', team=['crash'], mode='manual')))
        relay.act(g['id'], dict(action='pass', agent='crash')); g = run(relay.load(g['id']))
        self.assertEqual(g['turns'][-1]['status'], 'failed'); self.assertEqual(g['turns'][-1]['note'], note)
        self.assertEqual((g['status'], g['next']['agent']), ('needs_you', 'lead')); self.assertIn(note, g['next']['note'])   # the lead gets it back
        relay.act(g['id'], dict(action='pass', agent='helper'))                              # or hand to someone else: still carried
        self.assertIn(note, relay.load(g['id'])['next']['note'])
        g = run(relay.load(g['id'])); self.assertEqual(g['turns'][-1]['status'], 'ok')       # helper asserted it saw the note
        # a lead whose own turn failed is retried with the note too, without nesting
        g2 = relay.create_goal(dict(objective='Crash with a note', repo=str(repo('lim8')), lead='crash', mode='manual'))
        relay.update(g2['id'], next=dict(agent='crash', note='keep me', why='x')); g2 = run(relay.load(g2['id']))
        self.assertIn('keep me', g2['next']['note']); self.assertEqual(relay.original_note(g2['next']['note']), 'keep me')
        self.assertEqual(relay.keep_note(g2['next']['note'], 'keep me'), g2['next']['note'])

    def test_objective_and_note_unwrapped_from_powershell_objects(self):
        r = repo('lim9'); wrapped = {'value': 'Add the feature file', 'PSPath': 'C:\\x.txt', 'PSDrive': {'Name': 'C'}}
        g = relay.create_goal(dict(objective=wrapped, repo=str(r), lead='lead', mode='manual')); self.assertEqual(g['objective'], 'Add the feature file')
        for bad in (5, ['a list of text'], {'value': 7}, {'text': 'no value key'}, True):
            with self.assertRaises(ValueError) as cm: relay.create_goal(dict(objective=bad, repo=str(r), lead='lead'))
            self.assertIn('objective', str(cm.exception))
        relay.update(g['id'], status='needs_you')
        with self.assertRaises(ValueError): relay.act(g['id'], dict(action='continue', note=12))
        relay.act(g['id'], dict(action='continue', note={'value': ' wrapped note ', 'PSPath': 'x'}))
        self.assertIn('wrapped note', relay.load(g['id'])['next']['note'])


if __name__ == '__main__':
    unittest.main()
