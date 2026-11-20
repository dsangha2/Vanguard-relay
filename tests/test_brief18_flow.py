"""A finished goal whose tests could not be compared, and a project piece waiting to be accepted."""
import subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import _isolate
TMP = tempfile.mkdtemp()
import relay

FAKE = {'exe': [sys.executable], 'stdin': True, 'argv': ['{exe}', str(ROOT / 'tests/fake_agent.py'), '{model}'],
        'billing': {'guarded': True, 'note': 'test'}}
BUILT = {'built': ['The room shows before the character'], 'not_tested': ['A second browser'], 'watch_out': []}


def our_config():
    return {'projects_root': TMP, 'coordinator': 'lead',
            'providers': {**{k: {'exe': ['relay-test-provider-not-installed']} for k in relay.DEFAULT_CONFIG['providers']}, 'fake': FAKE},
            'agents': {'lead': {'provider': 'fake', 'model': 'lead', 'tier': 1}}}


def repo(name):
    r = Path(TMP) / name
    r.mkdir()
    for a in (['init', '-q', '-b', 'main'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'base']):
        subprocess.run(['git', '-C', str(r), *a], check=True)
    return r


class Brief18Flow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._cfg = (relay.DATA / 'config.json').read_bytes() if (relay.DATA / 'config.json').exists() else None
        relay.write_json(relay.DATA / 'config.json', our_config())

    @classmethod
    def tearDownClass(cls):
        path = relay.DATA / 'config.json'
        if cls._cfg is not None:
            path.write_bytes(cls._cfg)
        elif path.exists():
            path.unlink()

    def waiting(self, name):
        g = relay.create_goal(dict(objective='Show the room before the character arrives', repo=str(repo(name)), lead='lead', title='Room'))
        tail = '\n'.join('log %d' % i for i in range(45))
        g.update(status='needs_you', reason=relay.UNCONFIRMED, summary=dict(BUILT),
                 unconfirmed=dict(turn=1, head=relay.work_head(g['work'])),
                 turns=[dict(n=1, agent='lead', status='ok', final='The room is in place',
                             tests=dict(verdict='unknown', summary='names could not be read', tail=tail))])
        relay.save(g)
        return g

    def test_decide_unknown_records_the_summary_and_stops(self):
        r = repo('decide')
        goal = dict(lead='lead', team=['lead'], turns=[], work=str(r), question=dict(agent='lead', text='old'))
        turn = dict(n=3, agent='lead', status='ok', final='finished',
                    proposal=dict(next='done', to='', note='', summary=dict(BUILT)),
                    tests=dict(verdict='unknown', summary='could not name them'))
        self.assertEqual(relay.decide(goal, turn), ('needs_you', 'lead', '', relay.UNCONFIRMED))
        self.assertEqual(goal['summary'], BUILT)
        self.assertEqual(goal['unconfirmed'], dict(turn=3, head=relay.work_head(str(r))))
        self.assertNotIn('question', goal)
        broken = dict(lead='lead', team=['lead'], turns=[], work='C:/no/such/worktree')
        with patch.object(relay, 'git', side_effect=OSError('git failed')):
            relay.decide(broken, turn)
        self.assertEqual(broken['unconfirmed']['head'], '')

    def test_continue_does_nothing_until_there_is_a_note(self):
        g = self.waiting('cont')
        with self.assertRaises(ValueError) as cm:
            relay.act(g['id'], {'action': 'continue'})
        self.assertEqual(str(cm.exception), relay.NOTHING_TO_CONTINUE)
        kept = relay.load(g['id'])
        self.assertEqual(kept['status'], 'needs_you')
        self.assertIn('unconfirmed', kept)
        queued = relay.act(g['id'], {'action': 'continue', 'note': 'change X'})
        self.assertEqual(queued['status'], 'queued')
        self.assertNotIn('unconfirmed', queued)
        self.assertIn('change X', queued['next']['note'])
        self.assertNotIn('unconfirmed', relay.load(g['id']))

    def test_mark_done_keeps_the_lead_summary(self):
        g = self.waiting('done')
        out = relay.act(g['id'], {'action': 'done'})
        self.assertEqual(out['status'], 'ready')
        self.assertEqual(out['summary'], BUILT)
        self.assertNotIn('fallback', out['summary'])
        self.assertNotIn('unconfirmed', out)
        self.assertNotIn('unconfirmed', relay.load(g['id']))

    def test_summary_shows_the_brief_while_unconfirmed(self):
        g = self.waiting('sum')
        view = relay.summary(relay.load(g['id']))
        self.assertEqual(view['summary'], BUILT)
        self.assertTrue(view['unconfirmed'])
        lines = view['tests_tail'].splitlines()
        self.assertEqual(lines, ['log %d' % i for i in range(5, 45)])
        self.assertIn('press Mark done, then Merge', relay.COORD_RULES)
        self.assertIn('Accept piece', relay.COORD_RULES)
        self.assertLess(relay.COORD_RULES.index('already failing before'), relay.COORD_RULES.index('\nRules:'))

    def test_project_piece_can_be_accepted(self):
        r = repo('piece')
        p = relay.create_project(dict(title='Rooms', objective='Build the room in pieces that follow each other', repo=str(r), lead='lead', test_cmd=''))
        g = self.waiting('piece-goal')
        g.update(project=p['id'], piece=1)
        relay.save(g)
        p = relay.load_project(p['id'])
        p.update(status='running', started=1, pieces=[dict(n=1, title='First room', what='Publish the room.', status='running',
                                                           depends_on=[], goal=g['id'], goals=[g['id']])])
        relay.save_project(p)
        self.assertEqual(relay.piece_progress(p, p['pieces'][0]), 'needs_you')
        p = relay.load_project(p['id'])
        self.assertEqual(p['status'], 'needs_you')
        self.assertIn(relay.UNCONFIRMED_PIECE, p['reason'])
        self.assertIn('Piece 1 (First room):', p['reason'])
        self.assertEqual(p['accept'], 1)
        self.assertEqual(p['pieces'][0]['status'], 'failed')
        self.assertEqual(relay.project_view(p)['accept'], 1)
        out = relay.project_act(p['id'], dict(action='accept', n=1))
        self.assertEqual(out['status'], 'running')
        self.assertEqual(out['reason'], '')
        self.assertEqual(out['pieces'][0]['status'], 'running')
        self.assertIsNone(out.get('accept'))
        goal = relay.load(g['id'])
        self.assertEqual(goal['status'], 'ready')
        self.assertEqual(goal['reason'], 'You accepted this piece')
        self.assertNotIn('unconfirmed', goal)
        with self.assertRaises(ValueError) as cm:
            relay.run_action(dict(do='project', project=p['id'], action='accept', n=1))
        self.assertIn('coordinator', str(cm.exception))

    def test_stop_clears_accept(self):
        r = repo('clear')
        p = relay.create_project(dict(title='Rooms', objective='Build the room in pieces that follow each other', repo=str(r), lead='lead', test_cmd=''))
        p = relay.load_project(p['id'])
        p.update(status='needs_you', accept=2, pieces=[dict(n=2, title='Second', what='Leave it.', status='failed', depends_on=[])])
        relay.save_project(p)
        out = relay.project_act(p['id'], dict(action='stop'))
        self.assertEqual(out['status'], 'stopped')
        self.assertNotIn('accept', out)

    def test_finish_piece_merges_an_unknown_verdict(self):
        p = dict(id='p12345678', work='w', branch='relay/project-p12345678')
        piece = dict(n=1, title='First room', status='running')
        g = dict(id='g12345678', branch='relay/g12345678',
                 turns=[dict(tests=dict(verdict='unknown', summary='names missing'))], summary=dict(BUILT))
        with patch.object(relay, 'auto_merge') as merge, patch.object(relay, 'append_notes'), \
                patch.object(relay, 'update'), patch.object(relay, 'save_project'), patch.object(relay, 'git', return_value='abc'):
            self.assertEqual(relay.finish_piece(p, piece, g), 'merged')
        merge.assert_called_once()
        self.assertEqual(piece['status'], 'done')


if __name__ == '__main__':
    unittest.main()
