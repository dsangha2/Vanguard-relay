import os, re, tempfile, unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix='relay-settings-test-')
os.environ['RELAY_DATA'] = _TMP  # never the real data/ folder, never port 8940
HTML = (Path(__file__).resolve().parent.parent / 'web' / 'index.html').read_text(encoding='utf-8')


class SettingsMenuPage(unittest.TestCase):
    def test_ui_accent_picker_exists(self):
        self.assertIn('id="ui-accent"', HTML)
        self.assertIn('type="color" id="ui-accent"', HTML)

    def test_sync_ui_default_off_in_both_places(self):
        self.assertEqual(len(re.findall(r"syncUI: false", HTML)), 2)
        self.assertNotIn("syncUI: true", HTML)

    def test_phone_menu_rule_scrolls(self):
        rules = re.findall(r'@media[^{]*max-width:\s*900px[^{]*\{(.*?)\n\}', HTML, re.S)
        found = [r for r in rules if '.colour-control .colour-menu' in r]
        self.assertTrue(found, 'phone .colour-menu rule missing')
        body = re.search(r'\.colour-control \.colour-menu\s*\{([^}]*)\}', found[0]).group(1)
        self.assertIn('position:fixed', body)
        self.assertIn('overflow-y:auto', body)
        self.assertIn('max-height', body)

    def test_phone_rule_after_width_rule(self):
        self.assertGreater(HTML.index('.colour-control .colour-menu{position:fixed'),
                           HTML.index('width: 236px !important'))


if __name__ == '__main__':
    unittest.main()
