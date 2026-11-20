"""Node test names, and count fallback when the failing names cannot be read."""
import unittest

import _isolate  # noqa: F401
import relay

NODE = '\n'.join([
    '\u2716 failing tests:',
    '\u2716 tests\\hydra.test.cjs (217.7076ms)',
    '\u2716 approved static room is published before delayed character decode, then attached to the same renderer (10010.5619ms)',
])
NODE_NAMES = [
    'approved static room is published before delayed character decode, then attached to the same renderer',
    'tests\\hydra.test.cjs',
]


class Brief18Tests(unittest.TestCase):
    def test_node_failing_names_drop_the_header_and_the_duration(self):
        self.assertEqual(relay.failing_names(NODE), NODE_NAMES)
        other = NODE.replace('217.7076ms', '9ms').replace('10010.5619ms', '3.5s')
        self.assertNotEqual(other, NODE)
        self.assertEqual(relay.failing_names(other), relay.failing_names(NODE))

    def test_tap_line_drops_a_trailing_duration(self):
        self.assertEqual(relay.failing_names('not ok 3 - some test (12.5ms)'), ['some test'])

    def test_counts_from_each_runner_or_nothing(self):
        self.assertEqual(relay.test_counts('# pass 40\n# fail 2\n# cancelled 1'), {'fail': 3, 'pass': 40})
        self.assertEqual(relay.test_counts('Ran 10 tests in 1s\n\nFAILED (failures=1, errors=2)'), {'fail': 3, 'pass': 7})
        self.assertEqual(relay.test_counts('===== 2 failed, 8 passed in 1.2s ====='), {'fail': 2, 'pass': 8})
        self.assertIsNone(relay.test_counts('hello'))

    def test_judge_compares_counts_when_no_names_were_read(self):
        goal = dict(baseline=dict(exit=1, failing=[], counts={'fail': 2, 'pass': 10}), test_cmd='x')
        now = dict(exit=1, timed_out=False, failing=[], counts={'fail': 2, 'pass': 10}, tail='', log='x')
        same = relay.judge(goal, now, 't')
        self.assertEqual(same['verdict'], 'no_worse')
        self.assertIn('names could not be read', same['summary'])
        self.assertEqual(same['exit'], 1)
        self.assertEqual(same['log'], 'x')
        worse = relay.judge(goal, dict(now, counts={'fail': 3, 'pass': 10}), 't')
        self.assertEqual(worse['verdict'], 'worse')
        bare = dict(baseline=dict(exit=1, failing=[]), test_cmd='x')
        self.assertEqual(relay.judge(bare, dict(exit=1, timed_out=False, failing=[], tail='', log='x'), 't')['verdict'], 'unknown')


if __name__ == '__main__':
    unittest.main()
