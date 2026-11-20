"""Fewer requests per refresh: tick() from web/index.html run under node against a fake server that counts requests.
Random port, never 8940, no Vanguard data touched."""
import json, shutil, subprocess, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / 'web' / 'index.html').read_text(encoding='utf-8')

DRIVER = r'''
const port = process.argv[2];
const S = { state: null, initialVersion: null, restarting: false, sel: 'g1', proj: 'p1', chat: null, pfull: {}, goal: null };
const els = {}; const $ = id => els[id] || (els[id] = { classList: { toggle() {} }, textContent: '' });
const stat = () => {}, selId = () => S.sel, feedScene = () => {}, render = () => {}, unwrapText = x => x;
const location = { reload() {} };
const realFetch = fetch; const fetch2 = (p, o) => realFetch('http://127.0.0.1:' + port + p, o);
async function loadProject(pid) { try { S.pfull[pid] = (await api('/api/projects/' + pid)).project; } catch (e) { delete S.pfull[pid]; } }
async function get(path) { return (await realFetch('http://127.0.0.1:' + port + path)).json(); }
'''


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        s = self.server
        s.hits.append(self.path)
        if self.path == '/api/state':
            d = dict(version=1, chat_n=s.chat_n, goals=[dict(id='g1', title='t', updated=s.g_up)], projects=[dict(id='p1', updated=s.p_up)], agents=[])
        elif self.path == '/api/chat': d = dict(messages=[], n=s.chat_n)
        elif self.path == '/api/goals/g1': d = dict(goal=dict(id='g1', title='t', objective='o'), live='live')
        elif self.path == '/api/projects/p1': d = dict(project=dict(id='p1'))
        else: d = {}
        body = json.dumps(d).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        s = self.server
        for k, v in json.loads(self.rfile.read(int(self.headers['Content-Length']))).items(): setattr(s, k, v)
        s.hits.clear()
        self.send_response(200); self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'{}')


class RefreshRequests(unittest.TestCase):
    def test_source_uses_markers_and_promise_all(self):
        a = HTML.index('let ticking = false'); b = HTML.index('const poll =')
        tick = HTML[a:b]
        self.assertIn('Promise.all', tick)
        self.assertIn('chat_n', tick)

    def test_unchanged_refresh_is_one_request(self):
        node = shutil.which('node')
        if not node: self.skipTest('node not installed')
        a = HTML.index('async function api(path, body) {'); b = HTML.index('function b64uBytes')
        c = HTML.index('let ticking = false'); d = HTML.index('const poll =')
        api = HTML[a:b].replace('fetch(path', 'fetch2(path')
        js = DRIVER + api + HTML[c:d] + r'''
const set = o => realFetch('http://127.0.0.1:' + port + '/set', { method: 'POST', body: JSON.stringify(o) });
(async () => {
  const out = {};
  const run = async (name, o) => { if (o) await set(o); await tick(); out[name] = (await get('/hits')).hits; };
  await tick(); out.first = (await get('/hits')).hits;
  await set({});
  await tick(); out.same = (await get('/hits')).hits;
  await set({}); await set({ chat_n: 2 }); await tick(); out.chat = (await get('/hits')).hits;
  await set({}); await set({ g_up: 5 }); await tick(); out.goal = (await get('/hits')).hits;
  await set({}); await set({ p_up: 5 }); await tick(); out.proj = (await get('/hits')).hits;
  await set({}); await tick(); out.again = (await get('/hits')).hits;
  S.sel = null; S.goal = null; await set({}); await tick(); out.nogoal = (await get('/hits')).hits;
  console.log(JSON.stringify(out));
})();
'''
        # /hits and /set helpers: GET /hits returns the log (not itself counted)
        orig = Fake.do_GET
        def do_GET(self):
            if self.path == '/hits':
                body = json.dumps(dict(hits=list(self.server.hits))).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
            orig(self)
        Fake.do_GET = do_GET
        def do_POST(self):
            s = self.server
            for k, v in json.loads(self.rfile.read(int(self.headers['Content-Length']))).items(): setattr(s, k, v)
            s.hits.clear()
            self.send_response(200); self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'{}')
        srv = ThreadingHTTPServer(('127.0.0.1', 0), Fake)
        srv.hits = []; srv.chat_n = 1; srv.g_up = 1; srv.p_up = 1; srv.daemon_threads = True
        self.assertNotEqual(srv.server_address[1], 8940)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as dd:
                f = Path(dd) / 'run.js'; f.write_text(js, encoding='utf-8')
                r = subprocess.run([node, str(f), str(srv.server_address[1])], capture_output=True, text=True, timeout=30)
        finally:
            Fake.do_GET = orig; srv.shutdown(); srv.server_close()
        self.assertEqual(r.returncode, 0, r.stderr)
        o = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(sorted(o['first']), ['/api/chat', '/api/goals/g1', '/api/projects/p1', '/api/state'])
        self.assertEqual(o['same'], ['/api/state'])
        self.assertEqual(sorted(o['chat']), ['/api/chat', '/api/state'])
        self.assertEqual(sorted(o['goal']), ['/api/goals/g1', '/api/state'])
        self.assertEqual(sorted(o['proj']), ['/api/projects/p1', '/api/state'])
        self.assertEqual(o['again'], ['/api/state'])


if __name__ == '__main__':
    unittest.main()
