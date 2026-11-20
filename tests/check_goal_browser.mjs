import fs from 'node:fs'; import cp from 'node:child_process';
const root=process.cwd();
const html=fs.readFileSync('web/index.html'), approved=fs.readFileSync('handoff/model-icons/model-icons.json');
if(!html.includes(approved))throw Error('Approved JSON must be embedded byte for byte');
fs.mkdirSync('data',{recursive:true});
const server=cp.spawn('python',['-u','tests/goal_browser_fixture.py'],{cwd:root,windowsHide:true});
const port=await new Promise((resolve,reject)=>{server.stdout.once('data',d=>resolve(Number(d.toString().trim())));server.on('error',reject);server.stderr.on('data',d=>process.stderr.write(d));});
const chrome=cp.spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--disable-gpu','--no-sandbox','--no-first-run','--no-default-browser-check','--remote-debugging-port=0','--remote-allow-origins=*','--user-data-dir='+root+'/data/goals-chrome','about:blank'],{windowsHide:true});
try{
 const endpoint=await new Promise((resolve,reject)=>{let out='';chrome.stderr.on('data',d=>{out+=d;const m=out.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(m)resolve(m[1]);});chrome.on('error',reject);setTimeout(()=>reject(Error('Chrome startup timeout')),15000);});
 const sock=new WebSocket(endpoint);await new Promise(r=>sock.onopen=r);console.log('connected'); let seq=0;const pending=new Map();const errors=[];let chooserResolve;
 sock.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}if(m.method==='Page.fileChooserOpened' && chooserResolve){chooserResolve(m.params);chooserResolve=null;}if(m.method==='Runtime.exceptionThrown') errors.push(m.params.exceptionDetails);};
 const call=(method,params={},sessionId)=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});sock.send(JSON.stringify({id,method,params,sessionId}));});
 console.log('creating target'); const target=await call('Target.createTarget',{url:'about:blank'});const {sessionId}=await call('Target.attachToTarget',{targetId:target.targetId,flatten:true});
 const c=(m,p)=>call(m,p,sessionId);await c('Runtime.enable');await c('Page.enable');
 const run=async expression=>{const r=await c('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true,userGesture:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
 await c('Emulation.setDeviceMetricsOverride',{width:1440,height:860,deviceScaleFactor:1,mobile:false});
 await c('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]}); console.log('navigating'); await c('Page.navigate',{url:'http://127.0.0.1:'+port+'/'});
 for(let i=0;i<60;i++){if(await run("typeof S !== 'undefined' && !!S.state"))break;await new Promise(r=>setTimeout(r,200));}

 const wait=async expression=>{for(let i=0;i<100;i++){if(await run(expression))return;await new Promise(r=>setTimeout(r,100));}throw Error('Timed out: '+expression);};
 const check=async(expression,label)=>{if(!await run(expression))throw Error(label);};
 await wait('!!S.goal');
 await run("window.askId=S.goal.id;window.sent=[];window.originalApi=api;window.api=async(url,body)=>{if(body)sent.push({url,body});return originalApi(url,body)}");
 await check("document.activeElement.matches('[data-note]')",'Initial question focus');
 await check("document.querySelector('#mission').textContent.includes('Auto picked sol to lead with terra and luna:') && document.querySelector('#mission').textContent.includes('\u2713 Build the overview') && document.querySelector('#mission').textContent.includes('\u25cb Review the result') && document.querySelector('#mission').textContent.includes('luna: 1 turn')",'Plan and auto explanation');
 await check("document.querySelector('#att').textContent.includes('luna ASKS') && !document.querySelector('#mission details').open && document.querySelectorAll('#mission .chip svg').length>=2",'Question disclosure and chip symbols');
 await run("openNew('')");
 await check("$('f-lead').value==='auto' && $('f-team-mode').value==='auto'",'Auto defaults');
 await run("$('f-goal').value='Build an automatic overview';$('f-go').click()");
 await wait("S.goal?.objective==='Build an automatic overview'");
 await check("sent.at(-1).body.lead==='auto' && sent.at(-1).body.team==='auto' && S.goal.auto_team.by==='coordinator' && S.goal.mode==='auto' && S.goal.team.length===3",'Auto HTTP creation');
 await run("window.autoId=S.goal.id;openNew('sol');$('f-goal').value='Build a manual overview';$('f-mode').value='manual';$('f-go').click()");
 await wait("S.goal?.objective==='Build a manual overview'");
 await check("sent.at(-1).body.lead==='sol' && Array.isArray(sent.at(-1).body.team) && S.goal.mode==='manual' && !S.goal.auto_team",'Manual HTTP creation');
 await run('selectGoal(askId)');await wait("S.goal?.id===askId && !!document.querySelector('#mission [data-note]')");
 await run("(()=>{const x=document.querySelector('#mission [data-note]');x.focus();x.value='Vanguard first';x.dispatchEvent(new Event('input',{bubbles:true}));render()})()");
 await check("document.activeElement.value==='Vanguard first'",'Focus and answer draft');
 for(const [width,height] of [[1440,860],[390,800]]){
  await c('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width===390});await new Promise(r=>setTimeout(r,250));
  await check('document.documentElement.scrollWidth <= innerWidth','Horizontal overflow '+width);
  for(const [name,expr] of [['fleet',"document.querySelector('#dock').scrollIntoView({block:'start'})"],['ask',"document.querySelector('#att .att-card').scrollIntoView({block:'start'})"],['plan',"document.querySelector('#stream').scrollTop=0;document.querySelector('#mission').scrollIntoView({block:'start'})"]]){
   await run(expr);const img=await c('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(`data/${name}-${width}x${height}.png`,Buffer.from(img.data,'base64'));
  }
  await run('document.activeElement.blur();selectGoal(autoId)');await wait('S.goal?.id===autoId');
  await run("document.querySelector('#stream').scrollTop=0;document.querySelector('#mission').scrollIntoView({block:'start'})");
  const img=await c('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(`data/auto-${width}x${height}.png`,Buffer.from(img.data,'base64'));
  await run('selectGoal(askId)');await wait('S.goal?.id===askId');
 }
 await check("goalQuestion({...S.goal,question:null,reason:'Tests could not be compared'})===null",'Non-question waiting reason');
 await run("(()=>{const x=document.querySelector('#mission [data-note]');x.value='Vanguard first';x.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#mission [data-act=continue]').click()})()");
 await wait("S.goal?.id===askId && S.goal.status==='queued'");
 await check("sent.at(-1).body.action==='continue' && sent.at(-1).body.agent==='luna' && S.goal.next.agent==='luna' && S.goal.next.note==='Vanguard first' && !S.goal.question",'Answer HTTP routing');
 await run('Promise.all([...symbolTextures.values()].map(x=>x.ready))');
 await check("document.querySelectorAll('body > svg defs').length===1 && icon('unknown',20)===icon('other',20) && symbolTexture('sol')===symbolTexture('sol')",'Shared definitions, fallback, and texture cache');
 const symbols=await run(`(()=>{
  feedScene();selectAgent('luna');frame(performance.now());
  const m=mods.get('luna');const selected=m.sprite.isSprite && pick(m.sx,m.sy)==='luna';
  ST.active.add('luna');frame(performance.now());const working=m.beam.visible && m.sprite.material.opacity===1;
  ST.active.clear();frame(performance.now());const dimmed=m.sprite.material.opacity<1;
  return {selected,working,dimmed,count:mods.size,valid:[...mods.values()].every(x=>{
   const canvas=x.sprite.material.map.image,rgba=canvas.getContext('2d').getImageData(0,0,256,256).data;
   return canvas.width===256 && rgba.filter((v,i)=>i%4===3&&v>0).length>500 && x.pts.geometry.attributes.position.array.every(Number.isFinite);
  })};
 })()`);
 if(!symbols.selected||!symbols.working||!symbols.dimmed||!symbols.valid||symbols.count!==11)throw Error('Symbols: '+JSON.stringify(symbols));
 for(const [width,height] of [[1440,860],[390,800]]){
  await c('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width===390});
  await run("setIdle(false);selectAgent('');S.tab='mission';render();selectAgent('luna');document.querySelector('#dock-panel').scrollIntoView({block:'start'});size();frame(performance.now())");
  const shot=async name=>{await new Promise(r=>setTimeout(r,250));const img=await c('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(`data/symbols-${width}x${height}-${name}.png`,Buffer.from(img.data,'base64'));};
  await check("[...document.querySelectorAll('.shape-svg')].every(x=>x.getBoundingClientRect().width===0 || x.getBoundingClientRect().width>=18)",'Minimum symbol size');
  await check("document.querySelector('#agent-card svg').style.width==='56px' && document.querySelector('#dock svg').style.width==='24px'",'Dock and status sizes');
  await shot('status-fleet');
  await run("document.querySelector('#center-panel').scrollIntoView({block:'start'});document.querySelector('#stream').scrollTop=0");await shot('chain');
  await run("document.querySelector('#orbital-panel').scrollIntoView({block:'start'});size();frame(performance.now())");await shot('orbit');
  await run("selectAgent('');setIdle(true);size();frame(performance.now())");
  await check("[...mods.values()].every(m=>m.g.visible && m.label.style.display==='block')",'Centre symbols and labels');
  await shot('centre');await run('setIdle(false)');
  await run("openNew('sol')");
  await check("[...document.querySelectorAll('#f-team svg')].every(x=>x.style.width==='20px')",'Team picker size');
  await shot('team');await run("$('dlg').close()");
 }
 await c('Emulation.setDeviceMetricsOverride',{width:1440,height:860,deviceScaleFactor:1,mobile:false});
 await c('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
 await c('Page.navigate',{url:'http://127.0.0.1:'+port+'/'});
 await wait("typeof S!=='undefined' && !!S.state && !still");
 await run("selectGoal(S.state.goals.find(g=>g.title==='Build the project overview').id)");
 await wait("!!S.goal && S.goal.title==='Build the project overview'");
 await run("Promise.all([...symbolTextures.values()].map(x=>x.ready))");
 await run("window.motionAgent=mods.get('luna');motionAgent.orbit=true;motionAgent.vis=1;motionAgent.on=1;ST.active.add('luna');window.beforePulse=motionAgent.sprite.scale.x;window.beforeHalo=motionAgent.pts.rotation.z;window.beforeBob=motionAgent.g.position.y");
 await new Promise(r=>setTimeout(r,350));
 const motion=await run("({pulse:motionAgent.sprite.scale.x!==beforePulse,halo:motionAgent.pts.rotation.z!==beforeHalo,bob:motionAgent.g.position.y!==beforeBob,stream:motionAgent.beam.visible,on:motionAgent.on,active:[...ST.active]})");
 if(!motion.pulse||!motion.halo||!motion.bob||!motion.stream)throw Error('Working motion: '+JSON.stringify(motion));
 await run("selectAgent('');window.clickPoint=(()=>{const r=cv.getBoundingClientRect();return {x:r.left+motionAgent.sx,y:r.top+motionAgent.sy}})()");
 const point=await run('clickPoint');
 await c('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:1});
 await c('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',clickCount:1});
 await check("S.agent==='luna'",'Sprite click selects its agent');
 const moving=await c('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync('data/symbols-1440x860-working.png',Buffer.from(moving.data,'base64'));
 console.log(JSON.stringify({http:'passed',symbols}));
 if(errors.length)throw Error(JSON.stringify(errors));console.log('Goal browser checks passed');
 sock.close(); setTimeout(()=>process.exit(0),100);
}finally{chrome.kill();server.kill();}




