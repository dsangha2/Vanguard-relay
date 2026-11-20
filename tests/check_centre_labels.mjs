import fs from 'node:fs'; import cp from 'node:child_process';
const root=process.cwd();
const server=cp.spawn('python',['-u','tests/check_centre_labels.py'],{cwd:root,windowsHide:true});
const port=await new Promise((res,rej)=>{server.stdout.once('data',d=>res(Number(d.toString().trim())));server.on('error',rej);server.stderr.on('data',d=>process.stderr.write(d));});
const chrome=cp.spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--disable-gpu','--no-sandbox','--no-first-run','--remote-debugging-port=0','--remote-allow-origins=*','--user-data-dir='+root+'/data/centre-chrome','about:blank'],{windowsHide:true});
try{
 const endpoint=await new Promise((res,rej)=>{let out='';chrome.stderr.on('data',d=>{out+=d;const m=out.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(m)res(m[1]);});setTimeout(()=>rej(Error('timeout')),15000);});
 const sock=new WebSocket(endpoint);await new Promise(r=>sock.onopen=r);let seq=0;const pending=new Map();
 sock.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}};
 const call=(method,params={},sessionId)=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});sock.send(JSON.stringify({id,method,params,sessionId}));});
 const t=await call('Target.createTarget',{url:'about:blank'});const {sessionId}=await call('Target.attachToTarget',{targetId:t.targetId,flatten:true});
 const c=(m,p)=>call(m,p,sessionId);await c('Runtime.enable');await c('Page.enable');
 const run=async x=>{const r=await c('Runtime.evaluate',{expression:x,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
 await c('Emulation.setDeviceMetricsOverride',{width:1440,height:860,deviceScaleFactor:1,mobile:false});
 await c('Page.navigate',{url:'http://127.0.0.1:'+port+'/'});
 for(let i=0;i<60;i++){if(await run("typeof S !== 'undefined' && !!S.state"))break;await new Promise(r=>setTimeout(r,200));}
 for(const [w,h] of [[1440,860],[390,800]]){
  await c('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:w===390});
  await run("S.idle||setIdle(true)");
  await new Promise(r=>setTimeout(r,3500));
  const boxes=await run(`[...mods.values()].filter(m=>m.vis>.5).map(m=>{const r=m.label.getBoundingClientRect(),o=document.getElementById('gl').getBoundingClientRect();return {id:m.id,l:r.left-o.left,t:r.top-o.top,r:r.right-o.left,b:r.bottom-o.top,sx:m.sx,sy:m.sy,half:m.sprite.scale.y*m.g.scale.y*innerHeight/(4*Math.tan(cam.fov*Math.PI/360)*(cam.position.z-m.w.z))}})`);
  const bad=[];
  for(const a of boxes){ for(const b of boxes){ if(a===b)continue;
    if(a.l<b.r&&b.l<a.r&&a.t<b.b&&b.t<a.b)bad.push(a.id+' label x '+b.id+' label');
    const hs=b.half*0.8; if(a.l<b.sx+hs&&b.sx-hs<a.r&&a.t<b.sy+hs&&b.sy-hs<a.b)bad.push(a.id+' label x '+b.id+' symbol'); } }
  console.log(w+'x'+h,'agents',boxes.length,'overlaps',JSON.stringify(bad));
  const img=await c('Page.captureScreenshot',{format:'png'});fs.writeFileSync(`data/centre-${w}x${h}.png`,Buffer.from(img.data,'base64'));
 }
}finally{chrome.kill();server.kill();}
