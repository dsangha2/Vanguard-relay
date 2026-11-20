"""A moved base is tested with the goal before anything reaches that branch."""
import unittest
from pathlib import Path
from unittest.mock import patch

import test_relay as base          # sets up the fake agents and the temp data folder
from test_relay import relay, repo, run

TEST = base.TEST


class MergeCheck(unittest.TestCase):
    def ready(self, name):
        r = repo(name)
        # A divergent merge needs an identity, independent of the machine's Git config.
        relay.git(r, 'config', 'user.name', 'test')
        relay.git(r, 'config', 'user.email', 'test@localhost')
        g = run(relay.create_goal(dict(objective='Add the feature file', repo=str(r),
                                       lead='lead', team=['helper'], test_cmd=TEST)))
        self.assertEqual(g['status'], 'ready')
        self.assertTrue(all(t['base_tested'] == g['base_sha'] for t in g['turns']))
        return r, g

    def advance(self, r, filename):
        (r / filename).write_text('base branch moved', encoding='utf-8')
        relay.git(r, 'add', filename)
        relay.git(r, 'commit', '-q', '-m', 'Move the base')
        return relay.git(r, 'rev-parse', 'HEAD')

    def assert_cleaned(self, r, gid):
        tmp = relay.DATA / 'merge-check' / gid
        self.assertFalse(tmp.exists())
        self.assertNotIn(tmp.as_posix(), relay.git(r, 'worktree', 'list', '--porcelain'))
        self.assertEqual(relay.load(gid)['phase'], '')

    def test_moved_base_combination_no_worse_merges(self):
        r, g = self.ready('merge-check-pass')
        head = self.advance(r, 'other.txt')
        real_tests = relay.run_tests

        def check_combination(goal, label):
            self.assertEqual(label, 'merge-check')
            self.assertEqual(relay.summary(relay.load(g['id']))['phase'], relay.MERGE_PHASE)
            self.assertEqual(Path(goal['work']), relay.DATA / 'merge-check' / g['id'])
            self.assertTrue((Path(goal['work']) / 'feature.txt').exists())
            self.assertTrue((Path(goal['work']) / 'other.txt').exists())
            self.assertEqual(relay.git(r, 'rev-parse', 'HEAD'), head)
            self.assertFalse((r / 'feature.txt').exists())
            return real_tests(goal, label)

        with patch.object(relay, 'run_tests', side_effect=check_combination) as tests:
            merged = relay.merge_goal(g['id'])
        self.assertEqual(tests.call_count, 1)
        self.assertEqual(merged['status'], 'merged')
        self.assertEqual(merged['merge_check']['base'], head)
        self.assertEqual(merged['merge_check']['verdict'], 'no_worse')
        self.assertTrue((r / 'feature.txt').exists())
        self.assertTrue((r / 'other.txt').exists())
        self.assert_cleaned(r, g['id'])

    def test_moved_base_new_failure_refuses_and_leaves_base_unchanged(self):
        r, g = self.ready('merge-check-fail')
        head = self.advance(r, 'broken.flag')
        with patch.object(relay, 'run_tests', wraps=relay.run_tests) as tests:
            with self.assertRaises(ValueError) as cm:
                relay.merge_goal(g['id'])
        self.assertEqual([c.args[1] for c in tests.call_args_list], ['merge-check', 'merge-check-rerun'])
        self.assertIn('test_new (t.T)', str(cm.exception))
        self.assertIn('Press Continue', str(cm.exception))
        self.assertIn('main is unchanged', str(cm.exception))
        self.assertEqual(relay.git(r, 'rev-parse', 'HEAD'), head)
        self.assertEqual(relay.git(r, 'status', '--porcelain'), '')
        self.assertFalse((r / 'feature.txt').exists())
        self.assertTrue((r / 'broken.flag').exists())
        refused = relay.load(g['id'])
        self.assertEqual(refused['status'], 'ready')
        self.assertEqual(refused['reason'], str(cm.exception))
        self.assertEqual(refused['merge_check']['new'], ['test_new (t.T)'])
        self.assertEqual(refused['merge_check']['base'], head)
        self.assert_cleaned(r, g['id'])

    def test_unchanged_base_does_not_run_extra_tests(self):
        r, g = self.ready('merge-check-same')
        with patch.object(relay, 'test_merge', wraps=relay.test_merge) as merge_test, \
                patch.object(relay, 'run_tests', wraps=relay.run_tests) as tests:
            merged = relay.merge_goal(g['id'])
        merge_test.assert_not_called()
        tests.assert_not_called()
        self.assertEqual(merged['status'], 'merged')
        self.assertTrue((r / 'feature.txt').exists())
        self.assertNotIn('merge_check', merged)
        self.assert_cleaned(r, g['id'])


if __name__ == '__main__':
    unittest.main()
