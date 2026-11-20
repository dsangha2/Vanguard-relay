"""Small page fixes: Windows paths in the new-goal form, phone goals box, plan_ready card."""
import json, re, shutil, tempfile, subprocess, unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / 'web' / 'index.html').read_text(encoding='utf-8')


class PageFixes(unittest.TestCase):
    def test_folder_name_handles_both_slashes(self):
        m = re.search(r"const gname = \(\) => \$\('f-repo'\)\.value\.trim\(\)(\.replace\(.*?\)\.split\(.*?\)\.pop\(\));", HTML)
        self.assertIsNotNone(m)
        node = shutil.which('node')
        if not node: self.skipTest('node not installed')
        paths = [r'C:\Projects\Ambient-Deck-relay', 'C:/Projects/Ambient-Deck-relay/', r'C:\Projects\Ambient-Deck-relay' + chr(92)]
        js = 'const g=v=>v.trim()' + m.group(1) + ';console.log(' + json.dumps(paths) + '.map(g).join("|"))'
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'g.js'; f.write_text(js, encoding='utf-8')
            r = subprocess.run([node, str(f)], capture_output=True, text=True, timeout=30)
        out = r.stdout.strip()
        self.assertEqual(out, 'Ambient-Deck-relay|' * 2 + 'Ambient-Deck-relay')

    def test_phone_goals_box_not_clipped(self):
        self.assertIn('#goals-panel{order:5;flex:none;max-height:none}', HTML)

    def test_plan_ready_card(self):
        self.assertIn("const can = ['needs_you', 'stopped'].includes(f.status)", HTML)
        self.assertLess(HTML.index("pb('start', 'Start'"), HTML.index('<div style="margin-top:8px">${pieces}</div>'))


if __name__ == '__main__':
    unittest.main()
