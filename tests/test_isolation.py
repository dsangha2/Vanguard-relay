"""The whole suite, run from a temp copy of the code, must leave that copy's data/ folder untouched."""
import os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Isolation(unittest.TestCase):
    @unittest.skipIf(os.environ.get('RELAY_NESTED_RUN'), 'already inside the isolation run')
    def test_suite_does_not_touch_data_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / 'copy'
            copy.mkdir()
            for name in ('relay.py', 'strata_run.py', 'keepalive.py', 'p256.py'):
                shutil.copy2(ROOT / name, copy / name)
            shutil.copytree(ROOT / 'tests', copy / 'tests', ignore=shutil.ignore_patterns('__pycache__'))
            if (ROOT / 'web').exists():
                shutil.copytree(ROOT / 'web', copy / 'web')
            env = {k: v for k, v in os.environ.items() if k != 'RELAY_DATA'}
            env['RELAY_NESTED_RUN'] = '1'
            r = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], cwd=copy, env=env,
                               capture_output=True, text=True, timeout=900)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            data = copy / 'data'
            leftovers = [p.name for p in data.iterdir()] if data.exists() else []
            self.assertEqual([x for x in leftovers if x in ('config.json', 'chat.json', 'goals')], [], leftovers)
            self.assertEqual(leftovers, [])

    def test_import_without_data_folder_fails_loudly_under_unittest(self):
        env = {k: v for k, v in os.environ.items() if k != 'RELAY_DATA'}
        r = subprocess.run([sys.executable, '-c', 'import unittest, relay'], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('RELAY_DATA', r.stderr)

    def test_inherited_data_folder_is_replaced_and_left_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'config.json'
            marker.write_text('{"marker": true}', encoding='utf-8')
            mod = Path(tmp) / 'probe_mod.py'
            src = (
                "import sys; sys.path.insert(0, %r)\n"
                "import _isolate, os, relay, unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_it(self):\n"
                "        self.assertNotEqual(os.environ['RELAY_DATA'], %r)\n"
                "        relay.write_json(relay.DATA / 'config.json', {'prefer_agents': ['x']})\n"
                "unittest.main()\n"
            ) % (str(ROOT / 'tests'), tmp)
            mod.write_text(src, encoding='utf-8')
            env = dict(os.environ, RELAY_DATA=tmp)
            before = marker.read_text(encoding='utf-8')
            r = subprocess.run([sys.executable, str(mod)], cwd=tmp, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            self.assertEqual(marker.read_text(encoding='utf-8'), before)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir() if p.name != '__pycache__'), ['config.json', 'probe_mod.py'])

    def test_relay_refuses_real_data_folder_under_unittest(self):
        env = dict(os.environ, RELAY_DATA=str(ROOT / 'data'))
        r = subprocess.run([sys.executable, '-c', 'import unittest, relay'], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('real data folder', r.stderr)


if __name__ == '__main__':
    unittest.main()
