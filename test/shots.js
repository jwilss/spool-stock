// Makes the README screenshots from made-up demo stock. Run: node test/shots.js
const {chromium}=require('playwright');const fs=require('fs');const path=require('path');const http=require('http');const {execSync}=require('child_process');
const site=path.resolve(__dirname,'../site'),docs=path.resolve(__dirname,'../docs'),out=__dirname+'/out';
const F='/tmp/claude-0/fonts/';const w=(d,f)=>{const g=execSync(`find ${F}${d} -name '${f}'`).toString().trim().split('\n')[0];return fs.readFileSync(g).toString('base64')};
const face=(fam,wt,b)=>`@font-face{font-family:"${fam}";font-weight:${wt};src:url(data:font/woff2;base64,${b}) format("woff2")}`;
const css=[face('Barlow',400,w('x-fontsource-barlow-5.3.0.tgz','barlow-latin-400-normal.woff2')),face('Barlow',500,w('x-fontsource-barlow-5.3.0.tgz','barlow-latin-500-normal.woff2')),face('Barlow',600,w('x-fontsource-barlow-5.3.0.tgz','barlow-latin-600-normal.woff2')),face('Barlow Condensed',600,w('x-fontsource-barlow-condensed-5.3.0.tgz','barlow-condensed-latin-600-normal.woff2')),face('Barlow Condensed',700,w('x-fontsource-barlow-condensed-5.3.0.tgz','barlow-condensed-latin-700-normal.woff2')),face('IBM Plex Mono',500,w('x-fontsource-ibm-plex-mono-5.3.0.tgz','ibm-plex-mono-latin-500-normal.woff2')),face('IBM Plex Mono',600,w('x-fontsource-ibm-plex-mono-5.3.0.tgz','ibm-plex-mono-latin-600-normal.woff2'))].join('');
const types={'.html':'text/html','.js':'text/javascript','.png':'image/png','.webmanifest':'application/manifest+json'};
const srv=http.createServer((q,r)=>{let p=decodeURIComponent(q.url.split('?')[0]);if(p.endsWith('/'))p+='index.html';const f=path.join(site,p);if(!fs.existsSync(f)){r.statusCode=404;return r.end()}r.setHeader('content-type',types[path.extname(f)]||'text/plain');r.end(fs.readFileSync(f))}).listen(8768);
const U='http://localhost:8768/',uid='users/u1/';
const cfg='window.SPOOL_CONFIG={siteUrl:"https://jwilss.github.io/spool-stock/",repo:"jwilss/spool-stock",firebase:{projectId:"x"}};';
const now=Date.now(),day=864e5;
const sp=(o)=>({status:'active',labelled:false,addedAt:now,history:[],dia:1.75,tareG:220,finish:'',netG:1000,remainG:1000,nMin:240,nMax:270,bMin:60,bMax:80,dT:70,dH:8,pattern:'solid',...o,colours:o.colours||[o.hex]});
const data={};const L=(id,name,group,kind,capacity,order)=>{data[uid+'locations/'+id]={name,group,kind,capacity,order,wantLabel:false}};
L('pi-1','Slot 1','Space Pi Plus (Alduin)','dryer',1,1);L('pi-2','Slot 2','Space Pi Plus (Alduin)','dryer',1,2);let o=10;for(const a of[1,2])for(const n of[1,2,3,4])L(`ams${a}-${n}`,`Slot ${n}`,`AMS ${a} (Betsy)`,'ams',1,++o);L('rack-top','Top shelf','Rack','shelf',10,21);L('rack-bottom','Bottom shelf','Rack','shelf',10,22);
const S=[['s0001',{brand:'Northbank',material:'PETG HF',colour:'Graphite',hex:'#2B2F33',remainG:740,loc:'ams1-1'}],['s0002',{brand:'Northbank',material:'PETG HF',colour:'Signal Red',hex:'#C8102E',remainG:410,loc:'ams1-2'}],['s0003',{brand:'Harbour Filament',material:'PLA Matte',colour:'Sea Green',hex:'#1E8A6E',remainG:920,loc:'ams1-3',nMin:190,nMax:240,bMin:45,bMax:65,dT:55}],['s0004',{brand:'Harbour Filament',material:'PLA Silk',colour:'Sunset',hex:'#F2542D',colours:['#F2542D','#B5179E','#3A0CA3'],pattern:'gradient',remainG:680,loc:'ams1-4',nMin:190,nMax:240,bMin:45,bMax:65,dT:55}],['s0005',{brand:'Northbank',material:'PETG HF',colour:'Arctic White',hex:'#F1F3F4',remainG:120,loc:'ams2-1'}],['s0006',{brand:'Lagan 3D',material:'ASA',colour:'Cobalt',hex:'#1F4FBF',remainG:860,loc:'ams2-2',nMin:240,nMax:280,bMin:90,bMax:100,dT:80}],['s0007',{brand:'Lagan 3D',material:'TPU 95A',colour:'Black',hex:'#111111',remainG:530,loc:'pi-1',nMin:200,nMax:250,bMin:35,bMax:45}],['s0008',{brand:'Harbour Filament',material:'PLA Tri Colour',colour:'Festival',hex:'#FFC800',colours:['#FFC800','#E4002B','#0057B8'],pattern:'split',remainG:1000,loc:'rack-top',nMin:190,nMax:240,bMin:45,bMax:65,dT:55}],['s0009',{brand:'Northbank',material:'PETG CF',colour:'Carbon',hex:'#3A3F44',remainG:1000,loc:'rack-top'}],['s0010',{brand:'Lagan 3D',material:'PLA Wood',colour:'Oak',hex:'#B58B5A',remainG:640,loc:'rack-bottom',nMin:190,nMax:240,bMin:45,bMax:65,dT:55}]];
for(const[id,o]of S)data[uid+'spools/'+id]=sp(o);
for(const n of['Northbank','Harbour Filament','Lagan 3D'])data[uid+'brands/'+n.toLowerCase().replace(/[^a-z0-9]+/g,'-')]={name:n};
data[uid+'config/main']={seeded:true,lastId:10,sheet:{id:'l7160',start:0}};
(async()=>{
 const feedArg=fs.existsSync(out+'/demo-feed.mjpeg')?['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream','--use-file-for-fake-video-capture='+out+'/demo-feed.mjpeg']:[];
 const b=await chromium.launch({args:feedArg});
 const mk=async(scheme)=>{const c=await b.newContext({viewport:{width:400,height:820},deviceScaleFactor:2,serviceWorkers:'block',colorScheme:scheme,acceptDownloads:true,permissions:feedArg.length?['camera']:[]});
  await c.addInitScript({path:__dirname+'/mock-firebase.js'});
  await c.route(/vendor\/firebase-/,r=>r.fulfill({contentType:'text/javascript',body:''}));await c.route(/config\.js/,r=>r.fulfill({contentType:'text/javascript',body:cfg}));
  await c.route(/fonts\.googleapis/,r=>r.fulfill({contentType:'text/css',body:css}));await c.route(/fonts\.gstatic/,r=>r.abort());
  const p=await c.newPage();await p.goto(U);await p.evaluate(d=>{localStorage.setItem('__fb',JSON.stringify(d));localStorage.setItem('__fbUsers','{"a@b.co":"secret1"}');localStorage.setItem('__fbSession','a@b.co')},data);await p.reload();await p.waitForSelector('.racks');await p.waitForTimeout(400);return{c,p}};
 const {c,p}=await mk('light');
 await p.screenshot({path:docs+'/stock.png'});
 await p.evaluate(()=>{location.hash='s0004'});await p.waitForSelector('.spoolhead');await p.waitForTimeout(200);await p.screenshot({path:docs+'/spool.png'});
 await p.evaluate(()=>{location.hash='stickers'});await p.waitForSelector('.sheetmap');await p.waitForTimeout(300);
 const [d]=await Promise.all([p.waitForEvent('download'),p.click('[data-act=pdf]')]);await d.saveAs(out+'/demo.pdf');
 if(feedArg.length){await p.evaluate(()=>{location.hash='scan'});await p.waitForSelector('#camStart');await p.click('#camStart');await p.waitForFunction(()=>/Now scan a storage/.test(document.querySelector('#scanState').textContent),{timeout:25000});await p.evaluate(()=>{document.querySelector('#toast').hidden=true});await p.screenshot({path:docs+'/scan.png'});}
 await c.close();
 const {c:c2,p:p2}=await mk('dark');await p2.screenshot({path:docs+'/stock-dark.png'});await c2.close();
 await b.close();srv.close();
})().catch(e=>{console.error(e);process.exit(1)});
