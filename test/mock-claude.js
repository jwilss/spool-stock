// Stand-in for the Claude runtime, used only for local testing
(()=>{
  const KEY='__mockdb';
  const load=()=>{try{return JSON.parse(localStorage.getItem(KEY))||{}}catch{return{}}};
  let store=load();
  const save=()=>localStorage.setItem(KEY,JSON.stringify(store));
  const subs=[];
  const snapDoc=(id,body)=>({id,exists:body!==undefined,data:()=>body,metadata:{fromCache:false,hasPendingWrites:false}});
  const fire=()=>subs.forEach(f=>f());
  const docRef=(path)=>({id:path.split('/').pop(),path,
    get:async()=>snapDoc(path.split('/').pop(),store[path]),
    set:async d=>{JSON.stringify(d);store[path]=JSON.parse(JSON.stringify(d));save();window.__writes.push(['set',path]);setTimeout(fire)},
    update:async d=>{if(!store[path])throw{code:'invalid_argument'};Object.assign(store[path],JSON.parse(JSON.stringify(d)));save();window.__writes.push(['update',path]);setTimeout(fire)},
    delete:async()=>{delete store[path];save();window.__writes.push(['delete',path]);setTimeout(fire)},
    onSnapshot(next){const f=()=>next(snapDoc(path.split('/').pop(),store[path]));subs.push(f);setTimeout(f);return()=>{}}
  });
  const collRef=name=>({path:name,doc:id=>docRef(name+'/'+id),get:async()=>{const docs=Object.keys(store).filter(k=>k.startsWith(name+'/')).sort().map(k=>snapDoc(k.split('/').pop(),store[k]));return{docs,size:docs.length,empty:!docs.length}},
    onSnapshot(next){const f=()=>{const docs=Object.keys(store).filter(k=>k.startsWith(name+'/')).sort().map(k=>snapDoc(k.split('/').pop(),store[k]));next({docs,size:docs.length,empty:!docs.length,metadata:{fromCache:false,hasPendingWrites:false}})};subs.push(f);setTimeout(f);return()=>{}}
  });
  window.__writes=[];window.__samples=[];window.__saved=[];
  const sample=Object.assign(async()=>({text:'x'}),{
    limits:async()=>({maxPromptBytes:262144,images:{maxCount:10,maxInputBytes:2e7,mediaTypes:['image/jpeg','image/png']}}),
    json:async(prompt,opts)=>{window.__samples.push({prompt,images:[].concat(opts.images||[]).length});if(window.__sampleErr)throw window.__sampleErr;return (window.__queue&&window.__queue.length)?window.__queue.shift():window.__nextSample}
  });
  const downloads={save:async({filename,data})=>{const b=data instanceof Blob?new Uint8Array(await data.arrayBuffer()):data;let bin;if(typeof b==='string')bin=b;else{bin='';for(const x of b)bin+=String.fromCharCode(x);bin=btoa(bin)}window.__saved.push({filename,bin});return{status:'saved'}}};
  const db={doc:docRef,collection:collRef};
  window.claude={use:async n=>({db,sample,downloads})[n]??null};
})();
