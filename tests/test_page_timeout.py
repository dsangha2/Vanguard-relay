"""Page request timeout and stuck-tick recovery: api()/tick() extracted from web/index.html
and run under node against a fake HTTP server that stalls its first /api/state reply.
Random port, never 8940, no Vanguard data touched."""
import json, shutil, subprocess, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / 'web' / 'index.html').read_text(encoding='utf-8')

DRIVER = r'''
const port = process.argv[2];
const S = { state: null, initialVersion: null, restarting: false, sel: null, proj: null, chat: null };
const log = [];
const els = {}; const $ = id => els[id] || (els[id] = { classList: { toggle() {} }, textContent: '' });
const stat = (a, b) => log.push(a + '|' + b);
const selId = () => null, feedScene = () => {}, render = () => log.push('render'), unwrapText = x => x, loadProject = async () => {};
const location = { reload() {} };
const realFetch = fetch; const fetch2 = (p, o) => realFetch('http://127.0.0.1:' + port + p, o);
'''


class Stall(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        srv = self.server
        if self.path == '/api/state' and not srv.stalled:
            srv.stalled = True
            srv.stall_done.wait(10)   # hang until the test ends
            return
        body = json.dumps({'goals': [], 'version': 1} if self.path == '/api/state' else {}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)


class PageTimeout(unittest.TestCase):
    def extract(self):
        a = HTML.index('async function api(path, body) {')
        b = HTML.index('function b64uBytes')
        api = HTML[a:b]
        c = HTML.index('let ticking = false')
        d = HTML.index('const poll =')
        tick = HTML[c:d]
        return api, tick

    def test_source_has_timeouts_and_finally(self):
        api, tick = self.extract()
        self.assertIn('new AbortController()', api)
        self.assertIn('post ? 30000 : 10000', api)
        self.assertIn('finally {\n    if (gen === tickGen) { ticking = false;', tick)
        self.assertIn('cannot reach Vanguard Relay, retrying', tick)

    def test_stalled_server_times_out_and_next_tick_runs(self):
        node = shutil.which('node')
        if not node: self.skipTest('node not installed')
        api, tick = self.extract()
        api = api.replace('30000 : 10000', '600 : 300').replace('fetch(path', 'fetch2(path')
        tick = tick.replace('> 10000', '> 100')
        js = DRIVER + api + tick + r'''
(async () => {
  const t0 = Date.now();
  await tick();
  const first = { ms: Date.now() - t0, ticking, log: log.slice(), state: S.state };
  await new Promise(r => setTimeout(r, 150));
  await tick();   // second failure is not needed; server now answers
  console.log(JSON.stringify({ first, ticking, log, state: S.state, failSince }));
})();
'''
        srv = ThreadingHTTPServer(('127.0.0.1', 0), Stall)
        srv.stalled = False; srv.stall_done = threading.Event(); srv.daemon_threads = True
        self.assertNotEqual(srv.server_address[1], 8940)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as d:
                f = Path(d) / 'run.js'; f.write_text(js, encoding='utf-8')
                r = subprocess.run([node, str(f), str(srv.server_address[1])], capture_output=True, text=True, timeout=30)
        finally:
            srv.stall_done.set(); srv.shutdown(); srv.server_close()
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        first = out['first']
        self.assertGreaterEqual(first['ms'], 250); self.assertLess(first['ms'], 5000)   # timeout fired
        self.assertFalse(first['ticking'])                                              # ticking cleared
        self.assertEqual(first['log'], ['NOT REACHABLE|cannot reach Vanguard Relay'])
        self.assertFalse(out['ticking'])
        self.assertIn('render', out['log'])                                             # next tick ran normally
        self.assertEqual(out['state'], {'goals': [], 'version': 1})
        self.assertEqual(out['failSince'], 0)

    def test_recovery_handlers_and_hidden_skip(self):
        for h in ("document.addEventListener('visibilitychange'", "window.addEventListener('pageshow', recover)", "window.addEventListener('online', recover)"):
            self.assertIn(h, HTML)
        node = shutil.which('node')
        if not node: self.skipTest('node not installed')
        c = HTML.index('const poll ='); d = HTML.index('function recover()')
        poll = HTML[c:HTML.index(chr(10), HTML.index('setInterval(poll', c))]
        poll = poll.replace('setInterval(poll, 2500); tick();', '')
        rec = HTML[d:HTML.index("document.addEventListener('visibilitychange'")]
        js = "const document = { hidden: true }; let n = 0, ticking = true, again = true; const reads = new Set(); const tick = () => { n++; };" + chr(10) + poll + rec + r'''
poll(); const hiddenCalls = n;
document.hidden = false; poll(); const visibleCalls = n;
let aborted = 0; reads.add({ abort() { aborted++; } });
recover();
console.log(JSON.stringify({ hiddenCalls, visibleCalls, n, aborted, ticking, again }));
'''
        with tempfile.TemporaryDirectory() as dd:
            f = Path(dd) / 'run.js'; f.write_text(js, encoding='utf-8')
            r = subprocess.run([node, str(f)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out, {'hiddenCalls': 0, 'visibleCalls': 1, 'n': 2, 'aborted': 1, 'ticking': False, 'again': False})


if __name__ == '__main__':
    unittest.main()
