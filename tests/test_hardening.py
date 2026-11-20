"""Failed checkpoints preserve edits, and a durable Stop wins over worker completion."""
import sys, threading, time, unittest
from pathlib import Path
from unittest.mock import patch

import test_relay as base          # sets up the fake agents and the temp data folder
from test_relay import relay, repo, run

# Leaves a stray file behind, like a test that writes scratch output; fails so the baseline is simple.
LEAVE = f'"{sys.executable}" -c "open(\'leftover.tmp\', \'w\').write(\'x\')"'
real_git = relay.git


def failing_commit(cwd, *args, **kw):
    if 'commit' in args:
        raise RuntimeError('git -c user.name=Vanguard Relay: fatal: could not lock config file')
    return real_git(cwd, *args, **kw)


class FailedCheckpoint(unittest.TestCase):
    def test_commit_failure_keeps_edits_skips_tests_and_asks_dj(self):
        g = relay.create_goal(dict(objective='Break things', repo=str(repo('h1')), lead='breaker', test_cmd=LEAVE))
        with patch.object(relay, 'git', failing_commit):
            g = run(g)
        work = Path(g['work'])
        self.assertTrue((work / 'broken.flag').is_file())            # the edit is still on disk
        turn = g['turns'][0]
        self.assertEqual(turn['status'], 'failed')
        self.assertIsNone(turn['tests'])                              # no tests ran
        self.assertFalse((relay.goal_path(g['id']) / 'tests-turn-1.log').exists())
        self.assertEqual(g['status'], 'needs_you')
        self.assertTrue(g['reason'].startswith("Vanguard could not save this turn's changes (fatal: could not lock config file)."), g['reason'])
        self.assertIn(f'The files are still in the working folder {g["work"]}. Nothing was lost.', g['reason'])
        self.assertIn('could not lock config file', (relay.goal_path(g['id']) / turn['log']).read_text(encoding='utf-8'))   # raw git text kept
        self.assertEqual(real_git(work, 'rev-parse', 'HEAD'), turn['base'])   # nothing committed

    def test_normal_turn_still_cleans_up_test_leftovers(self):
        g = run(relay.create_goal(dict(objective='Break things', repo=str(repo('h2')), lead='breaker', test_cmd=LEAVE)))
        work = Path(g['work'])
        self.assertTrue(g['turns'][0]['tests']['cleanup'] == 'cleaned')
        self.assertTrue((work / 'broken.flag').is_file())            # committed work stays
        self.assertFalse((work / 'leftover.tmp').exists())           # leftovers are gone

    def test_dirty_folder_is_left_alone_by_run_tests(self):
        g = relay.create_goal(dict(objective='Break things', repo=str(repo('h3')), lead='breaker', test_cmd=LEAVE))
        work = Path(g['work'])
        (work / 'mine.txt').write_text('precious')
        res = relay.run_tests(relay.load(g['id']), 'dirty')
        self.assertEqual(res['cleanup'], 'skipped')
        self.assertIn('nothing was cleaned up', res['tail'])
        self.assertEqual((work / 'mine.txt').read_text(), 'precious')
        self.assertTrue((work / 'leftover.tmp').exists())


