"""Continue a finished project: a new project carries the repo, notes and piece list, and never branches from unmerged work."""
import http.client, json, os, subprocess, sys, tempfile, threading, unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import _isolate
TMP = tempfile.mkdtemp()
import relay

FAKE = {'exe': [sys.executable], 'stdin': True, 'chat_argv': ['{exe}', str(ROOT / 'tests/fake_agent.py'), '{model}'], 'argv': ['{exe}', str(ROOT / 'tests/fake_agent.py'), '{model}'], 'resume': ['--resume', '{session}'],
        'billing': {'guarded': True, 'note': 'test'}, 'limit': {'patterns': [r"(?i)hit your[\w\s-]{0,30}limit"], 'minutes': 60}}
HTML = (ROOT / 'web' / 'index.html').read_text(encoding='utf-8')


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


def commit_file(folder, name, text, msg):
    Path(folder, name).write_text(text, encoding='utf-8')
    subprocess.run(['git', '-C', str(folder), 'add', name], check=True)
    subprocess.run(['git', '-C', str(folder), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', msg], check=True)


def frozen(pid):
    p = relay.load_project(pid)
    p.pop('updated', None)
    return p


class FollowupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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

    def new(self, name, **kw):
        r = repo(name)
        body = dict(title='Big', objective='Build three parts that follow each other', repo=str(r), lead='planner', mode='manual', test_cmd='echo ok')
        body.update(kw)
        return r, relay.create_project(body)['id']

    def settle(self, pid, until_piece=None, limit=60):
        for _ in range(limit):
            p = relay.load_project(pid)
            if p['status'] == 'plan_ready':
                relay.start_project(pid)
                continue
            if until_piece and any(x['n'] == until_piece and x['status'] == 'done' for x in p['pieces']):
                return p
            if not until_piece and p['status'] in ('ready', 'needs_you', 'stopped', 'merged'):
                return p
            if p['status'] in ('needs_you', 'stopped', 'merged'):
                return p
            relay.project_step(pid, background=False)
            for g in relay.all_goals():
                if g['status'] == 'queued':
                    relay.run_goal(g['id'])
        self.fail('project did not settle: ' + relay.load_project(pid)['status'] + ' ' + relay.load_project(pid).get('reason', ''))

    def first_goal(self, pid):
        for _ in range(40):
            p = relay.load_project(pid)
            if p['pieces'] and p['pieces'][0].get('goal'):
                return relay.load(p['pieces'][0]['goal'])
            if p['status'] == 'plan_ready':
                relay.start_project(pid)
                continue
            if p['status'] in ('needs_you', 'stopped'):
                self.fail(p['reason'])
            relay.project_step(pid, background=False)
        self.fail('no first piece goal')

    def test_merged_followup_carries_repo_notes_and_base(self):
        r, pid = self.new('merged')
        old = self.settle(pid)
        self.assertEqual(old['status'], 'ready')
        relay.merge_project(pid)
        work = Path(old['work'])
        commit_file(work, 'only-on-project.txt', 'later\n', 'after merge')
        subprocess.run(['git', '-C', str(r), 'checkout', '-q', '-b', 'decoy'], check=True)
        commit_file(r, 'decoy.txt', 'nope\n', 'decoy')
        before, notes, plan = frozen(pid), relay.read_notes(pid), (relay.project_path(pid) / 'plan.md').read_text(encoding='utf-8')
        new = relay.followup_project(pid, dict(objective='Add a fourth part after the merge'))
        self.assertNotEqual(new['id'], pid)
        self.assertEqual(new['title'], 'Big (continued)')
        self.assertEqual(new['repo'], old['repo'])
        self.assertEqual(new['test_cmd'], 'echo ok')
        self.assertEqual(new['lead'], 'planner')
        self.assertEqual(new['mode'], 'manual')
        self.assertFalse(new['auto_start'])
        self.assertEqual(new['base_branch'], 'main')
        self.assertNotEqual(new['base_branch'], old['branch'])
        self.assertEqual(new['continues'], pid)
        self.assertFalse(new.get('notice'))
        self.assertNotIn('not included', new.get('reason') or '')
        carried = relay.read_notes(new['id'])
        self.assertTrue(carried.startswith(f"## Carried over from Big ({pid})"))
        self.assertLessEqual(len(carried), relay.NOTES_LIMIT)
        self.assertIn('## Piece 1:', carried)
        self.assertIn('name chosen: part1', carried)
        prompt = relay.plan_prompt(relay.load_project(new['id']))
        for piece in old['pieces']:
            self.assertIn(f"{piece['n']}. {piece['title']} ({piece['status']})", prompt)
        self.assertIn('FINAL BRIEF', prompt)
        self.assertIn('What was built: Big', prompt)
        self.assertIn('Do not plan them again', prompt)
        relay.ensure_project_branch(relay.load_project(new['id']))
        self.assertEqual(head(r, new['branch']), head(r, 'main'))
        self.assertNotEqual(head(r, new['branch']), head(r, old['branch']))
        self.assertNotEqual(head(r, new['branch']), head(r, 'decoy'))
        new_work = Path(new['work'])
        self.assertTrue((new_work / 'part1.txt').is_file())
        self.assertFalse((new_work / 'only-on-project.txt').exists())
        self.assertFalse((new_work / 'decoy.txt').exists())
        goal = self.first_goal(new['id'])
        self.assertIn(f'## Carried over from Big ({pid})', goal['objective'])
        self.assertIn('name chosen: part1', goal['objective'])
        after = frozen(pid)
        self.assertEqual(after.pop('continued_by'), [new['id']])
        self.assertEqual(before, after)
        self.assertEqual(relay.read_notes(pid), notes)
        self.assertEqual((relay.project_path(pid) / 'plan.md').read_text(encoding='utf-8'), plan)

    def test_long_notes_keep_the_newest_part(self):
        r, pid = self.new('long', test_cmd='')
        p = relay.load_project(pid)
        p['status'] = 'merged'
        relay.save_project(p)
        body = 'OLD-START\n' + ('line of older notes\n' * 800) + 'RECENT-END-MARKER\n'
        relay.write_text(relay.project_path(pid) / 'notes.md', body)
        new = relay.followup_project(pid, dict(objective='Carry the long notes into a new project'))
        text = relay.read_notes(new['id'])
        self.assertLessEqual(len(text), relay.NOTES_LIMIT)
        self.assertTrue(text.startswith('The start of these notes was cut to fit.'))
        self.assertIn(f'data/projects/{pid}/notes.md', text.split('## Carried over')[0])
        self.assertIn(f'## Carried over from Big ({pid})', text)
        self.assertIn('RECENT-END-MARKER', text)
        self.assertNotIn('OLD-START', text)
        obj = relay.piece_objective(relay.load_project(new['id']), dict(n=1, title='Next', what='Do the next part.'))
        self.assertIn('RECENT-END-MARKER', obj)
        self.assertIn('was cut to fit', obj)
        self.assertNotIn('OLD-START', obj)

    def test_ready_and_stopped_do_not_include_unmerged_work(self):
        r, pid = self.new('ready', test_cmd='')
        old = self.settle(pid)
        self.assertEqual(old['status'], 'ready')
        self.assertNotEqual(head(r, 'main'), head(r, old['branch']))
        subprocess.run(['git', '-C', str(r), 'checkout', '-q', '-b', 'decoy'], check=True)
        commit_file(r, 'decoy.txt', 'nope\n', 'decoy')
        new = relay.followup_project(pid, dict(objective='Add the next part once the old one is merged'))
        self.assertEqual(new['notice'], relay.UNMERGED_NOTE)
        self.assertEqual(new['reason'], relay.UNMERGED_NOTE)
        self.assertEqual(new['base_branch'], 'main')
        relay.ensure_project_branch(relay.load_project(new['id']))
        self.assertEqual(head(r, new['branch']), head(r, 'main'))
        self.assertNotEqual(head(r, new['branch']), head(r, old['branch']))
        self.assertNotEqual(head(r, new['branch']), head(r, 'decoy'))
        self.assertFalse((Path(new['work']) / 'part1.txt').exists())
        self.assertTrue((Path(old['work']) / 'part1.txt').is_file())

        r2, pid2 = self.new('stopped', test_cmd='')
        mid = self.settle(pid2, until_piece=1)
        self.assertEqual(mid['pieces'][0]['status'], 'done')
        stopped = relay.project_act(pid2, dict(action='stop'))
        self.assertEqual(stopped['status'], 'stopped')
        subprocess.run(['git', '-C', str(r2), 'checkout', '-q', '-b', 'decoy'], check=True)
        commit_file(r2, 'decoy.txt', 'nope\n', 'decoy')
        before = frozen(pid2)
        nxt = relay.followup_project(pid2, dict(objective='Add a part after the stopped one', title='After the stop'))
        self.assertEqual(nxt['title'], 'After the stop')
        self.assertEqual(nxt['notice'], relay.UNMERGED_NOTE)
        self.assertEqual(nxt['base_branch'], mid['base_branch'])
        self.assertNotEqual(nxt['base_branch'], mid['branch'])
        relay.ensure_project_branch(relay.load_project(nxt['id']))
        self.assertEqual(head(r2, nxt['branch']), head(r2, mid['base_branch']))
        self.assertNotEqual(head(r2, nxt['branch']), head(r2, mid['branch']))
        self.assertFalse((Path(nxt['work']) / 'part1.txt').exists())
        after = frozen(pid2)
        self.assertEqual(after.pop('continued_by'), [nxt['id']])
        self.assertEqual(before, after)

    def test_running_and_empty_are_refused(self):
        r, pid = self.new('running', test_cmd='')
        self.settle(pid, until_piece=1)
        p = relay.load_project(pid)
        self.assertEqual(p['status'], 'running')
        ids = {x['id'] for x in relay.all_projects()}
        with self.assertRaises(ValueError) as cm:
            relay.followup_project(pid, dict(objective='Keep going while it is still working'))
        self.assertEqual(str(cm.exception), relay.FOLLOWUP_BLOCKED)
        self.assertNotIn('Traceback', str(cm.exception))
        self.assertEqual({x['id'] for x in relay.all_projects()}, ids)
        fresh = relay.create_project(dict(title='Still planning', objective='Build three parts that follow each other', repo=str(r), lead='planner'))
        with self.assertRaises(ValueError) as cm:
            relay.followup_project(fresh['id'], dict(objective='Too soon to continue this one'))
        self.assertEqual(str(cm.exception), relay.FOLLOWUP_BLOCKED)
        relay.project_act(pid, dict(action='stop'))
        for blank in ('', '   ', None):
            with self.assertRaises(ValueError) as cm:
                relay.followup_project(pid, dict(objective=blank))
            self.assertEqual(str(cm.exception), relay.FOLLOWUP_EMPTY)
        self.assertEqual(relay.load_project(pid)['status'], 'stopped')

    def test_state_and_http_return_the_links(self):
        r, pid = self.new('http', test_cmd='echo ok')
        p = relay.load_project(pid)
        p['status'] = 'ready'
        p['pieces'] = [dict(n=1, title='Part 1', what='Create part1.txt.', depends_on=[], goal=None, goals=[], status='done')]
        relay.save_project(p)
        relay.write_text(relay.project_path(pid) / 'notes.md', '# Project notes: Big\n\nKeep the name part1.\n')
        server = relay.ThreadingHTTPServer(('127.0.0.1', 0), relay.Handler)
        port = server.server_address[1]
        self.assertNotEqual(port, 8940)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def call(method, path, body=None):
            c = http.client.HTTPConnection('127.0.0.1', port, timeout=20)
            c.request(method, path, json.dumps(body) if body is not None else None,
                      {'X-Relay': '1', 'Sec-Fetch-Site': 'same-origin', 'Content-Type': 'application/json'})
            resp = c.getresponse(); data = json.loads(resp.read()); c.close(); return resp.status, data
        try:
            code, denied = call('POST', f'/api/projects/{pid}/followup', {'objective': '   '})
            self.assertEqual(code, 400)
            self.assertEqual(denied['error'], relay.FOLLOWUP_EMPTY)
            code, made = call('POST', f'/api/projects/{pid}/followup', {'objective': 'Add the next part of the work'})
            self.assertEqual(code, 200)
            self.assertEqual(made['continues'], pid)
            self.assertEqual(made['base_branch'], 'main')
            self.assertEqual(made['test_cmd'], 'echo ok')
            self.assertEqual(made['notice'], relay.UNMERGED_NOTE)
            self.assertFalse(made['auto_start'])
            code, one = call('GET', f'/api/projects/{made["id"]}')
            self.assertEqual(one['project']['continues'], pid)
            self.assertEqual(one['project']['continued_by'], [])
            code, old = call('GET', f'/api/projects/{pid}')
            self.assertEqual(old['project']['continued_by'], [made['id']])
            self.assertIsNone(old['project']['continues'])
            code, st = call('GET', '/api/state')
            by_id = {x['id']: x for x in st['projects']}
            self.assertEqual(by_id[made['id']]['continues'], pid)
            self.assertEqual(by_id[pid]['continued_by'], [made['id']])
            self.assertIn('Keep the name part1.', by_id[made['id']] and relay.read_notes(made['id']))
            again = relay.followup_project(pid, dict(objective='And a second follow-up after that', title='Second'))
            self.assertEqual(relay.load_project(pid)['continued_by'], [made['id'], again['id']])
            self.assertEqual(again['title'], 'Second')
        finally:
            server.shutdown(); server.server_close()

    def test_chat_followup_works_and_still_cannot_merge(self):
        r, pid = self.new('chat', test_cmd='')
        p = relay.load_project(pid)
        p['status'] = 'merged'
        relay.save_project(p)
        main0 = head(r, 'main')
        result = relay.run_action(dict(do='project', project=pid, action='followup', objective='Add the feature the merge left out'))
        self.assertTrue(result['ok'])
        self.assertIn('Start', result['text'])
        self.assertNotIn('not included', result['text'])
        made = relay.load_project(result['project'])
        self.assertEqual(made['continues'], pid)
        self.assertFalse(made['auto_start'])
        self.assertEqual(head(r, 'main'), main0)
        for a in (dict(do='merge_project', project=pid), dict(do='project', action='merge', project=pid), dict(do='merge', goal='g00000000')):
            with self.assertRaises(ValueError) as cm:
                relay.run_action(a)
            self.assertIn('never merges', str(cm.exception))
        self.assertEqual(head(r, 'main'), main0)
        self.assertEqual(relay.load_project(pid)['status'], 'merged')
        asked = []

        def fake_call(name, prompt, session, **k):
            asked.append(prompt)
            return 'Continuing it.\nACTIONS: ' + json.dumps([{'do': 'project', 'project': pid, 'action': 'followup', 'objective': 'Extend the finished project with one more part'}]), None, None
        with mock.patch.object(relay, 'coordinator', return_value='planner'), mock.patch.object(relay, 'chat_call', side_effect=fake_call):
            relay.chat_run('please continue that finished project')
        msg = relay.chat_load()['messages'][-1]
        self.assertTrue(msg['actions'][0]['ok'])
        self.assertIn('Start', msg['actions'][0]['text'])
        self.assertEqual(relay.load_project(msg['actions'][0]['project'])['continues'], pid)
        self.assertIn('When the person asks to continue, extend or add to a finished project, use followup instead of create_project.\n'
                      'Tell them the new project\'s plan needs their Start.', relay.COORD_RULES)
        self.assertIn('"action": "<start|stop|continue|skip|retry|fix|followup>"', relay.COORD_RULES)
        self.assertLess(relay.COORD_RULES.index('use followup instead of create_project'), relay.COORD_RULES.index('\nRules:'))

    def test_page_offers_continue_on_a_finished_project(self):
        self.assertIn("canFollow = ['merged', 'ready', 'stopped'].includes(f.status)", HTML)
        self.assertIn("pb('followup', 'Continue this project')", HTML)
        self.assertIn('placeholder="What should happen next?"', HTML)
        self.assertIn('class="note" id="pj-follow"', HTML)
        self.assertIn('>Plan it</button>', HTML)
        self.assertIn("/followup", HTML)
        self.assertIn('Continues:', HTML)
        self.assertIn('Continued by:', HTML)
        self.assertIn('data-pj-sel=', HTML)
        self.assertLess(HTML.index("a === 'followup'"), HTML.index("a === 'followup-go'"))
        self.assertIn('created.id', HTML)


if __name__ == '__main__':
    unittest.main()
