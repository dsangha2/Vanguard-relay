"""The repo guide reaches the lead, helper, short-brief, planning and pick_team prompts; no guide means no section; default test command."""
import shutil, subprocess, tempfile, unittest
from pathlib import Path
from unittest import mock

import _isolate  # noqa: F401
import relay

HEAD = 'STANDING RULES FOR THIS REPO (from the user, always follow)'


class GuidePrompts(unittest.TestCase):
    def setUp(self):
        self.addCleanup(shutil.rmtree, relay.guides_dir(), True)
        shutil.rmtree(relay.guides_dir(), True)
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / 'guided'; self.repo.mkdir()
        for a in (['init'], ['config', 'user.email', 'a@b.c'], ['config', 'user.name', 'x'], ['commit', '--allow-empty', '-m', 'i']):
            subprocess.run(['git', '-C', str(self.repo), *a], check=True, capture_output=True)
        agents = list(relay.config()['agents'])
        if len(agents) < 2: self.skipTest('needs two agents')
        self.lead, self.helper = agents[0], agents[1]
        for name in ('blocked_reason', 'limited_reason'):
            p = mock.patch.object(relay, name, lambda a: None); p.start(); self.addCleanup(p.stop)

    def goal(self):
        return dict(id='gtest', branch='relay/gtest', objective='fix the bug', team=[self.lead, self.helper], lead=self.lead,
                    repo=str(self.repo), test_cmd='', baseline=None, turns=[], notes=[], attachments=[])

    def brief(self, agent, short=False, resumed=False):
        cfg = relay.config(); provider = cfg['agents'][agent]['provider']
        cfg['providers'].setdefault(provider, {})['short_brief'] = short
        with mock.patch.object(relay, 'config', lambda: cfg):
            return relay.brief(self.goal(), agent, 'do the piece', resumed)

    def test_guide_in_lead_and_helper_before_task(self):
        relay.save_guide('guided', 'Never touch vendor/.')
        for agent in (self.lead, self.helper):
            text = self.brief(agent)
            self.assertIn(HEAD + '\nNever touch vendor/.', text)
            self.assertLess(text.index(HEAD), text.index('GOAL\nfix the bug'))
        self.assertIn(HEAD, self.brief(self.helper, resumed=True))

    def test_guide_in_short_brief(self):
        relay.save_guide('guided', 'Rule X')
        text = self.brief(self.lead, short=True)
        self.assertLess(text.index(HEAD), text.index('TASK\n'))

    def test_no_guide_no_section(self):
        for text in (self.brief(self.lead), self.brief(self.helper), self.brief(self.lead, short=True),
                     relay.plan_prompt(dict(title='t', objective='o', repo=str(self.repo), base_branch='main', test_cmd=''))):
            self.assertNotIn('STANDING RULES', text)

    def test_plan_prompt_has_guide(self):
        relay.save_guide('guided', 'Plan rule')
        text = relay.plan_prompt(dict(title='t', objective='o', repo=str(self.repo), base_branch='main', test_cmd=''))
        self.assertIn(HEAD + '\nPlan rule', text)

    def test_pick_team_gets_guide(self):
        relay.save_guide('guided', 'Team rule')
        seen = []
        with mock.patch.object(relay, 'coordinator', lambda: 'x'), \
             mock.patch.object(relay, 'chat_call', lambda n, prompt, *a, **k: (seen.append(prompt), '', 'err')[1:]):
            relay.pick_team('Do a thing', self.repo)
        self.assertTrue(seen and 'Team rule' in seen[0])
        seen.clear(); shutil.rmtree(relay.guides_dir(), True)
        with mock.patch.object(relay, 'coordinator', lambda: 'x'), \
             mock.patch.object(relay, 'chat_call', lambda n, prompt, *a, **k: (seen.append(prompt), '', 'err')[1:]):
            relay.pick_team('Do a thing', self.repo)
        self.assertTrue(seen and 'repo_guide' not in seen[0])

    def test_default_test_command(self):
        relay.save_guide('guided', 'x\nTEST COMMAND: python -m unittest\n')
        body = dict(objective='Do a thing', repo=str(self.repo), lead=self.lead, team=[self.lead])
        g = relay.create_goal(dict(body)); self.assertEqual(g['test_cmd'], 'python -m unittest')
        g2 = relay.create_goal(dict(body, test_cmd='pytest')); self.assertEqual(g2['test_cmd'], 'pytest')
        self.addCleanup(lambda: [subprocess.run(['git', '-C', str(self.repo), 'worktree', 'prune'], capture_output=True)])
        with mock.patch.object(relay, 'git', lambda *a, **k: 'main'), mock.patch.object(relay, 'save_project', lambda p: None), \
             mock.patch.object(relay, 'write_text', lambda *a, **k: None):
            p = relay.create_project(dict(objective='Big thing', repo=str(self.repo)))
        self.assertEqual(p['test_cmd'], 'python -m unittest')


if __name__ == '__main__':
    unittest.main()
