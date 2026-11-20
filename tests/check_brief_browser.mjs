import fs from 'node:fs'; import cp from 'node:child_process';
const root=process.cwd();
fs.mkdirSync('data',{recursive:true});
const server=cp.spawn('python',['-u','tests/check_brief_browser.py'],{cwd:root,windowsHide:true});
const port=await new Promise((resolve,reject)=>{server.stdout.once('data',d=>resolve(Number(d.toString().trim())));server.on('error',reject);server.stderr.on('data',d=>process.stderr.write(d));});
const chrome=cp.spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',[...(process.env.RELAY_CAPTURE_TABS ? ['--new-window','--window-size=1460,1000'] : ['--headless=new']),'--disable-gpu','--no-sandbox','--no-first-run','--no-default-browser-check','--remote-debugging-port=0','--remote-allow-origins=*','--user-data-dir='+root+'/data/brief-chrome','about:blank'],{windowsHide:true});
try{
 const endpoint=await new Promise((resolve,reject)=>{let out='';chrome.stderr.on('data',d=>{out+=d;const m=out.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(m)resolve(m[1]);});chrome.on('error',reject);setTimeout(()=>reject(Error('Chrome startup timeout')),15000);});
 const sock=new WebSocket(endpoint);await new Promise(r=>sock.onopen=r);console.log('connected'); let seq=0;const pending=new Map();const errors=[];let chooserResolve;
 sock.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}if(m.method==='Page.fileChooserOpened' && chooserResolve){chooserResolve(m.params);chooserResolve=null;}if(m.method==='Runtime.exceptionThrown') errors.push(m.params.exceptionDetails);};
 const call=(method,params={},sessionId)=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});sock.send(JSON.stringify({id,method,params,sessionId}));});
 console.log('creating target'); const target=await call('Target.createTarget',{url:'about:blank'});const {sessionId}=await call('Target.attachToTarget',{targetId:target.targetId,flatten:true});
 const c=(m,p)=>call(m,p,sessionId);await c('Runtime.enable');await c('Page.enable');
 const run=async expression=>{const r=await c('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true,userGesture:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
 await c('Emulation.setDeviceMetricsOverride',{width:1440,height:860,deviceScaleFactor:1,mobile:false});
 console.log('navigating'); await c('Page.navigate',{url:'http://127.0.0.1:'+port+'/'});
 for(let i=0;i<60;i++){if(await run("typeof S !== 'undefined' && !!S.state"))break;await new Promise(r=>setTimeout(r,200));}
 const metrics=[];
 for(const [width,height] of [[1440,860],[390,800]]){
  await c('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width===390});
  await run("S.tab='mission';S.html={};render();");
  await new Promise(r=>setTimeout(r,400));
  metrics.push(await run(`({width:innerWidth,scroll:document.documentElement.scrollWidth,panels:document.querySelectorAll('.ready-brief').length,brief:document.querySelector('#mission').textContent.includes('WHAT WAS BUILT'),attention:document.querySelector('#att').textContent.includes('WHAT WAS BUILT'),stale:getComputedStyle(document.querySelector('#stale-bar')).display!=='none',meta:document.querySelector('#mission .ready-meta').textContent,icon:document.querySelector('link[rel="icon"]').getAttribute('href'),logo:document.querySelector('.brand img').complete&&document.querySelector('.brand img').naturalWidth>0})`));
  const shot=async name=>{const img=await c('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(`data/brief-${width}x${height}-${name}.png`,Buffer.from(img.data,'base64'));};
  if(width===390){
   await run("window.scrollTo(0,0)");await shot('top');
   await run("document.getElementById('att-panel').scrollIntoView({block:'start'})");await shot('attention');
   await run("document.getElementById('center-panel').scrollIntoView({block:'start'});document.getElementById('stream').scrollTop=document.getElementById('stream').scrollHeight");await shot('mission');
  } else { await run("document.getElementById('stream').scrollTop=document.getElementById('stream').scrollHeight");await shot('review'); }
 }
 if(metrics.some(m=>m.width!==m.scroll || !m.brief || !m.attention || !m.stale || !m.logo || !m.meta.includes('112') || !m.meta.includes('Tests pass') || m.icon!=='/assets/logo.svg')||errors.length)throw Error('Browser checks failed: '+JSON.stringify({metrics,errors}));
 const assets=await run("Promise.all(['/assets/logo.svg','/assets/apple-touch-icon.png','/manifest.webmanifest'].map(async u=>{const r=await fetch(u);return {url:u,status:r.status,type:r.headers.get('Content-Type')}}))");
 fs.writeFileSync('data/brief-browser-results.json',JSON.stringify({metrics,errors,assets},null,2));
 console.log(JSON.stringify({metrics,errors,assets},null,2));
 if(process.env.RELAY_CAPTURE_TABS){await c('Emulation.setDeviceMetricsOverride',{width:1440,height:860,deviceScaleFactor:1,mobile:false});await run("window.scrollTo(0,0);document.getElementById('stream').scrollTop=document.getElementById('stream').scrollHeight");await call('Target.activateTarget',{targetId:target.targetId});console.log('Ready for tab-strip capture');await new Promise(r=>setTimeout(r,55000));}
 sock.close();setTimeout(()=>process.exit(0),100);
}finally{chrome.kill();server.kill();}
