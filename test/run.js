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
 const fakeLogo=fs.readFileSync(__dirname+'/fixtures/logo.png');
 const allow={'access-control-allow-origin':'*'};
 await ctx.route(/simple-icons/,r=>r.fulfill({status:200,contentType:'image/svg+xml',headers:allow,body:'<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M2 2h20v20H2zM8 8v8h8V8z"/></svg>'}));
 await ctx.route(/commons\.wikimedia\.org/,r=>r.fulfill({status:200,contentType:'application/json',headers:allow,body:JSON.stringify({query:{pages:{1:{imageinfo:[{mime:'image/svg+xml',url:'https://upload.wikimedia.org/a.svg',thumburl:'https://upload.wikimedia.org/thumb/a.png'}]}}}})}));
 await ctx.route(/upload\.wikimedia\.org|img\.logo\.dev/,r=>r.fulfill({status:200,contentType:'image/png',headers:allow,body:fakeLogo}));
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

 // logo lookup: exact vector, Wikimedia choices, Logo.dev and upload
 await p.evaluate(()=>{location.hash='add'});await p.waitForSelector('#f_brand');
 await p.fill('#f_brand','Bambu Lab');await p.fill('#f_material','PLA Silk Multi');await p.fill('#f_colour','Aurora');
 await p.selectOption('#f_pattern','split');await p.locator('[data-act=colourAdd]').click();await p.locator('[data-act=colourAdd]').click();
 await p.evaluate(()=>{const set=(el,v)=>{el.value=v;el.dispatchEvent(new Event('input',{bubbles:true}))};set(document.querySelector('#f_hex'),'#c8102e');const c=document.querySelectorAll('[data-colour]');set(c[0],'#ffd100');set(c[1],'#00843d');set(c[2],'#0057b8')});
 await p.waitForTimeout(100);await p.screenshot({path:out+'/add-multi.png',fullPage:true});
 ok((await p.$$('#preview polygon')).length>=4,'split colour box drawn as triangles');
 await p.click('#saveSpool');await p.waitForSelector('.spoolhead');await p.waitForFunction(()=>{const d=JSON.parse(localStorage.getItem('__fb'));const b=d['users/u1/brands/bambu-lab'];return b&&b.logo});
 db=await dbOf(p);let bl=db[uid+'brands/bambu-lab'];ok(bl.logoW>=1000||bl.logoH>=1000,'vector logo saved at print size '+bl.logoW+'x'+bl.logoH);ok(bl.logoFrom.includes('Simple Icons'),'exact match added automatically');
 await add('Gradient Co','PLA Gradient','Sunset');
 await p.evaluate(()=>{location.hash=location.hash.replace('#','edit-')});await p.waitForSelector('#f_pattern');await p.selectOption('#f_pattern','gradient');await p.click('#saveSpool');await p.waitForSelector('.spoolhead');
 await p.evaluate(()=>{location.hash='setup'});await p.waitForSelector('[data-act=logosAll]');
 await p.click('[data-act=logoFind][data-v="eSUN"]');await p.waitForSelector('.logoPick');
 ok((await p.textContent('#logoPanel')).includes('Vector, Wikimedia'),'Wikimedia choices listed');
 await p.screenshot({path:out+'/logo-panel.png',fullPage:true});
 await p.click('.logoPick');await p.waitForFunction(()=>{const d=JSON.parse(localStorage.getItem('__fb'));return d['users/u1/brands/esun']&&d['users/u1/brands/esun'].logo});ok(true,'picked logo saved');
 const logoPixels=await p.evaluate(()=>{const src=JSON.parse(localStorage.getItem('__fb'))['users/u1/brands/esun'].logo;return new Promise(r=>{const i=new Image();i.onload=()=>{const c=document.createElement('canvas');c.width=i.width;c.height=i.height;const x=c.getContext('2d');x.drawImage(i,0,0);const d=x.getImageData(0,0,c.width,c.height).data;let colour=0,clear=0;for(let k=0;k<d.length;k+=4){if(d[k+3]>0&&(d[k]||d[k+1]||d[k+2]))colour++;if(d[k+3]===0)clear++}r({colour,clear})};i.src=src})});
 ok(logoPixels.colour===0&&logoPixels.clear>0,'logos are pure black on transparent');
 await p.evaluate(()=>{location.hash='stickers'});await p.waitForSelector('.sheetmap');await p.waitForTimeout(300);
 await p.click('.cell:nth-child(1)');await p.waitForTimeout(100);
 const [dl3]=await Promise.all([p.waitForEvent('download'),p.click('[data-act=pdf]')]);await dl3.saveAs(out+'/stickers2.pdf');

 // logos only file (needs a private logo file, which isn't kept in the repo)
 const lp=process.env.LOGO_FILE;
 if(lp){
 const lf=JSON.parse(fs.readFileSync(lp));
 await p.evaluate(()=>{location.hash='import'});await p.waitForSelector('#importText');await p.fill('#importText',JSON.stringify(lf));await p.click('[data-act=importPaste]');await p.waitForSelector('[data-act=importGo]');
 ok((await p.textContent('[data-act=importGo]')).includes('Add 4 logos'),'logo file recognised');await p.screenshot({path:out+'/logos-import.png',fullPage:true});
 await p.click('[data-act=importGo]');await p.waitForSelector('#apiKeyIn, [data-act=logosAll]');await p.waitForTimeout(300);
 db=await dbOf(p);ok(['esun','prusament','hatchbox','polymaker'].every(k=>db[uid+'brands/'+k]&&db[uid+'brands/'+k].logo&&db[uid+'brands/'+k].logoW>=1000),'all four logos stored at print size');
 await add('Prusament','PETG','Galaxy Black');await add('Hatchbox','PLA','Red');await add('Polymaker','PolyTerra PLA','Teal');
 await p.evaluate(()=>{location.hash='stickers'});await p.waitForSelector('.sheetmap');await p.waitForTimeout(200);await p.click('.cell:nth-child(1)');await p.waitForTimeout(100);
 const [dl4]=await Promise.all([p.waitForEvent('download'),p.click('[data-act=pdf]')]);await dl4.saveAs(out+'/stickers3.pdf');

 }

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
