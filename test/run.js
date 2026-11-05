const {chromium}=require('playwright');const fs=require('fs');const path=require('path');const http=require('http');
const site=path.resolve(__dirname,'../site');const out=__dirname+'/out';fs.mkdirSync(out,{recursive:true});
const types={'.html':'text/html','.js':'text/javascript','.png':'image/png','.webmanifest':'application/manifest+json'};
const srv=http.createServer((q,r)=>{let p=decodeURIComponent(q.url.split('?')[0]);if(p.endsWith('/'))p+='index.html';const f=path.join(site,p);if(!fs.existsSync(f)){r.statusCode=404;return r.end()}r.setHeader('content-type',types[path.extname(f)]||'text/plain');r.end(fs.readFileSync(f))}).listen(8766);
const U='http://localhost:8766/';
const ok=(c,m)=>{console.log((c?'PASS ':'FAIL ')+m);if(!c)process.exitCode=1};
const cfg='window.SPOOL_CONFIG={siteUrl:"https://jwilss.github.io/spool-stock/",repo:"jwilss/spool-stock",readerUrl:"https://claude.ai/artifact/x",firebase:{projectId:"x"}};';
const stubs=async ctx=>{
 await ctx.addInitScript({path:__dirname+'/mock-firebase.js'});
 await ctx.route(/vendor\/firebase-/,r=>r.fulfill({contentType:'text/javascript',body:''}));
 await ctx.route(/config\.js/,r=>r.fulfill({contentType:'text/javascript',body:cfg}));
 await ctx.route(/fonts\.g/,r=>r.abort());
};
const dbOf=p=>p.evaluate(()=>JSON.parse(localStorage.getItem('__fb')));
(async()=>{
 const b=await chromium.launch({args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream','--use-file-for-fake-video-capture='+out+'/feed.mjpeg']});
 const ctx=await b.newContext({viewport:{width:400,height:820},serviceWorkers:'block',acceptDownloads:true,permissions:['camera']});
 await stubs(ctx);
 const p=await ctx.newPage();const errs=[];
 p.on('pageerror',e=>errs.push(e.message));p.on('console',m=>{if(m.type()==='error'&&!/fonts|ERR_FAILED|404/.test(m.text()))errs.push(m.text())});
 const uid='users/u1/';
 await p.goto(U);await p.waitForSelector('#authForm');
 await p.fill('#authEmail','a@b.co');await p.fill('#authPass','nope12');await p.click('#authForm [type=submit]');await p.waitForSelector('#authErr:not([hidden])');ok(true,'bad sign in shows message');
 await p.click('[data-act=authCreate]');await p.waitForSelector('.racks');
 await p.waitForTimeout(300);let db=await dbOf(p);
 ok(Object.keys(db).filter(k=>k.startsWith(uid+'locations/')).length===12,'storages set up on first sign in');
 await p.waitForFunction(()=>document.querySelectorAll('.rack').length>=4);
 await p.screenshot({path:out+'/stock-phone.png',fullPage:true});
 ok((await p.textContent('.racks')).includes('Space Pi Plus (Alduin)')&&(await p.textContent('.racks')).includes('AMS 2 (Betsy)'),'printers shown on racks');
 await p.reload();await p.waitForSelector('.racks');ok(true,'stays signed in after reload');
 await p.waitForTimeout(200);db=await dbOf(p);ok(Object.keys(db).filter(k=>k.includes('/locations/')).length===12,'storages not seeded twice');

 const add=async(b,m,c,q)=>{await p.evaluate(()=>{location.hash='add'});await p.waitForSelector('#f_brand');await p.fill('#f_brand',b);await p.fill('#f_material',m);await p.fill('#f_colour',c);if(q)await p.fill('#f_qty',String(q));await p.click('#saveSpool');await p.waitForSelector('.spoolhead')};
 await add('eSUN','PETG HF','Galaxy Black');await add('Polymaker','TPU 95A','White',2);await add('Bambu Lab','ASA','Grey');
 ok(p.url().endsWith('#s0004'),'spool codes run in order '+p.url());

 // saving with no signal must not hang the app
 await p.evaluate(()=>{window.__offline=true});
 await p.click('[data-loc="rack-top"]');await p.waitForSelector('#toast:not([hidden])');
 db=await dbOf(p);ok(db[uid+'spools/s0004'].loc==='rack-top','move works with no signal');
 await add('SUNLU','PLA','Red');ok(p.url().endsWith('#s0005'),'adding works with no signal '+p.url());
 await p.evaluate(()=>{window.__offline=false});

 // dryer warning
 await p.evaluate(()=>{location.hash='s0004'});await p.waitForSelector('.spoolhead');
 await p.click('[data-loc="pi-1"]');await p.waitForFunction(()=>document.querySelector('#toast').textContent.includes('stops at 70'));ok(true,'warns ASA is too hot for the Space Pi Plus');
 await p.click('[data-loc="rack-top"]');await p.waitForTimeout(100);

 // stickers: real PDF download
 await p.evaluate(()=>{location.hash='stickers'});await p.waitForSelector('.sheetmap');
 const [dl]=await Promise.all([p.waitForEvent('download'),p.click('[data-act=pdf]')]);await dl.saveAs(out+'/stickers.pdf');
 ok(dl.suggestedFilename()==='spool-stickers.pdf','PDF downloads');await p.waitForTimeout(200);
 db=await dbOf(p);ok(db[uid+'spools/s0001'].labelled===true,'printed stickers leave the queue');

 // live camera: feed shows s0002, then a storage sticker
 await p.evaluate(()=>{location.hash='scan'});await p.waitForSelector('#camStart');
 await p.click('#camStart');await p.waitForFunction(()=>document.querySelector('#cam')&&document.querySelector('#cam').readyState>=2,{timeout:8000});
 await p.waitForFunction(()=>/S0002.*Now scan a storage/.test(document.querySelector('#scanState').textContent),{timeout:20000});ok(true,'camera picks up a spool sticker');
 await p.screenshot({path:out+'/scan-phone.png',fullPage:true});
 await p.waitForSelector('#modal:not([hidden])',{timeout:20000});ok((await p.textContent('#modal')).includes('TPU'),'TPU warning appears on a scan too');await p.click('#askBtns .go');
 await p.waitForFunction(()=>/S0002 is in AMS 1 \(Betsy\) Slot 1/.test(document.querySelector('#scanState').textContent),{timeout:20000});ok(true,'camera moves it on the storage sticker');
 db=await dbOf(p);ok(db[uid+'spools/s0002'].loc==='ams1-1','move saved: '+db[uid+'spools/s0002'].loc);
 ok(await p.evaluate(()=>!document.querySelector('#cam').paused),'camera keeps running after a move');
 await p.click('[data-nav=stock]');await p.waitForSelector('.racks');ok(await p.evaluate(()=>!document.querySelector('#cam')),'camera stops on leaving');

 // import from Spool Reader
 const logo='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
 const imp=fs.existsSync(out+'/reader-export.json')?JSON.parse(fs.readFileSync(out+'/reader-export.json')):{format:'spool-stock-import',version:1,batch:'abc123',spools:[{brand:'Testbrand',material:'ASA Aero',colour:'Sand',hex:'#D9C7A0',finish:'Matte',netG:750,qty:2,dia:1.75,nMin:250,nMax:265,bMin:null,bMax:null,dT:null,dH:null},{brand:'eSUN',material:'PETG HF',colour:'Blue',hex:'#0057B8',finish:'',netG:1000,qty:1,dia:1.75,nMin:null,nMax:null,bMin:null,bMax:null,dT:null,dH:null}],logos:{Testbrand:{src:logo,w:1,h:1}}};
 await p.evaluate(()=>{location.hash='import'});await p.waitForSelector('#importText');
 await p.fill('#importText','{"nope":1}');await p.click('[data-act=importPaste]');await p.waitForSelector('#importErr:not([hidden])');ok(true,'wrong file refused');
 await p.fill('#importText',JSON.stringify(imp));await p.click('[data-act=importPaste]');await p.waitForSelector('[data-act=importGo]');
 await p.screenshot({path:out+'/import-phone.png',fullPage:true});
 ok((await p.textContent('[data-act=importGo]')).includes('Add 3 spools'),'import counts quantities');
 await p.selectOption('#importLoc','rack-bottom');await p.click('[data-act=importGo]');await p.waitForSelector('.sheetmap');await p.waitForTimeout(200);
 db=await dbOf(p);const a1=Object.entries(db).filter(([k,v])=>k.includes('/spools/')&&v.brand==='Testbrand');
 ok(a1.length===2,'import adds both spools');ok(a1[0][1].nMin===250&&a1[0][1].bMax===100&&!!a1[0][1].hex,'label values kept, gaps filled typical');ok(a1[0][1].loc==='rack-bottom'&&a1[0][1].labelled===false,'filed and queued for stickers');
 ok(!!db[uid+'brands/testbrand'].logo,'logo imported');ok(db[uid+'config/main'].imported.includes(imp.batch),'import remembered');
 await p.evaluate(()=>{location.hash='import'});await p.waitForSelector('#importText');await p.fill('#importText',JSON.stringify(imp));await p.click('[data-act=importPaste]');await p.waitForSelector('[data-act=importGo]');
 ok((await p.textContent('#main')).includes('already imported'),'warns on a repeat import');await p.click('[data-act=importCancel]');

 // stock list for Spool Reader
 await p.evaluate(()=>{location.hash='setup'});await p.waitForSelector('[data-act=listSave]');
 await p.screenshot({path:out+'/setup-phone.png',fullPage:true});
 const [dl2]=await Promise.all([p.waitForEvent('download'),p.click('[data-act=listSave]')]);await dl2.saveAs(out+'/spool-stock-list.json');
 const list=JSON.parse(fs.readFileSync(out+'/spool-stock-list.json'));ok(list.format==='spool-stock-list'&&list.spools.length>=8&&list.spools[0].storage!==undefined,'stock list downloads');

 // sign out and the Android app's limits
 await p.evaluate(()=>{location.hash='setup'});await p.waitForSelector('[data-act=signOut]');await p.click('[data-act=signOut]');await p.waitForSelector('#authForm');ok(true,'sign out');
 ok((await p.evaluate(()=>window.__fbErrors)).length===0,'nothing Firestore would refuse: '+JSON.stringify(await p.evaluate(()=>window.__fbErrors)));
 await ctx.close();

 const a=await b.newContext({viewport:{width:400,height:820},serviceWorkers:'block',userAgent:'Mozilla/5.0 (Linux; Android 14) Chrome/120 Mobile SpoolStockApp'});await stubs(a);
 const ap=await a.newPage();await ap.goto(U);await ap.evaluate(()=>{localStorage.setItem('__fbUsers','{"a@b.co":"secret1"}');localStorage.setItem('__fbSession','a@b.co')});await ap.goto(U+'#stickers');await ap.reload();await ap.waitForSelector('.sheetmap');
 ok((await ap.textContent('#main')).includes("can't save PDFs"),'Android app sends printing to the PC');await a.close();

 // offline start with the real service worker
 const c=await b.newContext({viewport:{width:400,height:820}});await c.route(/config\.js/,r=>r.fulfill({contentType:'text/javascript',body:'window.SPOOL_CONFIG={};'}));
 const w=await c.newPage();await w.goto(U);await w.waitForFunction(()=>navigator.serviceWorker.ready.then(()=>true));await w.waitForTimeout(500);await w.reload();await w.waitForTimeout(400);
 await c.setOffline(true);await w.reload();await w.waitForSelector('.nav');ok((await w.textContent('.brand')).includes('Spool Stock'),'opens with no connection');await c.close();

 console.log('page errors:',errs);ok(errs.length===0,'no page errors');
 await b.close();srv.close();
})().catch(e=>{console.error(e);process.exit(1)});
