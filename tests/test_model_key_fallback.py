"""modelKey: agent id, else the agent's provider, else 'other' (spark and wonder get the Muse symbol)."""
import json, re, shutil, subprocess, tempfile, unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / 'web' / 'index.html').read_text(encoding='utf-8')


class ModelKey(unittest.TestCase):
    def test_source_uses_provider_fallback(self):
        m = re.search(r"const modelKey = id => \{.*?\n\};", HTML, re.S)
        self.assertIsNotNone(m)
        self.assertIn('a.provider', m.group(0))
        self.assertIn("'other'", m.group(0))

    def test_resolution(self):
        node = shutil.which('node')
        if not node: self.skipTest('node not installed')
        m = re.search(r"const modelKey = id => \{.*?\n\};", HTML, re.S).group(0)
        js = ("const MODEL_ICONS={icons:{muse:1,sonnet:1,other:1}};"
              "const S={state:{agents:[{id:'spark',provider:'muse'},{id:'wonder',provider:'muse'},{id:'sonnet',provider:'claude'},{id:'zed',provider:'nope'}]}};"
              + m + "console.log(JSON.stringify(['spark','wonder','sonnet','zed','ghost'].map(modelKey)))")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 't.js'; f.write_text(js, encoding='utf-8')
            r = subprocess.run([node, str(f)], capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(r.stdout), ['muse', 'muse', 'sonnet', 'other', 'other'])

    def test_agent_icon_vars_uses_provider(self):
        self.assertIn("FAM[a.provider] || FALLBACK", HTML)


if __name__ == '__main__':
    unittest.main()
