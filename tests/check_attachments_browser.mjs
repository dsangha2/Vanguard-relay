import fs from 'node:fs'; import cp from 'node:child_process';
const root=process.cwd();
fs.mkdirSync('data',{recursive:true});
const server=cp.spawn('python',['-u','tests/check_attachments_browser.py'],{cwd:root,windowsHide:true});
const port=await new Promise((resolve,reject)=>{server.stdout.once('data',d=>resolve(Number(d.toString().trim())));server.on('error',reject);server.stderr.on('data',d=>process.stderr.write(d));});
const chrome=cp.spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--disable-gpu','--no-sandbox','--no-first-run','--no-default-browser-check','--remote-debugging-port=0','--remote-allow-origins=*','--user-data-dir='+root+'/data/attachment-chrome','about:blank'],{windowsHide:true});
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
 // Exercise the real multiple-file picker with local files.
 fs.writeFileSync('data/review.txt','Review notes: attachment test.');
 await c('Page.setInterceptFileChooserDialog',{enabled:true});
 const chooser = new Promise(resolve=>chooserResolve=resolve);
 await run("document.querySelector('#cmd [data-pick]').click()");
 const chosen = await chooser;
 await c('DOM.setFileInputFiles',{backendNodeId:chosen.backendNodeId,files:[root+'/data/attachment-test.png',root+'/data/review.txt']});
 for(let i=0;i<100;i++){if(await run("fileIds('chat').length===2 && !fileProblem('chat')"))break;await new Promise(r=>setTimeout(r,100));}
 // Pasted files go to the waiting goal's note, without changing chat's files.
 await run("(()=>{const dt=new DataTransfer();dt.items.add(new File(['Pasted context'],'pasted.txt',{type:'text/plain'}));document.querySelector('#mission [data-note]').dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));})()");
 for(let i=0;i<60;i++){if(await run("fileIds('goal:'+S.goal.id).length===1"))break;await new Promise(r=>setTimeout(r,100));}
 if(!await run("fileIds('goal:'+S.goal.id).length===1 && fileIds('chat').length===2")) throw Error('Paste compose isolation failed');
 // Drop an invalid file in the new-goal form, check its error chip, then remove it.
 await run("openNew('sol');(()=>{const dt=new DataTransfer();dt.items.add(new File(['no'],'blocked.exe'));document.getElementById('new-form').dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}));})()");
 if(!await run("document.getElementById('new-form').textContent.includes('File type not allowed') && !!fileProblem('newgoal')"))throw Error('Invalid file chip missing');
 await run("document.querySelector('#new-form [data-file-remove]').click();document.getElementById('dlg').close()");
 console.log('uploaded'); const ids=await run("fileIds('chat')");if(ids.length!==2)throw Error('Expected 2 uploads');
 await run(`(async()=>{const g=S.goal; await api('/api/goals/'+g.id+'/note',{text:'Image and notes attached for review',attachments:${JSON.stringify(ids)}}); await tick();S.goal.attachments=S.goal.notes.at(-1).attachments;S.goal.turns=[{n:1,agent:'sol',model:'gpt-6.1-sol',status:'done',started:Date.now()/1000,final:'Reviewed the supplied files.',attachments:S.goal.attachments}]; S.html={};render();})()`);
 const metrics=[];
 for(const [width,height] of [[1440,860],[390,800]]){
  await c('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width===390});await new Promise(r=>setTimeout(r,350)); if(width===390) await run("document.getElementById('center-panel').scrollIntoView({block:'start'});document.getElementById('stream').scrollTop=210");
  metrics.push(await run(`({width:innerWidth,scroll:document.documentElement.scrollWidth,images:[...document.querySelectorAll('#mission img')].map(x=>({ok:x.complete&&x.naturalWidth>0})),text:document.querySelector('#mission').textContent.includes('review.txt')})`));
  const img=await c('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(`data/attachments-${width}x${height}.png`,Buffer.from(img.data,'base64'));
 }
 if(metrics.some(m=>m.width!==m.scroll || !m.text || m.images.some(x=>!x.ok)) || errors.length)throw Error('Browser checks failed');
 fs.writeFileSync('data/attachment-browser-results.json',JSON.stringify({ids,metrics,errors},null,2));
 console.log(JSON.stringify({ids,metrics,errors},null,2));
 sock.close(); setTimeout(()=>process.exit(0),100);
}finally{chrome.kill();server.kill();}



