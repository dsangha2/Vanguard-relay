import fs from 'node:fs';
import cp from 'node:child_process';

const root = process.cwd();
fs.mkdirSync('data', { recursive: true });
const server = cp.spawn('python', ['-u', 'tests/check_attachments_browser.py'], { cwd: root, windowsHide: true });
const port = await new Promise((resolve, reject) => {
  server.stdout.once('data', d => resolve(Number(d.toString().trim())));
  server.on('error', reject); server.stderr.on('data', d => process.stderr.write(d));
});
const chrome = cp.spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run', '--no-default-browser-check',
  '--remote-debugging-port=0', '--remote-allow-origins=*', '--user-data-dir=' + root + '/data/icons-chrome', 'about:blank'
], { windowsHide: true });
try {
  const endpoint = await new Promise((resolve, reject) => {
    let out = ''; chrome.stderr.on('data', d => { out += d; const m = out.match(/DevTools listening on (ws:\/\/[^\s]+)/); if (m) resolve(m[1]); });
    chrome.on('error', reject); setTimeout(() => reject(Error('Chrome startup timeout')), 15000);
  });
  const sock = new WebSocket(endpoint); await new Promise(r => sock.onopen = r);
  let seq = 0; const pending = new Map();
  sock.onmessage = e => { const m = JSON.parse(e.data); if (m.id) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(Error(JSON.stringify(m.error))) : p.resolve(m.result); } };
  const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => { const id = ++seq; pending.set(id, { resolve, reject }); sock.send(JSON.stringify({ id, method, params, sessionId })); });
  const target = await call('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await call('Target.attachToTarget', { targetId: target.targetId, flatten: true });
  const c = (m, p) => call(m, p, sessionId); await c('Runtime.enable'); await c('Page.enable');
  const run = async expression => { const r = await c('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value; };
  await c('Page.navigate', { url: 'http://127.0.0.1:' + port + '/' });
  for (let i = 0; i < 80; i++) { if (await run("typeof icon === 'function'")) break; await new Promise(r => setTimeout(r, 200)); }
  const ready = await run(`(() => {
    const ids=['astra','sol','terra','luna','opus','haiku','sonnet','gemini','grok','spark','strata'];
    document.body.innerHTML='<svg width="0" height="0" style="position:absolute"><defs>'+MODEL_ICONS.defs+'</defs></svg><main id="sheet"><h1>VANGUARD RELAY · MODEL SYMBOLS</h1><p>Approved full-colour artwork at page sizes</p><div class="grid">'+ids.map(id=>'<section><h2>'+id+'</h2>'+[20,24,56].map(n=>'<div class="cell"><span>'+n+'px</span>'+icon(id,n)+'</div>').join('')+'</section>').join('')+'</div></main>';
    document.head.innerHTML='<meta charset="utf-8"><style>*{box-sizing:border-box}body{margin:0;background:#080e18;color:#e8f2ff;font:14px system-ui,sans-serif}#sheet{padding:24px 30px}h1{font-size:20px;letter-spacing:.14em;margin:0 0 4px}p{color:#8ea1b9;margin:0 0 20px}.grid{display:grid;grid-template-columns:repeat(6,1fr);gap:12px}section{min-height:250px;padding:12px;background:#101a29;border:1px solid #24364d;border-radius:10px}h2{margin:0 0 8px;text-transform:uppercase;font-size:12px;letter-spacing:.1em;color:#a9c4de}.cell{height:66px;display:flex;align-items:center;gap:12px;border-top:1px solid #1b2b40;color:#7c90aa;font-size:11px}.cell span{width:30px}.shape-svg{flex:none;display:block;overflow:visible}@media(max-width:1000px){.grid{grid-template-columns:repeat(4,1fr)}}';
    return ids.map(id=>({id,markup:MODEL_ICONS.icons[modelKey(id)].length,svg:document.querySelector('section h2')!==null}));
  })()`);
  if (ready.length !== 11 || ready.some(x => !x.svg)) throw Error('Icon sheet DOM did not contain all eleven models');
  await c('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await new Promise(r => setTimeout(r, 250));
  const shot = await c('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
  fs.writeFileSync('data/model-icons-sheet.png', Buffer.from(shot.data, 'base64'));
  console.log(JSON.stringify({ output: 'data/model-icons-sheet.png', agents: ready.map(x => x.id), sizes: [20, 24, 56] }));
  sock.close(); setTimeout(() => process.exit(0), 100);
} finally { chrome.kill(); server.kill(); }
