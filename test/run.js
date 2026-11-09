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
 const stores=d=>Object.keys(d).filter(k=>k.startsWith(uid+'locations/'));
 ok(stores(db).length===0,'a new account starts with no storages');
 ok((await p.textContent('.racks')).includes('Set up your storage'),'stock page asks you to set up storage');

 // building storages from scratch
 const addStore=async(kind,o={})=>{await p.evaluate(()=>{location.hash='store-new'});await p.waitForSelector('.typepick');await p.check(`input[name=s_kind][value=${kind}]`);await p.waitForSelector('#s_name');if(o.check)await o.check();for(const k of['name','group','capacity','maxC','warn','qty'])if(o[k]!=null)await p.fill('#s_'+k,String(o[k]));await p.click('#saveStore');await p.waitForSelector('.storerow')};
 await addStore('ams',{name:'AMS1 1',check:async()=>{
  ok(await p.inputValue('#s_capacity')==='4'&&await p.inputValue('#s_spaces')==='1','multi-material unit defaults to 4 numbered spaces');
  ok((await p.inputValue('#s_warn')).includes('TPU'),'multi-material unit warns about TPU by default');
  ok((await p.$$('#storePreview .slot')).length===4,'preview shows 4 spaces');await p.screenshot({path:out+'/store-phone.png',fullPage:true});}});
 await addStore('ams',{name:'Unit 1',qty:2,group:'Printer A'});
 await addStore('dryer',{name:'Dryer',capacity:2,maxC:70});
 await addStore('shelf',{name:'Rack top',group:'Rack',capacity:10,check:async()=>{ok(await p.inputValue('#s_spaces')==='0'&&await p.inputValue('#s_capacity')==='','shelf defaults to loose with no limit');ok(await p.$('#s_maxC')===null,'only a dryer asks for a temperature');}});
 await addStore('shelf',{name:'Rack bottom',group:'Rack',capacity:10});
 db=await dbOf(p);ok(stores(db).length===6,'storages added, two at once: '+stores(db).map(k=>k.split('/').pop()));
 ok(db[uid+'locations/ams1-1'].capacity===4&&db[uid+'locations/ams1-1'].spaces===true&&db[uid+'locations/unit-2'].name==='Unit 2','a multi-material unit is one storage with 4 spaces');
 await p.evaluate(()=>{location.hash='stock'});await p.waitForFunction(()=>document.querySelectorAll('.rack').length===6);
 await p.screenshot({path:out+'/stock-phone.png',fullPage:true});
 ok((await p.$$('.slot')).length===4*3+2,'spaces drawn inside each storage');
 ok((await p.textContent('#main')).includes('Printer A'),'groups shown on the stock page');
 await p.reload();await p.waitForSelector('.racks');ok(true,'stays signed in after reload');
 await p.waitForTimeout(200);db=await dbOf(p);ok(stores(db).length===6,'storages not added twice');
 await p.evaluate(()=>{location.hash='setup'});await p.waitForSelector('.storerow');const firstName=()=>p.textContent('.storerow b');
 ok(await firstName()==='AMS1 1','setup lists storages in order');await p.click('[data-act=storeShift][data-v="ams1-1"][data-d="1"]');await p.waitForFunction(()=>document.querySelector('.storerow b').textContent==='Unit 1');await p.click('[data-act=storeShift][data-v="unit-1"][data-d="1"]');await p.waitForFunction(()=>document.querySelector('.storerow b').textContent==='AMS1 1');ok(true,'storages can be reordered');

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
 await p.click('[data-loc="dryer"]');await p.waitForFunction(()=>document.querySelector('#toast').textContent.includes('stops at 70'));ok(true,'warns ASA is too hot for the dryer');
 await p.click('[data-loc="rack-top"]');await p.waitForTimeout(100);

 // stickers: real PDF download
 await p.evaluate(()=>{location.hash='stickers'});await p.waitForSelector('.sheetmap');
 const [dl]=await Promise.all([p.waitForEvent('download'),p.click('[data-act=pdf]')]);await dl.saveAs(out+'/stickers.pdf');
 ok(dl.suggestedFilename()==='spool-stickers.pdf','PDF downloads');await p.waitForTimeout(200);
 db=await dbOf(p);ok(db[uid+'spools/s0001'].labelled===true,'printed stickers leave the queue');
 // the 99.1 x 38.1 mm sheet, 14 per sheet: every sticker lands in its own label, and its QR code scans
 {const before=await p.evaluate(()=>({...(S.settings.sheet||{})}));
  await p.evaluate(()=>{for(const id of Object.keys(S.spools).slice(0,5)) col('spools').doc(id).update({labelled:false});location.hash='stock'});await p.waitForTimeout(150);
  await p.evaluate(()=>{location.hash='stickers'});await p.waitForSelector('#sheetSel');await p.selectOption('#sheetSel','l7163');await p.waitForSelector('.sheetmap');
  ok(await p.$$eval('.sheetmap > *',e=>e.length)===14,'the 14 per sheet size shows 14 places to start from');
  const [d14]=await Promise.all([p.waitForEvent('download'),p.click('[data-act=pdf]')]);await d14.saveAs(out+'/stickers-14.pdf');
  const r=JSON.parse(require('child_process').execSync(`python3 -I ${__dirname}/sheet_check.py ${out}/stickers-14.pdf 99.1 38.1 2 7 4.65 15.15 101.6 38.1`).toString());
  ok(r.codes>=5&&r.codes===r.inside&&r.pages===1,`99.1 x 38.1 mm sheet: ${r.codes} QR codes scan, each inside its own label (${r.inside} inside), on one A4 page`);
  ok(r.inkOutside===0,'nothing is printed in the gaps between labels: '+r.inkOutside+' stray marks');
  ok(r.right===r.codes&&r.size>=31,`on the wide labels each QR code is on the far right and the full height of the label: ${r.size} mm`);
  const svg=await p.evaluate(()=>{const s=Object.values(S.spools).find(x=>x.brand==='eSUN')||Object.values(S.spools)[0];return opsToSVG(spoolOps(s,99.1,38.1),99.1,38.1)});
  ok(/Diameter/.test(svg)&&/1\.75 mm/.test(svg)&&/Nozzle/.test(svg),'the wide stickers add more about the filament, such as its diameter');
  const sizes=await p.evaluate(()=>{const out=new Set();for(const s of Object.values(S.spools)) for(const o of spoolOps(s,99.1,38.1)) if(o.t==='text'&&o.pt<8&&o.text!=='3') out.add(o.pt);return [...out]});
  ok(sizes.length===1&&sizes[0]===7.5,'every detail line on the wide stickers prints at full size: '+sizes.join(', '));
  const narrow=await p.evaluate(()=>{const s=Object.values(S.spools)[0];return spoolOps(s,63.5,38.1).find(o=>o.t==='qr')});
  ok(narrow.x<5&&narrow.s<27,'the smaller stickers keep their layout, with the code on the left');
  await p.evaluate(b=>{saveConfig({sheet:b})},before);await p.waitForTimeout(100);}

 // live camera: feed shows s0002, then a storage sticker
 await p.evaluate(()=>{location.hash='scan'});await p.waitForSelector('#camStart');
 await p.click('#camStart');await p.waitForFunction(()=>document.querySelector('#cam')&&document.querySelector('#cam').readyState>=2,{timeout:8000});
 await p.waitForFunction(()=>/S0002.*Now scan a storage/.test(document.querySelector('#scanState').textContent),{timeout:20000});ok(true,'camera picks up a spool sticker');
 await p.screenshot({path:out+'/scan-phone.png',fullPage:true});
 await p.waitForSelector('#modal:not([hidden])',{timeout:20000});ok((await p.textContent('#modal')).includes('TPU'),'TPU warning appears on a scan too');await p.click('#askBtns .go');
 await p.waitForFunction(()=>/S0002 is in AMS1 1, space 1/.test(document.querySelector('#scanState').textContent),{timeout:20000});ok(true,'camera moves it on the storage sticker');
 db=await dbOf(p);ok(db[uid+'spools/s0002'].loc==='ams1-1'&&db[uid+'spools/s0002'].slot===1,'move saved into the first free space: '+db[uid+'spools/s0002'].loc);
 ok(await p.evaluate(()=>!document.querySelector('#cam').paused),'camera keeps running after a move');
 await p.click('[data-nav=stock]');await p.waitForSelector('.racks');ok(await p.evaluate(()=>!document.querySelector('#cam')),'camera stops on leaving');

 // spaces: fill a unit, swap when full, pick a space, room left on add
 const open=async id=>{await p.evaluate(i=>{location.hash=i},id);await p.waitForSelector('[data-loc="unit-1"]')};
 for(const id of['s0001','s0003','s0004','s0005']){await open(id);await p.click('[data-loc="unit-1"]');if(id==='s0003'){await p.waitForSelector('#modal:not([hidden])');await p.click('#askBtns .go')}await p.waitForTimeout(80)}
 db=await dbOf(p);ok(['s0001','s0003','s0004','s0005'].map(s=>db[uid+'spools/'+s].slot).join()==='1,2,3,4','each spool takes the next free space');
 await open('s0002');await p.click('[data-loc="unit-1"]');await p.waitForSelector('#modal:not([hidden])');await p.click('#askBtns .go');await p.waitForSelector('#askBtns');await p.waitForFunction(()=>/is full/.test(document.querySelector('#modal').textContent));
 ok((await p.$$('#askBtns .btn')).length===5,'a full unit asks which spool comes out');await p.click('#askBtns .btn:nth-child(3)');await p.waitForTimeout(100);
 db=await dbOf(p);ok(db[uid+'spools/s0002'].loc==='unit-1'&&db[uid+'spools/s0002'].slot===3&&db[uid+'spools/s0004'].loc==='ams1-1'&&db[uid+'spools/s0004'].slot===1,'swap puts each spool in the other space');
 await open('s0002');await p.click('[data-act=spacePick][data-v="1"]');await p.waitForTimeout(100);db=await dbOf(p);ok(db[uid+'spools/s0002'].slot===1&&db[uid+'spools/s0001'].slot===3,'picking a taken space swaps the two');
 await p.evaluate(()=>{location.hash='loc-unit-1'});await p.waitForSelector('[data-go="store-unit-1"]');ok((await p.$$('#main .slot')).length===4,'storage page shows its spaces');
 await p.click('[data-go="store-unit-1"]');await p.waitForSelector('#s_capacity');await p.fill('#s_capacity','2');await p.click('#saveStore');await p.waitForFunction(()=>/needs room/.test(document.querySelector('#toast').textContent));ok(true,'cannot shrink below what it holds');await p.click('[data-act=storeCancel]');await p.waitForSelector('.storerow');
 await p.evaluate(()=>{location.hash='add'});await p.waitForSelector('#f_brand');
 ok(await p.$eval('#f_loc option[value="unit-1"]',o=>o.disabled),'a full storage cannot be picked when adding');
 await p.fill('#f_brand','SUNLU');await p.fill('#f_material','PLA');await p.fill('#f_colour','Blue');await p.selectOption('#f_loc','unit-2');await p.fill('#f_qty','5');await p.click('#saveSpool');await p.waitForSelector('.spoolhead');await p.waitForTimeout(100);
 db=await dbOf(p);ok(Object.values(db).filter(v=>v.loc==='unit-2').length===4&&Object.entries(db).filter(([k,v])=>k.includes('/spools/')&&v.colour==='Blue'&&v.brand==='SUNLU'&&!v.loc).length===1,'only as many as fit go in');
 await p.evaluate(()=>{location.hash='store-unit-2'});await p.waitForSelector('[data-act=storeDel]');await p.click('[data-act=storeDel]');await p.waitForSelector('#modal:not([hidden])');ok((await p.textContent('#modal')).includes('4 spools'),'deleting says what is in it');await p.click('#askBtns .bad');await p.waitForSelector('.storerow');await p.waitForTimeout(100);
 db=await dbOf(p);ok(!db[uid+'locations/unit-2']&&!Object.values(db).some(v=>v.loc==='unit-2'),'deleted storage leaves its spools with no storage');

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
 ok(!!db[uid+'brands/testbrand'].logo,'logo imported');
 // arranging a loose storage and a storage with spaces
 const order=async()=>p.$$eval('#main .list .row .end .mono:first-child',e=>e.map(x=>x.textContent));
 await p.evaluate(()=>{location.hash='loc-rack-bottom'});await p.waitForSelector('[data-act=arrStart]');const before=await order();
 await p.click('[data-act=arrStart]');await p.waitForSelector('.arrange');await p.click('.arr[data-v="'+(before.length-1)+'"]');ok((await p.textContent('#arrHint')).includes(before[before.length-1]),'picking a spool says where to tap next');
 await p.click('.arr[data-v="0"]');await p.waitForTimeout(100);let after=await order();ok(after[0]===before[before.length-1]&&after.length===before.length,'last spool moved to the front: '+before+' -> '+after);
 await p.click('.arr[data-v="0"]');await p.click('.arr.end');await p.waitForTimeout(100);ok((await order()).join()===before.join(),'and back to the end');
 await p.click('[data-act=arrDone]');ok(await p.$('.arrange')===null,'done closes arranging');
 db=await dbOf(p);const rb=Object.values(db).filter(v=>v&&v.loc==='rack-bottom').map(v=>v.pos);ok(rb.every(x=>x>=1)&&new Set(rb).size===rb.length,'positions saved: '+rb);
 await p.evaluate(()=>{location.hash='stock'});await p.waitForSelector('.racks');
 const discs=await p.$$eval('[data-go="loc-rack-bottom"].shelf .disc',e=>e.length);ok(discs===before.length,'stock page preview follows the storage');
 await p.evaluate(()=>{location.hash='loc-ams1-1'});await p.waitForSelector('[data-act=arrStart]');await p.click('[data-act=arrStart]');await p.waitForSelector('.arr');
 db=await dbOf(p);const inA=Object.entries(db).filter(([k,v])=>k.includes('/spools/')&&v.loc==='ams1-1'),first=inA.find(([,v])=>v.slot===1);
 await p.click('.arr[data-v="0"]');await p.screenshot({path:out+'/arrange-phone.png',fullPage:true});await p.click('.arr[data-v="3"]');await p.waitForTimeout(100);db=await dbOf(p);ok(db[first[0]].slot===4,'a spool moves to an empty space when arranging: '+db[first[0]].slot);
 await p.click('[data-act=arrDone]');ok(db[uid+'config/main'].imported.includes(imp.batch),'import remembered');
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

 // printers and what feeds them
 await p.evaluate(()=>{location.hash='setup'});await p.waitForSelector('[data-go="printer-new"]');await p.click('[data-go="printer-new"]');await p.waitForSelector('#p_name');
 await p.fill('#p_name','Betsy');await p.fill('#p_serial','01p00c500000001');await p.click('[data-act=pfAms]');await p.selectOption('#pf_loc_0','ams1-1');await p.click('[data-act=pfAms]');
 ok(await p.inputValue('#pf_unit_1')==='2','second AMS defaults to AMS 2');await p.selectOption('#pf_loc_1','unit-1');await p.screenshot({path:out+'/printer-phone.png',fullPage:true});await p.click('#printerForm [type=submit]');await p.waitForSelector('[data-go="printer-new"]');
 await p.click('[data-go="printer-new"]');await p.waitForSelector('#p_name');await p.fill('#p_name','Alduin');await p.click('[data-act=pfExt]');await p.selectOption('#pf_loc_0','dryer');await p.waitForSelector('#pf_space_0');await p.selectOption('#pf_space_0','1');
 ok(!(await p.$('[data-act=pfExt]')),'only one external spool per printer');await p.fill('#p_serial','01P00C500000001');await p.click('#printerForm [type=submit]');await p.waitForFunction(()=>/already has that serial/.test(document.querySelector('#toast').textContent));ok(true,'two printers cannot share a serial');
 await p.fill('#p_serial','01P00C500000002');await p.click('#printerForm [type=submit]');await p.waitForSelector('[data-go="printer-new"]');await p.waitForTimeout(100);
 db=await dbOf(p);ok(db[uid+'printers/betsy'].serial==='01P00C500000001'&&db[uid+'printers/betsy'].feeds.map(f=>f.kind+f.unit+f.loc).join()==='ams1ams1-1,ams2unit-1'&&db[uid+'printers/alduin'].feeds[0].loc==='dryer'&&db[uid+'printers/alduin'].feeds[0].space===1,'printers saved with their feeds');
 ok((await p.textContent('#main')).includes('AMS 2: Printer A Unit 1'),'Setup lists what feeds each printer');
 const tray=await p.evaluate(()=>{const l=S.locs['ams1-1'],u=S.locs['unit-1'];return {a:SpoolStock.trayToSpool('01P00C500000001',0,0)?.id||null,want:seated(l)[0]?.id||null,b:SpoolStock.trayToSpool('Betsy',1,2)?.id||null,wantb:seated(u)[2]?.id||null,ext:SpoolStock.trayToSpool('Alduin','external')?.id||null,wantext:seated(S.locs.dryer)[0]?.id||null,none:SpoolStock.trayToSpool('Betsy',3,0)}});
 ok(tray.a===tray.want&&tray.b===tray.wantb&&tray.ext===tray.wantext&&tray.none===null,'printer trays map to the spools in those spaces: '+JSON.stringify(tray));
 await p.evaluate(()=>{location.hash='loc-unit-1'});await p.waitForSelector('[data-go="store-unit-1"]');ok((await p.textContent('#main')).includes('Feeds Betsy (AMS 2)'),'storage page says which printer it feeds');
 if(tray.b){await p.evaluate(id=>{location.hash=id},tray.b);await p.waitForSelector('.spoolhead');ok((await p.textContent('#main')).includes('Feeding Betsy, AMS 2 tray 3'),'spool page says which printer tray it feeds');}

 // the Orca plugin: sign-in code, and Undo of a print it took off
 await p.evaluate(()=>{location.hash='setup'});await p.waitForSelector('[data-act=pluginCode]');
 ok(await p.getAttribute('a[href="spool_stock.py"]','download')!==null,'Setup offers the plugin as a download from the site');
 await p.evaluate(()=>{window.__copied='';navigator.clipboard.writeText=async t=>{window.__copied=t}});await p.click('[data-act=pluginCode]');await p.waitForFunction(()=>window.__copied);
 const pc=await p.evaluate(()=>{const c=window.__copied;return {pre:c.slice(0,4),d:JSON.parse(atob(c.slice(4)))}});
 ok(pc.pre==='SS1.'&&pc.d.p==='x'&&pc.d.u==='u1'&&pc.d.t==='RT-a@b.co'&&pc.d.e==='a@b.co','sign-in code carries the project, account and refresh token for the plugin');
 await p.evaluate(()=>{window.__copied='';navigator.clipboard.writeText=async()=>{throw new Error('refused')};const ex=document.execCommand.bind(document);document.execCommand=c=>{if(c==='copy'){window.__copied=document.activeElement.value;return true}return ex(c)}});
 await p.click('[data-act=pluginCode]');await p.waitForFunction(()=>window.__copied);
 ok((await p.evaluate(()=>window.__copied)).startsWith('SS1.')&&await p.evaluate(()=>document.querySelector('#modal').hidden),'sign-in code still copies where the app refuses the newer clipboard call, without showing it');
 const js=await p.evaluate(()=>{const s=liveSpools().find(s=>s.loc==='ams1-1');const at=1762545600000;const g0=s.remainG??s.netG;
  col('spools').doc(s.id).update({remainG:g0-12,prints:[{at,name:'Bracket',g:12},...(s.prints||[])]});
  col('jobs').doc('SER-1').set({at,printer:'Betsy',job:'Bracket',pct:100,items:[{spool:s.id,g:12}],source:'orca'});return {id:s.id,g0}});
 await p.waitForSelector('[data-act=jobUndo]');ok((await p.textContent('#main')).includes('12 g off '+js.id.toUpperCase()),'Setup lists prints the plugin took off');
 await p.click('[data-act=jobUndo]');await p.waitForTimeout(200);db=await dbOf(p);
 ok(db[uid+'spools/'+js.id].remainG===js.g0&&!(db[uid+'spools/'+js.id].prints||[]).some(x=>x.name==='Bracket')&&db[uid+'jobs/SER-1'].undone===true,'Undo puts the grams back and removes the print from the spool');
 ok(!(await p.$('[data-act=jobUndo]')),'an undone print has no Undo button');

 // logging a print from an Orca sliced file
 const F3=__dirname+'/fixtures/';
 await p.evaluate(()=>{location.hash='stock'});await p.waitForSelector('[data-go=log]');await p.click('[data-go=log]');await p.waitForSelector('label[for=pickPrint]');
 await p.setInputFiles('#pickPrint',F3+'unsliced.3mf');await p.waitForFunction(()=>/hasn't been sliced/.test(document.querySelector('#toast').textContent));ok(true,'an unsliced 3MF is explained');
 await p.setInputFiles('#pickPrint',F3+'print.gcode.3mf');await p.waitForSelector('[data-act=logSave]');
 ok((await p.$$('.useRow')).length===2&&(await p.textContent('#main')).includes('12.5 g')&&(await p.textContent('#main')).includes('PETG, 3.2 g'),'sliced 3MF read: two filaments with grams');
 const picks=await p.$$eval('[data-lp]',e=>e.map(x=>x.value));ok(picks[0]&&picks[0]!==picks[1],'a different spool suggested for each filament: '+picks);
 db=await dbOf(p);ok(/PLA/i.test(db[uid+'spools/'+picks[0]].material)&&(!picks[1]||/PETG/i.test(db[uid+'spools/'+picks[1]].material)),'suggestions match the material: '+picks.map(id=>id&&db[uid+'spools/'+id].material));
 db=await dbOf(p);const was=picks.map(id=>id?db[uid+'spools/'+id].remainG??db[uid+'spools/'+id].netG:null);
 await p.screenshot({path:out+'/log-phone.png',fullPage:true});
 await p.fill('#lpPct','50');await p.click('[data-act=logSave]');await p.waitForSelector('.racks');await p.waitForTimeout(150);
 db=await dbOf(p);const now0=db[uid+'spools/'+picks[0]];ok(now0.remainG===Math.max(0,Math.round(was[0]-6.25))&&now0.prints[0].g===6.3&&now0.prints[0].name==='print','half a print takes half the grams off: '+was[0]+' -> '+now0.remainG);
 await p.evaluate(()=>{location.hash='log'});await p.waitForSelector('label[for=pickPrint]');await p.setInputFiles('#pickPrint',F3+'print.gcode.3mf');await p.waitForSelector('[data-act=logSave]');ok((await p.textContent('#main')).includes('already logged'),'warns when a print is logged twice');await p.click('[data-act=logCancel]');await p.waitForSelector('.racks');
await p.evaluate(id=>{location.hash=id},picks[0]);await p.waitForSelector('.spoolhead');ok((await p.textContent('#main')).includes('Prints'),'the spool page lists its prints');
 const gtext=fs.readFileSync(F3+'print.gcode','utf8');
 await p.evaluate(t=>{const dt=new DataTransfer();dt.items.add(new File([t],'part.gcode'));document.body.dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}))},gtext);
 await p.waitForSelector('[data-act=logSave]');ok((await p.textContent('#main')).includes('PLA, 7.8 g'),'dropping plain G-code anywhere opens it');
 const pick=await p.$eval('[data-lp="0"]',e=>e.value);db=await dbOf(p);const w0=db[uid+'spools/'+pick].remainG;await p.click('[data-act=logSave]');await p.waitForSelector('#toast button');await p.click('#toast button');await p.waitForTimeout(150);db=await dbOf(p);ok(db[uid+'spools/'+pick].remainG===w0,'undo puts the weight back');

 // ring size follows a real 1 kg roll
 const radius=await p.evaluate(()=>[1000,750,500,250,100,10,0,1200].map(g=>Math.round(rollR({remainG:g,netG:1000})*10)/10));
 ok(radius[0]===47&&radius[7]===47&&radius[6]===24,'full is a 1 kg roll, empty is the core: '+radius);
 ok((radius[0]-radius[1])/250<(radius[3]-radius[4])/150&&(radius[3]-radius[4])/150<(radius[4]-radius[6])/100,'shrinks slowly at first, then quickly: '+radius);
 ok(radius[2]>(47+24)/2,'half a roll still looks more than half full: '+radius[2]);
 ok(radius[5]>24,'a few grams still shows a sliver');

 // filament catalogue: find a manufacturer, then their own names
 await p.evaluate(()=>{location.hash='add'});await p.waitForSelector('#f_brand');await p.waitForFunction(()=>!!CAT);
 await p.fill('#f_brand','esu');await p.waitForSelector('#brandPick .pick');ok((await p.textContent('#brandPick')).includes('filaments'),'manufacturer search shows how many filaments');
 await p.click('#brandPick .pick');await p.waitForSelector('#linePick .pick');await p.waitForTimeout(50);ok(await p.inputValue('#f_brand')==='eSUN'&&(await p.$$('#linePick .pick')).length>=10,'picking a manufacturer lists its filaments');await p.screenshot({path:out+'/catalogue-phone.png',fullPage:true});
 await p.fill('#f_material','pla high speed');await p.waitForTimeout(50);ok((await p.textContent('#linePick')).includes('ePLA+HS'),'high speed finds their HS naming');
 await p.fill('#f_material','epla+hs');await p.waitForTimeout(50);await p.click('#linePick .pick');await p.waitForTimeout(80);
 ok(await p.inputValue('#f_material')==='ePLA+HS'&&await p.inputValue('#f_nMin')==='210'&&await p.inputValue('#f_bMin')==='45'&&await p.inputValue('#f_tareG')==='170','catalogue fills settings and spool weight');ok(await p.inputValue('#f_dT')==='55'&&(await p.textContent('#srcNote')).includes('catalogue'),'drying comes from the typical table and the source is shown');ok(await p.$eval('#linePick',e=>e.hidden),'list closes after picking');
 await p.click('#catColours .sw');await p.waitForTimeout(50);const pickedColour=await p.inputValue('#f_colour');ok(pickedColour.length>0&&(await p.inputValue('#f_hex'))!=='#111111','their colour list fills name and colour: '+pickedColour);
 await p.click('#saveSpool');await p.waitForSelector('.spoolhead');
 await p.evaluate(()=>{location.hash='add'});await p.waitForSelector('#f_brand');await p.fill('#f_brand','eSUN');await p.fill('#f_material','PETG Speedster');ok((await p.textContent('#linePick')).includes('Not listed'),'an unlisted name says it can still be used');await p.fill('#f_colour','Black');await p.fill('#f_nMin','220');await p.fill('#f_nMax','260');await p.click('#saveSpool');await p.waitForSelector('.spoolhead');
 await p.evaluate(()=>{location.hash='add'});await p.waitForSelector('#f_brand');await p.fill('#f_brand','eSUN');await p.click('#f_material');await p.waitForSelector('#linePick .pick');ok((await p.textContent('#linePick')).includes('PETG Speedster')&&(await p.textContent('#linePick')).includes('Saved by you'),'your own names join the list');await p.fill('#f_material','speedster');await p.waitForTimeout(50);await p.click('#linePick .pick');await p.waitForTimeout(60);ok(await p.inputValue('#f_nMax')==='260','your own settings come back');
 const pickFrom=async(brand,q)=>{await p.fill('#f_brand','');await p.fill('#f_brand',brand);await p.fill('#f_material','');await p.fill('#f_material',q);await p.waitForTimeout(50);await p.click('#linePick .pick');await p.waitForTimeout(60);return Promise.all(['nMin','nMax','bMin','bMax','dT','dH'].map(k=>p.inputValue('#f_'+k))).then(v=>v.join())};
 await p.evaluate(()=>{location.hash='stock'});await p.waitForSelector('.racks');await p.evaluate(()=>{location.hash='add'});await p.waitForSelector('#f_brand');
 let got=await pickFrom('eSUN','petg high speed');ok(await p.inputValue('#f_material')==='PETG+HS'&&got==='220,260,75,90,60,8','eSUN PETG+HS found as PETG High Speed: '+got);
 got=await pickFrom('eSUN','epetg+hs');ok(await p.inputValue('#f_material')==='ePETG+HS'&&got==='220,260,75,90,70,8','eSUN ePETG+HS is its own filament, typical drying: '+got);
 ok(await p.evaluate(()=>CAT.esun.lines.filter(l=>/PETG\+HS/i.test(l.n)).map(l=>l.n).sort().join())==='PETG+HS,ePETG+HS','PETG+HS and ePETG+HS listed separately');
 got=await pickFrom('eSUN','petg+hs');ok(await p.inputValue('#f_material')==='PETG+HS'&&got==='220,260,75,90,60,8','an exact name comes first: PETG+HS');
 got=await pickFrom('Sunlu','high speed matte petg');ok(got==='240,260,70,80,65,8','Sunlu High Speed Matte PETG: '+got);
 got=await pickFrom('Sunlu','rapid petg');ok(await p.inputValue('#f_material')==='PETG High Speed'&&got==='230,270,70,80,65,8','Sunlu PETG High Speed, also sold as Rapid PETG: '+got);
 await p.fill('#f_brand','');await p.fill('#f_brand','ender');await p.waitForSelector('#brandPick .pick');ok((await p.textContent('#brandPick')).includes('also sold as Ender'),'searching Ender finds Creality');await p.click('#brandPick .pick');await p.waitForTimeout(50);ok(await p.inputValue('#f_brand')==='Creality','Ender picks Creality');
 got=await pickFrom('Creality','fast pla');ok(await p.inputValue('#f_material')==='Ender Fast PLA'&&got==='190,230,45,60,55,8','Creality Ender Fast PLA: '+got);
 got=await pickFrom('Landu','rapid petg');ok(await p.inputValue('#f_material')==='Rapid PETG'&&got==='240,270,60,80,70,8','Landu Rapid PETG listed, typical settings until checked: '+got);
 got=await pickFrom('Bambu Lab','pla basic');ok(await p.inputValue('#f_material')==='PLA Basic'&&got==='190,230,35,45,55,8','Bambu PLA Basic: '+got);
 got=await pickFrom('Bambu Lab','pla matte');ok(got==='190,230,35,45,50,8','Bambu PLA Matte uses manufacturer ranges: '+got);
 got=await pickFrom('eSUN','petg basic');ok(got.startsWith('250,265,65,80'),'a single value never replaces a range: '+got);
 got=await pickFrom('Bambu Lab','pva');ok(got==='220,250,35,45,55,8','unchecked drying falls back to typical: '+got);
 got=await pickFrom('Landu','pla plus');ok(await p.inputValue('#f_material')==='PLA+'&&got==='210,230,45,60,55,8','Landu PLA+: '+got);
 got=await pickFrom('Deeplee','rapid pla plus');ok(await p.inputValue('#f_material')==='Rapid PLA+'&&got==='190,240,45,65,55,8','Deeplee Rapid PLA+ listed with typical settings: '+got);
 got=await pickFrom('CC3D','pla silk');ok(got==='195,225,30,60,55,8','CC3D PLA Silk uses the label nozzle range: '+got);await p.fill('#f_colour','Teal Blue');await p.waitForTimeout(60);ok(await p.inputValue('#f_hex')==='#00688d','CC3D Teal Blue colour comes from the open database');
 await p.fill('#f_brand','');await p.fill('#f_brand','amolen');await p.waitForSelector('#brandPick .pick');ok(/amolen.*filaments/i.test(await p.textContent('#brandPick')),'brands from the open database are searchable');
 got=await pickFrom('Sunlu','tpu-95a');ok(got==='190,230,50,60,50,','Sunlu TPU 95A, no drying time given: '+got);ok(!/null|undefined/.test(await p.textContent('#preview')),'sticker copes with no drying time');
 await p.fill('#f_brand','eSUN');await p.fill('#f_material','PETG');await p.waitForTimeout(60);ok(await p.inputValue('#f_dT')==='60','eSUN PETG dries at 60');
 await p.evaluate(()=>{location.hash='stock'});await p.waitForSelector('.racks');

 // Orca presets for the filaments you own
 await p.evaluate(()=>{location.hash='setup'});await p.waitForSelector('[data-act=orcaZip]');ok(!(await p.$('[data-act=orcaSend]')),'Send to Orca only shows in the Windows app');
 const [oz]=await Promise.all([p.waitForEvent('download'),p.click('[data-act=orcaZip]')]);await oz.saveAs(out+'/orca.zip');
 const presets=JSON.parse(require('child_process').execSync(`python3 -I -c "import zipfile,json,sys;z=zipfile.ZipFile(sys.argv[1]);print(json.dumps({n:json.loads(z.read(n)) for n in z.namelist()}))" ${out}/orca.zip`).toString());
 const cat=JSON.parse(fs.readFileSync(__dirname+'/../site/catalogue.json')),known=new Set([...cat.orca.generic,...Object.values(cat.brands).flat().map(l=>l.o).filter(Boolean)]);
 ok(cat.brands['Bambu Lab'].find(l=>l.n==='Support for PLA/PETG').o==='Bambu Support For PLA/PETG @System'&&(cat.orca.variants['Bambu Support For PLA/PETG']||[]).includes('Bambu Support For PLA/PETG @BBL X1C'),'Orca names come from inside its files, not the file names (PLA/PETG, not PLA-PETG)');
 const pv=Object.entries(presets);ok(pv.length>=3&&pv.every(([n,j])=>j.type==='filament'&&j.from==='User'&&j.name+'.json'===n&&j.filament_settings_id[0]===j.name&&/^\d+\.\d+\.\d+\.\d+$/.test(j.version)),'zip holds valid Orca user presets: '+pv.length);
 ok(pv.every(([,j])=>known.has(j.inherits)&&j.inherits.startsWith('Generic ')),'new presets build on generic profiles Orca has: '+[...new Set(pv.map(([,j])=>j.inherits))].join('; '));
 ok(!pv.some(([n])=>/^Bambu Lab ASA \(/.test(n)),'no preset for a filament Orca already has (Bambu ASA)');
 ok((await p.textContent('#main')).includes("Orca's own profile, used as it is"),'Setup says which use Orca\'s own profile');
 const gen=pv.find(([,j])=>j.nozzle_temperature);ok(gen&&+gen[1].nozzle_temperature[0]>=+gen[1].nozzle_temperature_range_low[0]&&+gen[1].nozzle_temperature[0]<=+gen[1].nozzle_temperature_range_high[0]&&gen[1].textured_plate_temp,'new presets carry the temperatures');
 await p.evaluate(()=>{window.__sent=null;window.spoolStockDesktop={orca:{version:3,write:async(f,o)=>{window.__sent={f,o};return {written:f.length,shown:3,missing:[]}},restore:async()=>({restored:true})}};location.hash='stock'});await p.waitForSelector('.racks');
 await p.evaluate(()=>{location.hash='setup'});await p.waitForSelector('[data-act=orcaSend]');ok(await p.isChecked('#orcaOnly'),'only list my filaments is on by default');await p.click('[data-act=orcaSend]');await p.waitForFunction(()=>/set to your filaments/.test(document.querySelector('#toast').textContent));
 const sent=await p.evaluate(()=>window.__sent);ok(sent.f.length===pv.length&&sent.o.show.includes('Bambu ASA @System')&&sent.o.show.some(n=>/^Bambu ASA @BBL /.test(n)),'Send to Orca hands over exact Orca names, Bambu printer versions included: '+sent.o.show.length);
 await p.evaluate(()=>{window.spoolStockDesktop.orca.version=undefined;window.__sent=null});await p.click('[data-act=orcaSend]');await p.waitForFunction(()=>/update the Windows app/.test(document.querySelector('#toast').textContent));ok((await p.evaluate(()=>window.__sent.o.show))===undefined,'an older Windows app only gets the presets, so it can\'t empty Orca\'s list');
 await p.click('[data-act=orcaRestore]');await p.waitForFunction(()=>/back to how it was/.test(document.querySelector('#toast').textContent));ok(true,'restore is offered');
 await p.evaluate(()=>{delete window.spoolStockDesktop});
 // the list the Orca plugin builds its Spool Stock group from
 const orcaDoc=async(not)=>{for(let i=0;i<50;i++){const d=(await dbOf(p))[uid+'config/orca'];if(d&&d.sig!==not)return d;await p.waitForTimeout(100)}return (await dbOf(p))[uid+'config/orca']};
 const feed=await orcaDoc();
 ok(feed&&feed.v===1&&feed.sig&&feed.items.filter(x=>!x.own).length===pv.length&&feed.items.some(x=>x.own),'the app keeps a list of your filaments for the Orca plugin: '+(feed&&feed.items.length));
 const fasa=feed&&feed.items.find(x=>x.own&&/ASA/.test(x.label));
 ok(fasa&&fasa.parents[0]==='Bambu ASA @System'&&fasa.parents.some(n=>/@BBL /.test(n))&&JSON.parse(fasa.text).inherits==='Bambu ASA @System','a filament Orca has builds on its own profile, with its printer versions as fallbacks');
 ok(feed&&feed.items.filter(x=>!x.own).every(x=>{const j=JSON.parse(x.text);return j.name===x.name&&known.has(x.parents[0])&&x.parents.includes('Generic PLA @System')&&JSON.stringify(presets[x.name+'.json'])===JSON.stringify(j)}),'the rest match the downloadable presets, with Generic PLA as the last fallback');
 await p.evaluate(()=>{col('spools').doc('zz01').set({brand:'Polymaker',material:'PolyTerra PLA',colour:'Black',hex:'#111111',netG:1000,remainG:1000,status:'active',loc:null})});
 const feed2=await orcaDoc(feed.sig);
 ok(feed2.sig!==feed.sig&&feed2.items.some(x=>/PolyTerra/.test(x.name)),'the list updates when you add a new filament');
 await p.evaluate(()=>{col('spools').doc('zz01').delete()});
 ok(!(await orcaDoc(feed2.sig)).items.some(x=>/PolyTerra/.test(x.name)),'and when you no longer have one');
 // every spool is a 1 kg spool
 await p.evaluate(()=>{col('spools').doc('kg01').set({brand:'Sunlu',material:'PLA',colour:'Red',hex:'#ff0000',netG:550,remainG:550,status:'active',loc:null});col('spools').doc('kg02').set({brand:'Sunlu',material:'PLA',colour:'Blue',hex:'#0000ff',netG:250,status:'active',loc:null});col('spools').doc('kg03').set({brand:'Sunlu',material:'PLA',colour:'Green',hex:'#00ff00',netG:1000,remainG:1200,status:'active',loc:null})});
 let kg;for(let i=0;i<30;i++){kg=await dbOf(p);if(kg[uid+'spools/kg01'].netG===1000&&kg[uid+'spools/kg02'].netG===1000&&kg[uid+'spools/kg03'].remainG===1000)break;await p.waitForTimeout(100)}
 ok(['kg01','kg02','kg03'].map(k=>kg[uid+'spools/'+k].netG+'/'+kg[uid+'spools/'+k].remainG).join()==='1000/550,1000/250,1000/1000','spools saved with another full weight become 1 kg, keeping what was on them: '+['kg01','kg02','kg03'].map(k=>kg[uid+'spools/'+k].remainG+' of '+kg[uid+'spools/'+k].netG).join(', '));
 ok(Object.entries(kg).filter(([k])=>k.startsWith(uid+'spools/')).every(([,v])=>v.netG===1000),'every spool in the stock is out of 1000 g');
 await p.evaluate(()=>{location.hash='add'});await p.waitForSelector('#f_left');
 ok(await p.inputValue('#f_left')==='1000'&&!(await p.$('#f_netG'))&&/of 1000/.test(await p.textContent('label:has(#f_left)')),'adding a spool asks how much is left, out of 1000 g');
 await p.fill('#f_brand','Sunlu');await p.fill('#f_material','PLA');await p.fill('#f_left','400');await p.click('#saveSpool');await p.waitForTimeout(300);
 kg=await dbOf(p);const added=Object.entries(kg).filter(([k,v])=>k.startsWith(uid+'spools/')&&v.remainG===400&&v.brand==='Sunlu').map(([,v])=>v);
 ok(added.length===1&&added[0].netG===1000,'a part used spool is saved as 400 g left of 1000 g');
 await p.evaluate(()=>{for(const k of ['kg01','kg02','kg03'])col('spools').doc(k).delete()});

 // stock list for Spool Reader
 await p.evaluate(()=>{location.hash='setup'});await p.waitForSelector('[data-act=listSave]');
 await p.screenshot({path:out+'/setup-phone.png',fullPage:true});
 const [dl2]=await Promise.all([p.waitForEvent('download'),p.click('[data-act=listSave]')]);await dl2.saveAs(out+'/spool-stock-list.json');
 const list=JSON.parse(fs.readFileSync(out+'/spool-stock-list.json'));ok(list.format==='spool-stock-list'&&list.spools.length>=8&&list.spools[0].storage!==undefined,'stock list downloads');

 // removing everything to start again
 await p.evaluate(()=>{location.hash='setup'});await p.waitForSelector('[data-act=storesClear]');await p.click('[data-act=storesClear]');await p.waitForSelector('#modal:not([hidden])');await p.click('#askBtns .bad');await p.waitForFunction(()=>!document.querySelector('[data-act=storeShift]'));await p.waitForTimeout(150);
 db=await dbOf(p);ok(stores(db).length===0&&!Object.values(db).some(v=>v.loc),'remove all clears storages and keeps the spools');

 // sign out and the Android app's limits
 await p.evaluate(()=>{location.hash='setup'});await p.waitForSelector('[data-act=signOut]');await p.click('[data-act=signOut]');await p.waitForSelector('#authForm');ok(true,'sign out');
 ok((await p.evaluate(()=>window.__fbErrors)).length===0,'nothing Firestore would refuse: '+JSON.stringify(await p.evaluate(()=>window.__fbErrors)));
 await ctx.close();

 const a=await b.newContext({viewport:{width:400,height:820},serviceWorkers:'block',userAgent:'Mozilla/5.0 (Linux; Android 14) Chrome/120 Mobile SpoolStockApp'});await stubs(a);
 const ap=await a.newPage();await ap.goto(U);await ap.evaluate(()=>{localStorage.setItem('__fbUsers','{"a@b.co":"secret1"}');localStorage.setItem('__fbSession','a@b.co')});await ap.goto(U+'#stickers');await ap.reload();await ap.waitForSelector('.sheetmap');
 ok((await ap.textContent('#main')).includes("can't save PDFs"),'Android app sends printing to the PC');await a.close();

 // an older account: one record per slot folds into one storage
 const m=await b.newContext({viewport:{width:400,height:820},serviceWorkers:'block'});await stubs(m);const mp=await m.newPage();await mp.goto(U);
 const old={};const sl=(id,name,group,kind,capacity,order,x={})=>old[uid+'locations/'+id]={name,group,kind,capacity,order,wantLabel:false,...x};
 sl('u-1','Slot 1','Unit A','ams',1,1);sl('u-2','Slot 2','Unit A','ams',1,2);sl('u-3','Slot 3','Unit A','ams',1,3);sl('u-4','Slot 4','Unit A','ams',1,4);sl('d-1','Slot 1','Dryer B','dryer',1,5,{maxC:70});sl('d-2','Slot 2','Dryer B','dryer',1,6,{maxC:70});sl('top','Top shelf','Rack','shelf',10,7);sl('bot','Bottom shelf','Rack','shelf',10,8);
 old[uid+'spools/s0001']={status:'active',brand:'X',material:'PLA',colour:'Red',hex:'#ff0000',netG:1000,remainG:1000,loc:'u-3',history:[{at:1,from:'d-1',to:'u-3'}]};old[uid+'config/main']={seeded:true};
 await mp.evaluate(d=>{localStorage.setItem('__fb',JSON.stringify(d));localStorage.setItem('__fbUsers','{"a@b.co":"secret1"}');localStorage.setItem('__fbSession','a@b.co')},old);await mp.reload();await mp.waitForSelector('.racks');await mp.waitForTimeout(300);
 const md=await dbOf(mp),ml=Object.keys(md).filter(k=>k.includes('/locations/')).map(k=>k.split('/').pop()).sort();
 ok(ml.join()==='bot,dryer-b,top,unit-a','slots fold into one storage: '+ml);
 ok(md[uid+'locations/unit-a'].capacity===4&&md[uid+'locations/unit-a'].spaces===true&&md[uid+'locations/dryer-b'].maxC===70,'spaces and dryer limit carried over');
 ok(md[uid+'spools/s0001'].loc==='unit-a'&&md[uid+'spools/s0001'].slot===3&&md[uid+'spools/s0001'].history[0].from==='dryer-b','spool keeps its space and history');
 ok(md[uid+'config/main'].layout===2,'folding only runs once');ok(await mp.$$eval('.slot',e=>e.length)===6,'folded storages drawn with spaces');await m.close();

 // offline start with the real service worker
 const c=await b.newContext({viewport:{width:400,height:820}});await c.route(/config\.js/,r=>r.fulfill({contentType:'text/javascript',body:'window.SPOOL_CONFIG={};'}));
 const w=await c.newPage();await w.goto(U);await w.waitForFunction(()=>navigator.serviceWorker.ready.then(()=>true));await w.waitForTimeout(500);await w.reload();await w.waitForTimeout(400);
 await c.setOffline(true);await w.reload();await w.waitForSelector('.nav');ok((await w.textContent('.brand')).includes('Spool Stock'),'opens with no connection');await c.close();

 console.log('page errors:',errs);ok(errs.length===0,'no page errors');
 await b.close();srv.close();
})().catch(e=>{console.error(e);process.exit(1)});