class DurableStop(unittest.TestCase):
    def assert_stopped(self, g, turns=1):
        self.assertEqual(g['status'], 'stopped')
        self.assertEqual(g['reason'], 'You stopped it')
        self.assertEqual(len(g['turns']), turns)
        self.assertTrue(g['stop_requested'])
        self.assertEqual(g['phase'], '')
        self.assertIsNone(g.get('suggestion'))

    def test_stop_during_tests_ends_stopped(self):
        g = relay.create_goal(dict(objective='Break things', repo=str(repo('stop-tests')), lead='breaker', test_cmd=LEAVE))
        real_tests = relay.run_tests

        def stop_in_tests(goal, label):
            if label.startswith('turn-'):
                relay.stop_goal(goal['id'])
            return real_tests(goal, label)

        with patch.object(relay, 'run_tests', stop_in_tests):
            g = run(g)
        self.assert_stopped(g)
        self.assertNotIn(g['id'], relay.TESTS_RUNNING)
        self.assertIsNone(g['test_pid'])

    def test_stop_kills_test_run_by_pid(self):
        g = relay.create_goal(dict(objective='Break things', repo=str(repo('stop-pid')), lead='breaker',
                                   test_cmd=f'"{sys.executable}" -c "import time; time.sleep(60)"'))
        gid = g['id']
        relay.update(gid, baseline=dict(exit=0, failing=[], counts=None, timed_out=False, tail='', seconds=0))
        worker = threading.Thread(target=relay.run_goal, args=(gid,), daemon=True)
        relay.THREADS[gid] = worker
        started = time.monotonic()
        worker.start()
        process = None
        try:
            deadline = started + 30
            while time.monotonic() < deadline and worker.is_alive():
                process = relay.TESTS_RUNNING.get(gid)
                if process and relay.load(gid).get('test_pid') == process.pid:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(process, 'The test process did not start')
            self.assertEqual(relay.load(gid)['test_pid'], process.pid)
            with patch.object(relay, 'kill_tree', wraps=relay.kill_tree) as kill:
                relay.stop_goal(gid)
                worker.join(timeout=30)
                self.assertFalse(worker.is_alive(), 'Stop did not interrupt the tests')
                self.assertIn(unittest.mock.call(process), kill.call_args_list)
            self.assertLess(time.monotonic() - started, 45)
            self.assertIsNotNone(process.poll())
            self.assert_stopped(relay.load(gid))
            self.assertNotIn(gid, relay.TESTS_RUNNING)
            self.assertIsNone(relay.load(gid)['test_pid'])
        finally:
            # Only clean up this test's own recorded process if an assertion failed.
            if process and process.poll() is None:
                relay.kill_tree(process)
            worker.join(timeout=30)

    def test_stop_between_turns_then_continue(self):
        g = relay.create_goal(dict(objective='Add the feature file', repo=str(repo('stop-between')), lead='lead', team=['helper']))
        real_decide = relay.decide

        def stop_between(goal, turn):
            decision = real_decide(goal, turn)
            self.assertEqual(decision[0], 'run')
            relay.stop_goal(goal['id'])
            return decision

        with patch.object(relay, 'decide', stop_between):
            g = run(g)
        self.assert_stopped(g)
        self.assertFalse((Path(g['work']) / 'feature.txt').exists())
        g = relay.act(g['id'], dict(action='continue'))
        self.assertEqual(g['status'], 'queued')
        self.assertNotIn('stop_requested', g)
        self.assertNotIn('stop_reason', g)
        g = run(g)
        self.assertEqual(g['status'], 'ready')
        self.assertEqual([t['agent'] for t in g['turns']], ['lead', 'lead', 'helper', 'lead'])

    def test_stop_during_decide_cannot_be_overwritten_by_ready_or_needs_you(self):
        real_decide = relay.decide

        def stop_before_status(goal, turn):
            decision = real_decide(goal, turn)
            relay.stop_goal(goal['id'])
            return decision

        for agent in ('breaker', 'asker'):
            with self.subTest(agent=agent):
                g = relay.create_goal(dict(objective='Finish or ask', repo=str(repo('stop-status-' + agent)), lead=agent))
                with patch.object(relay, 'decide', stop_before_status):
                    g = run(g)
                self.assert_stopped(g)

    def test_stop_queued_and_clear_on_explicit_actions(self):
        for action in ('continue', 'pass', 'planner', 'done'):
            with self.subTest(action=action):
                g = relay.create_goal(dict(objective='Break things', repo=str(repo('stop-clear-' + action)), lead='breaker'))
                g = relay.stop_goal(g['id'])
                self.assert_stopped(g, turns=0)
                self.assert_stopped(run(g), turns=0)
                g = relay.act(g['id'], dict(action=action, agent='breaker'))
                self.assertNotIn('stop_requested', relay.load(g['id']))
                self.assertNotIn('stop_reason', g)
                self.assertEqual(g['status'], 'ready' if action == 'done' else 'queued')


if __name__ == '__main__':
    unittest.main()
