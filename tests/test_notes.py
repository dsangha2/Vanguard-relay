"""the user's notes must reach the next agent turn's prompt, in full, exactly once, on every path."""
import os, time, unittest
from pathlib import Path

import test_relay as base          # sets up the fake agents and the temp data folder
from test_relay import relay, repo, run

NOTE = 'Create feature.txt containing hello. PERSON-NOTE-' + 'x' * 2500    # longer than the short brief's old 1500 cap


def prompt(g, n):
    return (relay.goal_path(g['id']) / f'turn-{n}.prompt.md').read_text(encoding='utf-8')


def add_note(g, text=NOTE):
    with relay.LOCK:
        x = relay.load(g['id']); x['notes'].append(dict(ts=time.time(), text=text, delivered=False, attachments=[])); relay.save(x)


class NotePaths(unittest.TestCase):
    def test_answer_and_continue_reaches_prompt_once_and_stays_in_history(self):
        g = run(relay.create_goal(dict(objective='Ask something', repo=str(repo('n1')), lead='asker')))
        self.assertEqual(g['status'], 'needs_you')
        relay.act(g['id'], dict(action='continue', note=NOTE))
        g = run(relay.load(g['id']))
        self.assertEqual(prompt(g, 2).count(NOTE), 1)
        self.assertEqual([n['text'] for n in g['notes']], [NOTE]); self.assertTrue(g['notes'][0]['delivered'])
        self.assertEqual(g['turns'][1]['notes_given'], [NOTE])

    def test_note_box_note_follows_a_handoff_to_another_agent_once(self):
        g = run(relay.create_goal(dict(objective='Add the feature file', repo=str(repo('n2')), lead='lead', team=['helper'], mode='manual')))
        add_note(g)
        relay.act(g['id'], dict(action='pass', agent='helper'))
        g = run(relay.load(g['id']))
        self.assertEqual(g['turns'][1]['agent'], 'helper')
        self.assertEqual(prompt(g, 2).count(NOTE), 1)
        self.assertEqual(sum(NOTE in prompt(g, n) for n in range(2, len(g['turns']) + 1)), 1)   # not repeated on later turns

    def test_pass_with_note_is_not_duplicated_and_not_lost(self):
        g = run(relay.create_goal(dict(objective='Add the feature file', repo=str(repo('n3')), lead='lead', team=['helper'], mode='manual')))
        relay.act(g['id'], dict(action='pass', agent='helper', note=NOTE))
        g = run(relay.load(g['id']))
        self.assertEqual(prompt(g, 2).count(NOTE), 1)

    def test_auto_mode_note_added_while_the_decision_is_saved_is_kept(self):
        orig = relay.decide
        left = []
        def decide_and_leave_note(goal, turn):
            if not left: left.append(1); add_note(goal, 'LATE-NOTE')   # the user presses "leave a note" while Vanguard is deciding what comes next
            return orig(goal, turn)
        relay.decide = decide_and_leave_note
        try:
            g = run(relay.create_goal(dict(objective='Add the feature file', repo=str(repo('n4')), lead='lead', team=['helper'])))
        finally:
            relay.decide = orig
        self.assertTrue(any('LATE-NOTE' in prompt(g, n) for n in range(2, len(g['turns']) + 1)))
        self.assertEqual(sum('LATE-NOTE' in prompt(g, n) for n in range(1, len(g['turns']) + 1)), 1)

    def test_usage_limit_reroute_still_delivers_the_note(self):
        orig = relay.config
        def fakes_only():
            c = orig(); keep = ('fake', 'fake2', 'fakeL')
            c['agents'] = {k: v for k, v in c['agents'].items() if v['provider'] in keep}
            c['providers'] = {k: v for k, v in c['providers'].items() if k in keep}
            return c
        relay.config = fakes_only; relay.billing_recheck()
        try:
            g = relay.create_goal(dict(objective='Add the feature file', repo=str(repo('n6')), lead='limlead', team=['alt2']))
            add_note(g, 'Create feature.txt containing hello')
            g = run(g)
        finally:
            relay.config = orig; relay.billing_recheck()
        self.assertEqual([t['agent'] for t in g['turns']][:2], ['limlead', 'alt2'])
        self.assertTrue(g['turns'][0]['limited'])
        self.assertIn('note seen: yes', g['reason'])                       # the replacement agent saw the note
        self.assertEqual(prompt(g, 2).count('Create feature.txt containing hello'), 1)

    def test_restart_while_running_gives_the_note_to_whoever_continues(self):
        g = relay.create_goal(dict(objective='Add the feature file', repo=str(repo('n7')), lead='lead', team=['helper']))
        add_note(g)
        with relay.LOCK:
            x = relay.load(g['id']); x['notes'][0]['delivered'] = True; x['status'] = 'running'
            x['next'] = dict(agent='lead', note='earlier instructions', why='test')
            x['turns'].append(dict(n=1, agent='lead', status='running', note='earlier instructions', note_idx=[0], started=time.time())); relay.save(x)
        relay.recover_cut_off_goals([relay.load(g['id'])])
        x = relay.load(g['id'])
        self.assertEqual((x['status'], x['turns'][0]['status'], x['notes'][0]['delivered']), ('needs_you', 'interrupted', False))
        relay.act(g['id'], dict(action='pass', agent='helper'))
        x = relay.load(g['id'])
        self.assertIn('earlier instructions', x['next']['note'])
        self.assertEqual(relay.brief(x, 'helper', x['next']['note'], False).count(NOTE), 1)

    def test_failed_turn_gives_the_note_back(self):
        g = relay.create_goal(dict(objective='Do a thing', repo=str(repo('n8')), lead='crash', team=['lead']))
        add_note(g)
        t = relay.run_turn(relay.load(g['id']), 'crash', '', 'test')
        self.assertNotEqual(t['status'], 'ok')
        x = relay.load(g['id']); self.assertFalse(x['notes'][0]['delivered'])
        self.assertEqual(relay.brief(x, 'lead', '', False).count(NOTE), 1)

    def test_strata_short_brief_carries_notes_in_full(self):
        g = relay.create_goal(dict(objective='Add the feature file', repo=str(repo('n9')), lead='lead'))
        add_note(g)
        x = relay.load(g['id'])
        self.assertEqual(relay.short_brief(x, '').count(NOTE), 1)
        self.assertEqual(relay.short_brief(x, NOTE).count(NOTE), 1)        # a long instruction is not cut to 1500 characters

    def test_over_long_prompt_keeps_the_end(self):
        text = 'H' * 40000 + 'THE-NOTE-AND-RULES'
        capped = relay.cap_prompt(text)
        self.assertLessEqual(len(capped), 28000); self.assertTrue(capped.endswith('THE-NOTE-AND-RULES'))


if __name__ == '__main__':
    unittest.main()
