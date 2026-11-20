import re, shutil, subprocess, unittest
from pathlib import Path
HTML = (Path(__file__).resolve().parent.parent / 'web' / 'index.html').read_text(encoding='utf-8')


class ProjectFixPage(unittest.TestCase):
    def test_buttons_and_text(self):
        self.assertIn("pb('fix', 'Fix'", HTML)
        self.assertIn("nFix < 3", HTML)
        self.assertIn("data-worse", HTML)
        self.assertIn("'Merge with ' + nFail + ' failing tests?'", HTML)
        self.assertIn("{ accept_worse: true }", HTML)

    @unittest.skipUnless(shutil.which('node'), 'node not installed')
    def test_script_parses(self):
        import tempfile
        scripts = re.findall(r'<script>(.*?)</script>', HTML, re.S)
        self.assertTrue(scripts)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 's.js'; f.write_text('\n'.join(scripts), encoding='utf-8')
            r = subprocess.run(['node', '--check', str(f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
