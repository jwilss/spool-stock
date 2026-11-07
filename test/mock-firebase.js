// Stand-in for Firebase used only by the local tests. Like the real thing, a write
// shows up locally at once, and its promise stays open while there is no signal.
(()=>{
  const KEY='__fb'; const load=()=>{try{return JSON.parse(localStorage.getItem(KEY))||{}}catch{return{}}};
  const store=load(); const save=()=>localStorage.setItem(KEY,JSON.stringify(store));
  const subs=new Set(); const fire=()=>setTimeout(()=>subs.forEach(f=>f()));
  window.__offline=false; window.__fbErrors=[];
  const settle=()=>window.__offline?new Promise(()=>{}):Promise.resolve();
  const check=(v,inArr)=>{ if(v===undefined) throw new Error('undefined value'); if(Array.isArray(v)){ if(inArr) throw new Error('nested array'); v.forEach(x=>check(x,true)); } else if(v&&typeof v==='object') Object.values(v).forEach(x=>{ if(x!==undefined) check(x,false); }); };
  const meta={fromCache:false,hasPendingWrites:false};
  const dsnap=p=>({id:p.split('/').pop(),exists:store[p]!==undefined,data:()=>store[p],metadata:meta});
  const docRef=p=>({id:p.split('/').pop(),path:p,
    get:async()=>dsnap(p),
    set(d){ try{check(d)}catch(e){window.__fbErrors.push(e.message+' '+p);return Promise.reject(e)} store[p]=JSON.parse(JSON.stringify(d));save();fire();return settle() },
    update(d){ if(!store[p])return Promise.reject({code:'not-found'}); Object.assign(store[p],JSON.parse(JSON.stringify(d)));save();fire();return settle() },
    delete(){ delete store[p];save();fire();return settle() },
    onSnapshot(o,next){ const f=()=>next(dsnap(p)); subs.add(f); setTimeout(f); return ()=>subs.delete(f) }});
  const colRef=c=>({path:c,doc:id=>docRef(c+'/'+id),
    onSnapshot(o,next){ const f=()=>{ const ds=Object.keys(store).filter(k=>k.startsWith(c+'/')&&k.split('/').length===c.split('/').length+1).sort().map(dsnap); next({docs:ds,size:ds.length,empty:!ds.length,metadata:meta}) }; subs.add(f); setTimeout(f); return ()=>subs.delete(f) }});
  const fsApi={settings(){},enablePersistence:async()=>{},collection:colRef,doc:docRef};
  const users=()=>JSON.parse(localStorage.getItem('__fbUsers')||'{}');
  let cbs=[]; const sess=()=>localStorage.getItem('__fbSession');
  const authApi={get currentUser(){const s=sess();return s?{uid:'u1',email:s,refreshToken:'RT-'+s}:null},
    onAuthStateChanged(cb){cbs.push(cb);setTimeout(()=>cb(authApi.currentUser))},
    async createUserWithEmailAndPassword(e,p){ if(p.length<6) throw{code:'auth/weak-password'}; const u=users(); if(u[e]) throw{code:'auth/email-already-in-use'}; u[e]=p; localStorage.setItem('__fbUsers',JSON.stringify(u)); localStorage.setItem('__fbSession',e); cbs.forEach(c=>c(authApi.currentUser)) },
    async signInWithEmailAndPassword(e,p){ if(users()[e]!==p) throw{code:'auth/invalid-credential'}; localStorage.setItem('__fbSession',e); cbs.forEach(c=>c(authApi.currentUser)) },
    async signOut(){ localStorage.removeItem('__fbSession'); cbs.forEach(c=>c(null)) },
    async sendPasswordResetEmail(){}};
  window.firebase={initializeApp(){},firestore:()=>fsApi,auth:()=>authApi};
})();
