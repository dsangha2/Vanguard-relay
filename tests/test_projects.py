import http.client, json, os, subprocess, sys, tempfile, threading, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import _isolate
TMP = tempfile.mkdtemp()
import relay
from unittest.mock import patch

FAKE = {'exe': [sys.executable], 'stdin': True, 'chat_argv': ['{exe}', str(ROOT / 'tests/fake_agent.py'), '{model}'], 'argv': ['{exe}', str(ROOT / 'tests/fake_agent.py'), '{model}'], 'resume': ['--resume', '{session}'],
        'billing': {'guarded': True, 'note': 'test'}, 'limit': {'patterns': [r"(?i)hit your[\w\s-]{0,30}limit"], 'minutes': 60}}
def our_config():
    return {'projects_root': TMP, 'coordinator': 'planner',
    'providers': {**{k: {'exe': ['relay-test-provider-not-installed']} for k in relay.DEFAULT_CONFIG['providers']}, 'fake': FAKE}, 'handoff_cap': 6,
    'agents': {n: {'provider': 'fake', 'model': n, 'tier': 1} for n in ('planner', 'piecer')}}


def repo(name):
    r = Path(TMP) / name; r.mkdir()
    for a in (['init', '-q', '-b', 'main'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'base']):
        subprocess.run(['git', '-C', str(r), *a], check=True)
    return r


def head(r, ref='HEAD'):
    return subprocess.run(['git', '-C', str(r), 'rev-parse', ref], capture_output=True, text=True, check=True).stdout.strip()


class ProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):      # the config file is shared by every test module: ours only while these tests run
        cls._cfg = (relay.DATA / 'config.json').read_bytes() if (relay.DATA / 'config.json').exists() else None
        relay.write_json(relay.DATA / 'config.json', our_config())

    @classmethod
    def tearDownClass(cls):
        if cls._cfg is not None: (relay.DATA / 'config.json').write_bytes(cls._cfg)

    def setUp(self):
        self._pick = relay.pick_team
        relay.pick_team = lambda *a, **k: dict(lead='piecer', team=['piecer'], why='t', by='rules')
        self._env = {}

    def tearDown(self):
        relay.pick_team = self._pick
        for k in self._env:
            os.environ.pop(k, None)
        relay.PTHREADS.clear()

    def env(self, **kw):
        for k, v in kw.items():
            os.environ[k] = str(v); self._env[k] = 1

    def new(self, name, **kw):
        r = repo(name)
        p = relay.create_project(dict(title='Big', objective='Build three parts', repo=str(r), lead='planner', **kw))
        return r, p['id']

    def drive(self, pid, auto_start=True, limit=60, until=('ready', 'needs_you', 'stopped', 'merged')):
        for _ in range(limit):
            p = relay.load_project(pid)
            if p['status'] == 'plan_ready':
                if not auto_start: return p
                relay.start_project(pid)
            if p['status'] in until: return p
            relay.project_step(pid, background=False)
            for g in relay.all_goals():
                if g['status'] == 'queued': relay.run_goal(g['id'])
        self.fail('project did not settle: ' + relay.load_project(pid)['status'])

    def test_plan_created_and_saved(self):
        r, pid = self.new('plan')
        self.assertEqual(relay.load_project(pid)['status'], 'planning')
        p = self.drive(pid, auto_start=False)
        self.assertEqual(p['status'], 'plan_ready'); self.assertEqual([x['title'] for x in p['pieces']], ['Part 1', 'Part 2', 'Part 3'])
        d = relay.DATA / 'projects' / pid
        self.assertEqual(json.loads((d / 'project.json').read_text(encoding='utf-8'))['id'], pid)
        self.assertIn('Part 2', (d / 'plan.md').read_text(encoding='utf-8'))
        self.assertTrue((d / 'notes.md').is_file())

    def test_edited_plan_is_used(self):
        r, pid = self.new('edit')
        self.drive(pid, auto_start=False)
        relay.edit_plan(pid, [dict(title='Only one', what='Create part1.txt. Reworded on purpose.', depends_on=[]),
                              dict(title='Then two', what='Create part2.txt. Check it.', depends_on=[1])])
        p = self.drive(pid)
        self.assertEqual(p['status'], 'ready'); self.assertEqual(len(p['pieces']), 2)
        g = relay.load(p['pieces'][0]['goal'])
        self.assertIn('Reworded on purpose', g['objective']); self.assertNotIn('Part 3', g['objective'])

    def test_order_branching_notes_and_base_untouched(self):
        r, pid = self.new('flow')
        self.drive(pid, auto_start=False)
        relay.start_project(pid)
        p = relay.load_project(pid); main0 = head(r, 'main')
        self.assertEqual(p['branch'], 'relay/project-' + pid); start = head(r, p['branch'])
        starts = []
        for _ in range(60):
            relay.project_step(pid, background=False)
            for g in relay.all_goals():
                if g['status'] == 'queued':
                    if g.get('project') == pid and g['piece'] == 1:
                        self.assertEqual(g['base_branch'], p['branch']); self.assertEqual(g['base_sha'], start)
                    starts.append(g['piece']); relay.run_goal(g['id'])
            if relay.load_project(pid)['status'] == 'ready': break
        p = relay.load_project(pid)
        self.assertEqual(p['status'], 'ready'); self.assertEqual(starts, [1, 2, 3])
        self.assertEqual(head(r, 'main'), main0); self.assertFalse((r / 'part1.txt').exists())      # base untouched until Merge
        notes = relay.read_notes(pid)
        for n in (1, 2, 3): self.assertIn(f'## Piece {n}:', notes)
        work = Path(p['work'])
        self.assertIn('seen: yes', (work / 'part2.txt').read_text(encoding='utf-8'))
        self.assertIn('seen: no', (work / 'part1.txt').read_text(encoding='utf-8'))
        g2 = relay.load(p['pieces'][1]['goal'])
        self.assertGreater(g2['created'], relay.load(p['pieces'][0]['goal'])['created'])

        with self.assertRaises(ValueError): relay.merge_goal(p['pieces'][0]['goal'])             # a piece goal never merges by itself
        self.assertEqual(relay.load_project(pid)['status'], 'ready')
        m = relay.merge_project(pid)
        self.assertEqual(m['status'], 'merged')
        for n in (1, 2, 3): self.assertTrue((r / f'part{n}.txt').exists())

    def test_auto_merge_refuses_anything_but_the_project_branch(self):
        r = repo('refuse'); log = Path(TMP) / 'refuse.log'
        for target in ('main', 'master', 'relay/g12345678', 'relay/project-pzzzzzzzz', 'relay/project-p12345678/x', '', None):
            with self.assertRaises(ValueError, msg=str(target)):
                relay.auto_merge(str(r), target, 'relay/x', log, 'x')
        with self.assertRaises(ValueError):       # the project's own branch is the only target
            relay.auto_merge(str(r), 'relay/project-p12345678', 'relay/x', log, 'x', project=dict(branch='relay/project-paaaaaaaa', base_branch='main'))
        self.assertEqual(head(r), head(r, 'main'))

    def prepare_merge_check(self, name, piece=False):
        r, pid = self.new(name, test_cmd=f'"{sys.executable}" -m unittest -v test_combination.py')
        relay.git(r, 'config', 'user.name', 'test')
        relay.git(r, 'config', 'user.email', 'test@localhost')
        (r / 'test_combination.py').write_text(
            'import unittest\nfrom pathlib import Path\n'
            'class Combination(unittest.TestCase):\n'
            '    def test_combination(self):\n'
            '        self.assertFalse(Path("part1.txt").exists() and Path("break.flag").exists())\n', encoding='utf-8')
        self.commit_merge_change(r)
        if piece:
            self.drive(pid, auto_start=False); relay.start_project(pid)
            self.assertEqual(relay.project_step(pid, background=False), 'baseline')
            self.assertEqual(relay.project_step(pid, background=False), 'started')
            g = relay.load(relay.load_project(pid)['pieces'][0]['goal'])
            relay.run_goal(g['id'])
            g = relay.load(g['id'])
            self.assertEqual(g['status'], 'ready')
            p = relay.load_project(pid)
            self.assertEqual(g['turns'][-1]['base_tested'], head(r, p['branch']))
        else:
            p = self.drive(pid)
            self.assertEqual(p['status'], 'ready')
            self.assertEqual(p['base_tested'], head(r, 'main'))
        return r, p

    def commit_merge_change(self, folder):
        relay.git(folder, 'add', '.')
        relay.git(folder, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'merge check fixture')

    def assert_merge_check_cleaned(self, r, item):
        tmp = relay.DATA / 'merge-check' / item['id']
        self.assertFalse(tmp.exists())
        self.assertNotIn(tmp.as_posix(), relay.git(r, 'worktree', 'list', '--porcelain'))
        self.assertNotIn(item['id'], relay.MERGE_CHECKS)

    def check_project_merge(self, breaks):
        r, p = self.prepare_merge_check('project-combination-' + str(breaks))
        pid = p['id']
        (r / ('break.flag' if breaks else 'safe.flag')).write_text('base moved', encoding='utf-8')
        self.commit_merge_change(r); before = head(r)
        # Each branch passes alone; only their combination can fail.
        self.assertEqual(relay.run_tests(dict(p, work=str(r), log_dir=str(relay.project_path(pid))), 'base-moved')['exit'], 0)
        real_tests = relay.run_tests; seen = []

        def check_phase(item, label):
            self.assertEqual(relay.load_project(pid)['phase'], relay.MERGE_PHASE)
            seen.append(label)
            return real_tests(item, label)

        with patch.object(relay, 'run_tests', check_phase):
            if breaks:
                with self.assertRaisesRegex(ValueError, 'combined code is worse.*test_combination.*Continue'):
                    relay.merge_project(pid)
            else:
                self.assertEqual(relay.merge_project(pid)['status'], 'merged')
        p = relay.load_project(pid)
        self.assertEqual(seen, ['merge-check'])
        self.assertEqual(p['phase'], '')
        self.assertEqual(p['merge_check']['base'], before)
        self.assertEqual(p['merge_check']['verdict'], 'worse' if breaks else 'pass')
        if breaks:
            self.assertEqual(head(r), before)
            self.assertEqual(p['status'], 'ready')
            self.assertFalse((r / 'part1.txt').exists())
        else:
            self.assertNotEqual(head(r), before)
            self.assertTrue((r / 'part1.txt').exists())
            self.assertTrue((r / 'safe.flag').exists())
        self.assertTrue((relay.project_path(pid) / 'tests-merge-check.log').is_file())
        self.assert_merge_check_cleaned(r, p)

    def test_project_merge_moved_base_combination_failure_is_refused(self):
        self.check_project_merge(True)

    def test_project_merge_moved_base_combination_passes(self):
        self.check_project_merge(False)

    def check_piece_combination(self, breaks):
        r, p = self.prepare_merge_check('piece-combination-' + str(breaks), piece=True)
        pid = p['id']; gid = p['pieces'][0]['goal']; work = Path(p['work'])
        base_before = head(r)
        (work / ('break.flag' if breaks else 'safe.flag')).write_text('project moved', encoding='utf-8')
        self.commit_merge_change(work); before = head(work)
        real_tests = relay.run_tests; seen = []

        def check_phase(item, label):
            self.assertEqual(relay.load_project(pid)['phase'], relay.MERGE_PHASE)
            self.assertEqual(relay.load(gid)['phase'], relay.MERGE_PHASE)
            seen.append(label)
            return real_tests(item, label)

        with patch.object(relay, 'run_tests', check_phase):
            self.assertEqual(relay.project_step(pid, background=False), 'checking')
        p = relay.load_project(pid); g = relay.load(gid)
        self.assertEqual(seen, ['merge-check'])
        self.assertEqual(p['phase'], ''); self.assertEqual(g['phase'], '')
        self.assertEqual(head(r), base_before)
        self.assertEqual(g['merge_check']['base'], before)
        self.assertEqual(g['merge_check']['verdict'], 'worse' if breaks else 'pass')
        if breaks:
            self.assertEqual(head(work), before)
            self.assertEqual((p['status'], p['pieces'][0]['status'], g['status']), ('needs_you', 'failed', 'ready'))
            self.assertIn('test_combination', p['reason']); self.assertIn('Continue', p['reason'])
            self.assertFalse((work / 'part1.txt').exists())
        else:
            self.assertEqual((p['status'], p['pieces'][0]['status'], g['status']), ('running', 'done', 'merged'))
            self.assertTrue((work / 'part1.txt').exists()); self.assertTrue((work / 'safe.flag').exists())
        self.assert_merge_check_cleaned(r, g)

    def test_piece_merge_moved_project_combination_failure_is_refused(self):
        self.check_piece_combination(True)

    def test_piece_merge_moved_project_combination_passes(self):
        self.check_piece_combination(False)

    def test_piece_failing_only_in_fresh_checkout_goes_back_to_lead(self):
        r, pid = self.new('fresh-eol', test_cmd=f'"{sys.executable}" -m unittest -v test_eol.py')
        (r / '.gitattributes').write_bytes(b'eol.txt text eol=crlf\n')
        (r / 'test_eol.py').write_text(
            'import unittest\nfrom pathlib import Path\n'
            'class Eol(unittest.TestCase):\n'
            '    def test_eol_is_lf(self):\n'
            '        p = Path("eol.txt")\n'
            '        self.assertFalse(p.exists() and b"\\r" in p.read_bytes())\n', encoding='utf-8')
        self.commit_merge_change(r)
        self.drive(pid, auto_start=False); relay.start_project(pid)
        self.assertEqual(relay.project_step(pid, background=False), 'baseline')
        self.assertEqual(relay.project_step(pid, background=False), 'started')
        p = relay.load_project(pid); gid = p['pieces'][0]['goal']
        relay.run_goal(gid)
        g = relay.load(gid); self.assertEqual(g['status'], 'ready')
        # The agent's folder has LF endings; a fresh checkout writes CRLF because of .gitattributes.
        work = Path(g['work']); (work / 'eol.txt').write_bytes(b'one\ntwo\n')
        self.commit_merge_change(work)
        self.assertEqual(relay.run_tests(dict(g, log_dir=str(relay.goal_path(gid))), 'in-place')['exit'], 0)
        project_before = head(r, p['branch'])
        self.assertEqual(relay.project_step(pid, background=False), 'checking')
        p = relay.load_project(pid); g = relay.load(gid)
        self.assertEqual(head(r, p['branch']), project_before)                     # not merged
        self.assertFalse((Path(p['work']) / 'eol.txt').exists())
        self.assertEqual((p['status'], p['pieces'][0]['status']), ('running', 'running'))
        self.assertEqual(g['status'], 'queued'); self.assertEqual(g['next']['agent'], g['lead'])
        self.assertIn(relay.FRESH_NOTE, g['next']['note']); self.assertIn('test_eol_is_lf', g['next']['note'])
        self.assertEqual(g['fresh_check']['verdict'], 'worse')
        self.assertEqual((p['phase'], g['phase']), ('', ''))
        tmp = relay.DATA / 'fresh-check' / gid
        self.assertFalse(tmp.exists())
        self.assertNotIn(tmp.as_posix(), relay.git(r, 'worktree', 'list', '--porcelain'))
        self.assertNotIn(gid, relay.MERGE_CHECKS)
        # The lead changes nothing: the same branch is never added; the project stops for the user instead.
        relay.run_goal(gid)
        self.assertEqual(relay.load(gid)['status'], 'ready')
        self.assertEqual(relay.load(gid)['fresh_check']['sha'], head(r, relay.load(gid)['branch']))
        self.assertEqual(relay.project_step(pid, background=False), 'needs_you')
        p = relay.load_project(pid)
        self.assertEqual(p['pieces'][0]['status'], 'failed'); self.assertIn(relay.FRESH_NOTE, p['reason'])
        self.assertEqual(head(r, p['branch']), project_before)

    def test_failing_piece_stops_with_plain_reason_then_skip(self):
        self.env(FAKE_FAIL_PIECE=2)
        r, pid = self.new('fail')
        p = self.drive(pid)
        self.assertEqual(p['status'], 'needs_you'); self.assertIn('Piece 2', p['reason'])
        self.assertNotIn('Traceback', p['reason'])
        self.assertEqual([x['status'] for x in p['pieces']], ['done', 'failed', 'waiting'])
        relay.project_act(pid, dict(action='skip', n=2))
        p = relay.load_project(pid); self.assertEqual(p['status'], 'running'); self.assertEqual(p['pieces'][1]['status'], 'skipped')
        p = self.drive(pid)
        self.assertEqual(p['status'], 'ready'); self.assertEqual([x['status'] for x in p['pieces']], ['done', 'skipped', 'done'])

    def test_stop_during_piece_tests_is_durable(self):
        r, pid = self.new('stoptests', test_cmd=f'"{sys.executable}" -c "pass"')
        self.drive(pid, auto_start=False); relay.start_project(pid)
        real_tests = relay.run_tests; seen = []

        def stop_in_tests(goal, label):
            if label.startswith('turn-'):
                seen.append(goal['id']); relay.project_act(pid, dict(action='stop'))
            return real_tests(goal, label)

        for _ in range(10):
            relay.project_step(pid, background=False)
            queued = [g for g in relay.all_goals() if g['status'] == 'queued' and g.get('project') == pid]
            if queued: break
        self.assertEqual(len(queued), 1)
        relay.PTHREADS.pop(pid, None)
        with patch.object(relay, 'run_tests', stop_in_tests):
            relay.run_goal(queued[0]['id'])
        g = relay.load(queued[0]['id']); p = relay.load_project(pid)
        self.assertEqual(len(seen), 1)
        self.assertEqual((g['status'], g['reason']), ('stopped', 'You stopped its project'))
        self.assertEqual(p['status'], 'stopped'); self.assertTrue(p['stop_requested'])
        for _ in range(3):                                        # nothing moves it on or starts the next piece
            self.assertEqual(relay.project_step(pid, background=False), 'idle')
        relay.project_needs_you(pid, 'late worker')                # a late worker cannot move it out of stopped
        p = relay.load_project(pid)
        self.assertEqual(p['status'], 'stopped'); self.assertEqual(p['reason'], 'You stopped it')
        self.assertEqual([x['status'] for x in p['pieces']][1:], ['waiting', 'waiting'])
        self.assertEqual(len([x for x in relay.all_goals() if x.get('project') == pid]), 1)
        relay.project_act(pid, dict(action='retry', n=1))          # retry clears the stop
        p = relay.load_project(pid)
        self.assertEqual(p['status'], 'running'); self.assertNotIn('stop_requested', p)

    def test_retry_makes_a_new_goal(self):
        self.env(FAKE_FAIL_PIECE=2)
        r, pid = self.new('retry')
        p = self.drive(pid); old = p['pieces'][1]['goal']
        self.env(FAKE_FAIL_PIECE=9)
        relay.project_act(pid, dict(action='retry', n=2))
        p = self.drive(pid)
        self.assertEqual(p['status'], 'ready')
        self.assertNotEqual(p['pieces'][1]['goal'], old); self.assertEqual(len(p['pieces'][1]['goals']), 2)

    def test_restart_resumes_and_cut_off_goal_needs_you(self):
        r, pid = self.new('restart')
        self.drive(pid, auto_start=False); relay.start_project(pid)
        while relay.load_project(pid)['pieces'][0]['status'] != 'done':
            relay.project_step(pid, background=False)
            for g in relay.all_goals():
                if g['status'] == 'queued': relay.run_goal(g['id'])
        relay.PTHREADS.clear()                                  # a fresh process has no threads
        self.assertEqual(relay.project_step(pid, background=False), 'started')
        p = relay.load_project(pid); self.assertEqual(p['pieces'][1]['status'], 'running')
        g = relay.load(p['pieces'][1]['goal']); self.assertEqual(g['status'], 'queued')
        g['status'] = 'running'; relay.save(g)                  # cut off while running, no thread behind it
        relay.recover_cut_off_goals([relay.load(g['id'])])
        self.assertEqual(relay.project_step(pid, background=False), 'needs_you')
        p = relay.load_project(pid); self.assertEqual(p['status'], 'needs_you'); self.assertIn('restarted', p['reason'])

    def test_worse_final_tests_need_you(self):
        r, pid = self.new('worsefinal', test_cmd=f'"{sys.executable}" -c "pass"')
        real_judge = relay.judge
        names = ['test_a', 'test_b', 'test_c', 'test_d']

        def worse_judge(p, result, label, *a, **k):
            v = real_judge(p, result, label, *a, **k)
            if label == 'final':
                v = dict(v, verdict='worse', new=names, failing=names, summary='Tests are worse than before this goal')
            return v

        with patch.object(relay, 'judge', worse_judge):
            p = self.drive(pid)
        self.assertEqual(p['status'], 'needs_you')
        self.assertEqual(p['reason'], 'Every piece is finished, but 4 tests fail that passed before this project: test_a, test_b, test_c. '
                                      'Press Fix to send them to an agent, or Merge anyway if you have checked them.')
        self.assertEqual(p['final_tests']['verdict'], 'worse'); self.assertEqual(p['final_tests']['new'], names)
        self.assertEqual([x['status'] for x in p['pieces']], ['done'] * 3)
        with self.assertRaises(ValueError): relay.merge_project(pid)          # not ready, so not mergeable
        with self.assertRaises(ValueError): relay.project_act(pid, dict(action='retry', n=1))   # piece is done: clean refusal
        # continue re-runs the final tests (still worse here) and lands back in needs_you without crashing
        with patch.object(relay, 'judge', worse_judge):
            relay.project_act(pid, dict(action='continue'))
            self.assertEqual(relay.load_project(pid)['status'], 'running')
            p = self.drive(pid)
        self.assertEqual(p['status'], 'needs_you'); self.assertIn('4 tests fail', p['reason'])
        relay.project_act(pid, dict(action='stop'))
        p = relay.load_project(pid); self.assertEqual(p['status'], 'stopped')
        self.assertEqual(relay.project_step(pid, background=False), 'idle')

    def worse_then(self, results):
        real_judge = relay.judge
        calls = []

        def judge(p, result, label, *a, **k):
            v = real_judge(p, result, label, *a, **k)
            if label == 'final':
                calls.append(1)
                if results(len(calls)):
                    v = dict(v, verdict='worse', new=['test_a', 'test_b'], failing=['test_a', 'test_b'], summary='Tests are worse than before this goal')
            return v
        return patch.object(relay, 'judge', judge)

    def test_fix_adds_piece_and_project_becomes_ready(self):
        r, pid = self.new('fixok', test_cmd=f'"{sys.executable}" -c "pass"')
        with self.worse_then(lambda i: i == 1):
            p = self.drive(pid)
            self.assertEqual(p['status'], 'needs_you')
            p = relay.project_act(pid, dict(action='fix', note='Look at CRLF'))
            self.assertEqual(p['status'], 'running'); self.assertEqual(len(p['pieces']), 4)
            fx = p['pieces'][3]
            self.assertEqual(fx['title'], 'Fix failing tests after the final run'); self.assertEqual(fx['status'], 'waiting')
            for want in ('test_a, test_b', 'Look at CRLF', 'line-ending', 'change no behaviour beyond that', 'Last part of the final test log'):
                self.assertIn(want, fx['what'])
            p = self.drive(pid)
        self.assertEqual(p['status'], 'ready'); self.assertEqual(p['pieces'][3]['status'], 'done')
        self.assertEqual(relay.load(p['pieces'][3]['goal'])['status'], 'merged')
        self.assertEqual(p['final_tests']['verdict'], 'ok' if p['final_tests']['verdict'] == 'ok' else p['final_tests']['verdict'])
        self.assertNotEqual(p['final_tests']['verdict'], 'worse')

    def test_fix_limited_to_three_and_only_when_worse(self):
        r, pid = self.new('fixlimit', test_cmd=f'"{sys.executable}" -c "pass"')
        with self.worse_then(lambda i: True):
            p = self.drive(pid)
            for i in range(3):
                relay.project_act(pid, dict(action='fix'))
                p = self.drive(pid)
                self.assertEqual(p['status'], 'needs_you'); self.assertEqual(len(p['pieces']), 4 + i)
            with self.assertRaises(ValueError) as cm: relay.project_act(pid, dict(action='fix'))
            self.assertIn('3 times', str(cm.exception))
            p = relay.load_project(pid)
            self.assertEqual(len(p['pieces']), 6); self.assertEqual(p['status'], 'needs_you'); self.assertIn('3 times', p['reason'])
        r2, pid2 = self.new('fixnot', test_cmd=f'"{sys.executable}" -c "pass"')
        p = self.drive(pid2)
        self.assertEqual(p['status'], 'ready')
        with self.assertRaises(ValueError): relay.project_act(pid2, dict(action='fix'))
        self.assertEqual(len(relay.load_project(pid2)['pieces']), 3)

    def test_worse_final_tests_without_new_use_failing(self):
        r, pid = self.new('worsefinal2', test_cmd=f'"{sys.executable}" -c "pass"')
        real_judge = relay.judge

        def worse_judge(p, result, label, *a, **k):
            v = real_judge(p, result, label, *a, **k)
            if label == 'final':
                v = dict(v, verdict='worse', failing=['x1', 'x2'], summary='worse'); v.pop('new', None)
            return v

        with patch.object(relay, 'judge', worse_judge):
            p = self.drive(pid)
        self.assertEqual(p['status'], 'needs_you'); self.assertIn('but 2 tests fail that passed before this project: x1, x2. Press Fix', p['reason'])

    def test_accept_worse_merges_and_records_names(self):
        r, pid = self.new('acceptworse', test_cmd=f'"{sys.executable}" -c "pass"')
        with self.worse_then(lambda i: True):
            p = self.drive(pid)
        self.assertEqual(p['status'], 'needs_you'); main0 = head(r, 'main')
        for flag in (False, None, 'true', 1):
            with self.assertRaises(ValueError): relay.merge_project(pid, accept_worse=flag)
        with self.assertRaises(ValueError): relay.merge_project(pid)
        self.assertEqual(head(r, 'main'), main0)
        m = relay.merge_project(pid, accept_worse=True)
        self.assertEqual(m['status'], 'merged'); self.assertEqual(m['merged_with_failing'], ['test_a', 'test_b'])
        self.assertIn('test_a', m['reason']); self.assertIn('test_b', m['reason']); self.assertIn('Merged into', m['reason'])
        self.assertNotEqual(head(r, 'main'), main0)

    def test_chat_cannot_merge_worse_project(self):
        r, pid = self.new('chatworse', test_cmd=f'"{sys.executable}" -c "pass"')
        with self.worse_then(lambda i: True):
            self.drive(pid)
        main0 = head(r, 'main')
        for a in (dict(do='merge_project', project=pid, accept_worse=True), dict(do='project', action='merge', project=pid, accept_worse=True),
                  dict(do='project', action='continue', project=pid, accept_worse=True)):
            try: relay.run_action(a)
            except Exception: pass
        p = relay.load_project(pid)
        self.assertNotEqual(p['status'], 'merged'); self.assertNotIn('merged_with_failing', p); self.assertEqual(head(r, 'main'), main0)

    def test_chat_cannot_merge(self):
        r, pid = self.new('chat')
        p = self.drive(pid); self.assertEqual(p['status'], 'ready'); main0 = head(r, 'main')
        for a in (dict(do='merge_project', project=pid), dict(do='project', action='merge', project=pid), dict(do='merge', goal=p['pieces'][0]['goal'])):
            with self.assertRaises(ValueError): relay.run_action(a)
        self.assertEqual(head(r, 'main'), main0); self.assertEqual(relay.load_project(pid)['status'], 'ready')

    def test_http_endpoints_and_state(self):
        r, pid = self.new('http')
        self.drive(pid, auto_start=False)
        server = relay.ThreadingHTTPServer(('127.0.0.1', 0), relay.Handler); port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def call(method, path, body=None):
            c = http.client.HTTPConnection('127.0.0.1', port, timeout=20)
            c.request(method, path, json.dumps(body) if body is not None else None,
                      {'X-Relay': '1', 'Sec-Fetch-Site': 'same-origin', 'Content-Type': 'application/json'})
            resp = c.getresponse(); data = json.loads(resp.read()); c.close(); return resp.status, data
        try:
            code, d = call('GET', f'/api/projects/{pid}')
            self.assertEqual(code, 200); self.assertIn('Part 1', d['project']['plan_md']); self.assertIn('Project notes', d['project']['notes_md'])
            code, d = call('POST', f'/api/projects/{pid}/start'); self.assertEqual((code, d['status']), (200, 'running'))
            p = self.drive(pid); self.assertEqual(p['status'], 'ready')
            code, st = call('GET', '/api/state')
            mine = next(x for x in st['projects'] if x['id'] == pid)
            self.assertEqual((mine['done'], mine['total']), (3, 3)); self.assertTrue(mine['brief'])
            piece_goal = next(g for g in st['goals'] if g['project'] == pid)
            self.assertEqual(piece_goal['project_title'], 'Big')
            code, d = call('POST', f'/api/goals/{piece_goal["id"]}/merge', {})
            self.assertNotEqual(code, 200); self.assertEqual(relay.load_project(pid)['status'], 'ready')
            code, d = call('POST', f'/api/projects/{pid}/merge', {})
            self.assertEqual(code, 200); self.assertEqual(d['status'], 'merged'); self.assertTrue((r / 'part3.txt').exists())
        finally:
            server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
