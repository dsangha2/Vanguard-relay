"""Repo guides in the coordinator chat: LIVE STATUS lists them, names and aliases resolve, the guide reaches the prompt, remember appends."""
import shutil, unittest
from pathlib import Path
from unittest import mock

import _isolate  # noqa: F401
import relay

CONF = relay.DATA / 'config.json'
CHAT = relay.DATA / 'chat.json'
DECK = ('Ambient Deck rules.\nBASE FOLDER: C:\\Projects\\Ambient-Deck\nNAMES: ambient deck, deck, ari\n'
        'STATUS FILE: Ambient-Deck\\PROGRESS.md\nTEST COMMAND: npm test\n')


class GuideChat(unittest.TestCase):
    def setUp(self):
        for f in (CONF, CHAT):     # shared data folder: put both files back exactly as they were
            before = f.read_bytes() if f.exists() else None
            self.addCleanup(lambda f=f, before=before: f.write_bytes(before) if before is not None else f.unlink(missing_ok=True))
        self.addCleanup(shutil.rmtree, relay.guides_dir(), True)
        shutil.rmtree(relay.guides_dir(), True)
        self.agent = next(iter(relay.config()['agents']))
        relay.save_guide('Ambient-Deck-relay', DECK)
        relay.save_guide('other', 'Other rules.\n')

    def say(self, text, reply):
        asked = []
        def fake_call(name, prompt, session, **k):
            asked.append(prompt); return reply, None, None
        with mock.patch.object(relay, 'coordinator', return_value=self.agent), mock.patch.object(relay, 'chat_call', side_effect=fake_call):
            relay.chat_run(text)
        return asked[-1], relay.chat_load()['messages'][-1]

    def test_aliases_resolve_to_the_right_repo(self):
        for word in ['Ambient-Deck-relay', 'ambient deck', 'Deck', 'ARI', 'Ambient-Deck', 'C:\\Projects\\Ambient-Deck-relay\\']:
            self.assertEqual(relay.resolve_guide(word), 'Ambient-Deck-relay', word)
        self.assertEqual(relay.resolve_guide('other'), 'other')
        self.assertIsNone(relay.resolve_guide('nothing'))
        self.assertIsNone(relay.resolve_guide(''))
        self.assertEqual(relay.guide_repo('Ambient-Deck-relay'), 'C:\\Projects\\Ambient-Deck')
        self.assertEqual(relay.guide_repo('other'), str(Path(relay.config()['projects_root']) / 'other'))

    def test_message_names_pick_guides_by_whole_word(self):
        self.assertEqual(relay.guides_in_message('what should we do next on the deck?'), ['Ambient-Deck-relay'])
        self.assertEqual(relay.guides_in_message('Ari and other both'), ['Ambient-Deck-relay', 'other'])
        self.assertEqual(relay.guides_in_message('the decking and a safari'), [])

    def test_status_lists_guides(self):
        got = {g['name']: g for g in relay.status_block()['guides']}
        self.assertEqual(set(got), {'Ambient-Deck-relay', 'other'})
        self.assertEqual(got['Ambient-Deck-relay']['repo'], 'C:\\Projects\\Ambient-Deck')
        self.assertIn('ari', got['Ambient-Deck-relay']['names'])

    def test_named_repo_puts_guide_in_prompt(self):
        prompt, _ = self.say('what should we do next on ambient deck?', 'Reading PROGRESS.md...')
        self.assertIn('GUIDE FOR Ambient-Deck-relay', prompt)
        self.assertIn('STATUS FILE: Ambient-Deck\\PROGRESS.md', prompt)
        self.assertIn('C:\\Projects\\Ambient-Deck', prompt)
        self.assertNotIn('Other rules.', prompt)
        self.assertLess(prompt.index('GUIDE FOR Ambient'), prompt.index('THE PERSON SAYS'))
        prompt, _ = self.say('how are the goals?', 'Fine.')
        self.assertNotIn('GUIDE FOR Ambient', prompt); self.assertNotIn('Ambient Deck rules.', prompt)

    def test_chat_appends_one_line_and_repeats_it(self):
        _, msg = self.say('remember that for deck: never touch the main branch',
                          'Saved: never touch the main branch\nACTIONS: [{"do": "remember", "repo": "deck", "text": "never touch the main branch"}]')
        self.assertEqual(msg['actions'][0], dict(ok=True, guide='Ambient-Deck-relay',
                                                 text='Saved to the guide for Ambient-Deck-relay: never touch the main branch'))
        self.assertEqual(relay.guide_text('Ambient-Deck-relay'), DECK + '- never touch the main branch\n')
        relay.save_guide('other', 'no newline')
        relay.guide_remember('other', '- second\nline')
        self.assertEqual(relay.guide_text('other'), 'no newline\n- second line\n')

    def test_remember_refuses_unknown_repo_and_empty_text(self):
        _, msg = self.say('remember that for nowhere: x', 'ACTIONS: [{"do": "remember", "repo": "nowhere-xyz", "text": "x"}]')
        self.assertFalse(msg['actions'][0]['ok']); self.assertIn('No repo or guide is called nowhere-xyz', msg['actions'][0]['text'])
        self.assertFalse((relay.guides_dir() / 'nowhere-xyz.md').exists())
        with self.assertRaises(ValueError): relay.guide_remember('deck', '   ')

    def test_remember_makes_a_guide_for_a_repo_without_one(self):
        with mock.patch.object(relay, 'repos', return_value=[str(Path(relay.config()['projects_root']) / 'NewRepo')]):
            name, line = relay.guide_remember('newrepo', 'tests are slow')
        self.assertEqual((name, line), ('NewRepo', 'tests are slow'))
        self.assertEqual(relay.guide_text('NewRepo'), '- tests are slow\n')

    def test_changed_rules_start_a_fresh_session(self):
        def run(rules):
            c = relay.chat_load(); c['sessions'] = {self.agent: 'S1'}
            c.pop('rules', None)
            if rules is not None: c['rules'] = {self.agent: rules}
            relay.write_json(CHAT, c)
            seen = []
            def fake_call(name, prompt, session, **k):
                seen.append((prompt, session)); return 'ok', 'S2', None
            with mock.patch.object(relay, 'coordinator', return_value=self.agent), mock.patch.object(relay, 'chat_call', side_effect=fake_call):
                relay.chat_run('hello')
            return seen[0]
        prompt, session = run(relay.rules_hash())
        self.assertEqual(session, 'S1'); self.assertNotIn('You are the Vanguard coordinator', prompt)
        prompt, session = run('oldhash')
        self.assertIsNone(session); self.assertIn('You are the Vanguard coordinator', prompt)
        self.assertEqual(relay.chat_load()['rules'][self.agent], relay.rules_hash())
        prompt, session = run(None)    # sessions saved before this feature have no hash
        self.assertIsNone(session)

    def test_rules_explain_guides(self):
        for bit in ('"do": "remember"', 'What should we do next', 'Repeat back', 'show a guide'):
            self.assertIn(bit, relay.COORD_RULES)


if __name__ == '__main__':
    unittest.main()
