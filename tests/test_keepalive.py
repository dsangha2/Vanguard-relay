"""keepalive.py restarts the server sensibly, and relay.py cooperates with it; every agent prompt carries the live-server rule."""
import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import _isolate  # noqa: F401  (must come before relay)
import relay

KEEPALIVE = Path(__file__).resolve().parents[1] / 'keepalive.py'

# Fake server. Behaviour for run N comes from plan.json (the last entry repeats):
#   code: exit code, flag: write stop.flag first, serve: bind RELAY_PORT and wait for stop.flag.
FAKE_CHILD = r'''
import json, os, socket, sys, time
from pathlib import Path
d = Path(os.environ['FAKE_DIR']); data = Path(os.environ['RELAY_DATA'])
runs = d / 'runs.txt'
n = (int(runs.read_text()) if runs.exists() else 0) + 1
runs.write_text(str(n))
plan = json.loads((d / 'plan.json').read_text())
step = plan[min(n, len(plan)) - 1]
with (d / 'events.txt').open('a') as f: f.write('start %d\n' % n)
if step.get('serve'):
    s = socket.socket(); s.bind(('127.0.0.1', int(os.environ['RELAY_PORT']))); s.listen()
    (d / 'pid.txt').write_text(str(os.getpid()))
    for _ in range(100):
        if (data / 'stop.flag').exists(): break
        time.sleep(0.1)
    s.close()
if step.get('flag'): (data / 'stop.flag').write_text('x')
with (d / 'events.txt').open('a') as f: f.write('end %d\n' % n)
sys.exit(step.get('code', 0))
'''


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
    return port


class KeepaliveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.data = self.dir / 'data'; self.data.mkdir()
        (self.dir / 'child.py').write_text(FAKE_CHILD, encoding='utf-8')
        self.port = free_port()
        self.proc = None
        self.addCleanup(self.stop_keepalive)

    def stop_keepalive(self):
        if self.proc and self.proc.poll() is None:   # only the Popen this test started
            self.proc.kill(); self.proc.wait()

    def run_keepalive(self, plan, timeout=60):
        (self.dir / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
        env = dict(os.environ, FAKE_DIR=str(self.dir), RELAY_DATA=str(self.data), RELAY_PORT=str(self.port))
        self.proc = subprocess.Popen([sys.executable, str(KEEPALIVE), '--child', str(self.dir / 'child.py'),
                                      '--data', str(self.data), '--delay', '0.1'], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, _ = self.proc.communicate(timeout=timeout)
        return self.proc.returncode, out

    def runs(self):
        p = self.dir / 'runs.txt'
        return int(p.read_text()) if p.exists() else 0

    def read(self, name):
        p = self.data / name
        return p.read_text(encoding='utf-8') if p.exists() else ''

    def test_restarts_after_a_crash_until_stop_flag(self):
        code, _ = self.run_keepalive([dict(code=1), dict(code=1, flag=True)])
        self.assertEqual(code, 0)
        self.assertEqual(self.runs(), 2)
        self.assertEqual(len(self.read('keepalive.log').strip().splitlines()), 2)
        self.assertFalse((self.data / 'stop.flag').exists())

    def test_stop_flag_prevents_restart(self):
        code, _ = self.run_keepalive([dict(code=1, flag=True)])
        self.assertEqual(code, 0)
        self.assertEqual(self.runs(), 1)
        self.assertFalse((self.data / 'stop.flag').exists())
        self.assertIn('stop.flag found', self.read('keepalive.log'))

    def test_stale_stop_flag_does_not_block_a_fresh_start(self):
        (self.data / 'stop.flag').write_text('old')
        code, _ = self.run_keepalive([dict(code=1, flag=True)])
        self.assertEqual(code, 0)
        self.assertEqual(self.runs(), 1)

    def test_gives_up_after_five_restarts(self):
        code, _ = self.run_keepalive([dict(code=1)])
        self.assertEqual(code, 1)
        self.assertEqual(self.runs(), 6)
        self.assertEqual(self.read('server.err.log').count('not restarting'), 1)
        self.assertIn('giving up', self.read('keepalive.log'))

    def test_restart_request_never_overlaps(self):
        # Run 2 binds the port and waits for stop.flag, which a helper thread of this test writes.
        import threading
        (self.dir / 'plan.json').write_text(json.dumps([dict(code=3), dict(serve=True, code=0)]), encoding='utf-8')
        done = threading.Event()

        def writer():
            deadline = time.monotonic() + 30
            while not done.is_set() and time.monotonic() < deadline:
                if (self.dir / 'pid.txt').exists():
                    (self.data / 'stop.flag').write_text('x'); return
                time.sleep(0.05)
        t = threading.Thread(target=writer, daemon=True); t.start(); self.addCleanup(done.set)
        code, _ = self.run_keepalive([dict(code=3), dict(serve=True, code=0)])
        self.assertEqual(code, 0)
        self.assertEqual(self.runs(), 2)
        events = (self.dir / 'events.txt').read_text().split()
        self.assertEqual(events, ['start', '1', 'end', '1', 'start', '2', 'end', '2'])
        self.assertTrue((self.dir / 'pid.txt').exists())

    def test_port_already_in_use_exits_quietly_without_child(self):
        s = socket.socket(); s.bind(('127.0.0.1', self.port)); s.listen(); self.addCleanup(s.close)
        code, out = self.run_keepalive([dict(code=1)], timeout=20)
        self.assertEqual(code, 0)
        self.assertEqual(self.runs(), 0)
        self.assertIn('already running', out)


class RelayRestartTests(unittest.TestCase):
    def tearDown(self):
        relay.EXIT_CODE = 0; relay.RESTARTING = False

    def test_under_keepalive_restart_does_not_spawn_a_child(self):
        server = MagicMock()
        with patch.dict(os.environ, {'RELAY_KEEPALIVE': '1'}), patch.object(relay, 'restart_allowed'), \
                patch.object(relay, 'spawn_restart_child') as spawn:
            relay.restart_process(server)
        spawn.assert_not_called()
        server.shutdown.assert_called_once()
        self.assertEqual(relay.EXIT_CODE, relay.EXIT_RESTART)

    def test_without_keepalive_restart_spawns_a_child(self):
        server = MagicMock()
        env = {k: v for k, v in os.environ.items() if k != 'RELAY_KEEPALIVE'}
        with patch.dict(os.environ, env, clear=True), patch.object(relay, 'restart_allowed'), \
                patch.object(relay, 'spawn_restart_child') as spawn:
            relay.restart_process(server)
        spawn.assert_called_once()
        server.shutdown.assert_called_once()
        self.assertEqual(relay.EXIT_CODE, 0)


class ServerRulePromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'project'; (self.repo / '.git').mkdir(parents=True)
        self.cfg = copy.deepcopy(relay.DEFAULT_CONFIG)
        self.cfg['agents'] = {
            'sol': dict(model='sol', provider='codex', tier=3),
            'luna': dict(model='luna', provider='codex', tier=1)}
        view = [dict(id=k, available=True, installed=True, blocked=False, **v) for k, v in self.cfg['agents'].items()]
        for name, value in [('DATA', self.root / 'data'), ('config', lambda: self.cfg), ('agents_view', lambda: view),
                            ('coordinator', lambda: 'luna'), ('strata_reachable', lambda: False),
                            ('blocked_reason', lambda _: None), ('limited_reason', lambda _: None),
                            ('git', lambda *a, **k: '')]:
            p = patch.object(relay, name, value); p.start(); self.addCleanup(p.stop)

    def check(self, text):
        self.assertIn('Never stop, kill or restart it', text)
        self.assertIn('by the PID you recorded', text)
        self.assertIn('RELAY_PORT', text)
        self.assertIn(str(relay.live_port()), text)

    def test_rule_is_in_every_brief(self):
        g = relay.create_goal(dict(repo=str(self.repo), objective='Make a useful feature', lead='sol', team=['luna']))
        self.check(relay.brief(g, 'sol', '', False))
        self.check(relay.brief(g, 'sol', '', True))
        self.check(relay.brief(g, 'luna', 'Only edit the icon', False))
        self.check(relay.short_brief(g, 'note'))


if __name__ == '__main__':
    unittest.main()
