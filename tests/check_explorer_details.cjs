/* Numerical and view-integration checks; no browser automation. */
const fs=require('fs'),path=require('path'),zlib=require('zlib'),assert=require('assert'),vm=require('vm');
const root=path.resolve(__dirname,'../docs/dashboard'),dir=path.join(root,'data');
const read=f=>JSON.parse(fs.readFileSync(path.join(dir,f),'utf8'));
const unzip=f=>JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(dir,f))));
const math=require(path.join(root,'explorer-math.js')),D=read('planning.json'),M=read('profile_manifest.json'),B=unzip('buildings.json.gz');
function close(a,b,t=0.005){assert(Math.abs(a-b)<=t,`${a} != ${b} (tol ${t})`);}
assert.equal(Object.keys(M.cells).length,271);assert.equal(M.shape.length,8760);close(M.shape.reduce((a,b)=>a+b),1,1e-12);
assert.equal(B.rows.length,B.geometry.features.length);assert(B.rows.length>50000);
assert.equal(new Set(B.rows.map(r=>r.id)).size,B.rows.length);
close(B.rows.reduce((a,r)=>a+(r.pv_kWh||0),0)/1e6,213.249,0.001);
for(const r of B.rows){assert.deepEqual(Object.keys(r).sort(),['archetype','archetype_pct','archetypes','dwellings','id','irradiation','name','pv_kWh','pv_kWp','suitable_m2'].sort());assert.equal(Object.values(r.archetypes).reduce((a,b)=>a+b,0),r.dwellings);}
for(const f of B.geometry.features)assert.deepEqual(Object.keys(f.properties),['id']);
const total=unzip(M.all.file),sumImports=Array(6).fill(0),sumExports=Array(6).fill(0);
const targets=new Map(D.cells.map(r=>[r.zone_id+'_'+r.scenario,r]));
let first;
for(const [id,meta] of Object.entries(M.cells)){
 const p=unzip(meta.file);if(!first)first=p;
 for(const a of Object.values(p)){assert.equal(a.length,8760);assert(a.every(v=>Number.isFinite(v)&&v>=0));}
 for(let n=0;n<6;n++){
  const c='U'+n,imports=math.series(p,meta,M.shape,c,'imports'),exports=math.series(p,meta,M.shape,c,'exports'),tar=targets.get(id+'_'+c);
  close(imports.reduce((a,b)=>a+b,0)/1000,tar.cell_import_MWh,0.006);
  close(exports.reduce((a,b)=>a+b,0)/1000,tar.cell_export_MWh,0.006);
  close(Math.max(...imports),tar.cell_import_peak_kW,0.001);
  sumImports[n]+=imports.reduce((a,b)=>a+b,0);sumExports[n]+=exports.reduce((a,b)=>a+b,0);
 }
}
for(let n=0;n<6;n++){close(sumImports[n],total['i'+n].reduce((a,b)=>a+b,0),3);close(sumExports[n],total['x'+n].reduce((a,b)=>a+b,0),3);}
for(const kind of ['electricity','heat','imports','exports','pv','potential']){
 const a=math.series(first,Object.values(M.cells)[0],M.shape,'U5',kind),weeks=math.weeks(a);
 assert.equal(weeks.length,53);close(weeks.reduce((a,b)=>a+b,0),a.reduce((a,b)=>a+b,0)/1000,1e-8);
 close(weeks[52],a.slice(8736).reduce((a,b)=>a+b,0)/1000,1e-8);
}
assert.deepEqual(math.series(first,Object.values(M.cells)[0],M.shape,'U5','heat'),first.h3);
assert.deepEqual(math.series(first,Object.values(M.cells)[0],M.shape,'U4','electricity'),first.e1);
console.log('PASS: 271 cell profiles × 6 scenarios match annual imports, exports and peaks; weekly totals conserve energy; building data reconciles');

class Element{constructor(){this.innerHTML='';this.classList={add(){},remove(){},toggle(){}};this.dataset={};}appendChild(){} remove(){} setAttribute(){}}
const els=new Map(),charts=[];const ctx={console,Response,DecompressionStream,Blob,URL,setTimeout,clearTimeout,history:{replaceState(){}},location:{hash:''},window:{addEventListener(){}},requestAnimationFrame:f=>f(),ExplorerMath:math,
 document:{getElementById(id){if(!els.has(id))els.set(id,new Element());return els.get(id);},querySelectorAll(){return[];},createElement(){return new Element();}},Plotly:{purge(){},newPlot(id,traces,layout){charts.push({id,traces,layout});}},
 fetch:async u=>{const file=u.split('?')[0].replace('./data/','');return new Response(fs.readFileSync(path.join(dir,file)));}};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(root,'details.js'),'utf8')+'\n'+fs.readFileSync(path.join(root,'app.js'),'utf8').replace(/init\(\);\s*$/,''),ctx);
ctx.inputD=D;ctx.inputM=M;ctx.inputB=B;
vm.runInContext('D=inputD;M=inputM;B=inputB;G={};',ctx);
(async()=>{
 async function view(code){charts.length=0;vm.runInContext(code+';prepareRows();renderFilters();renderKpis();',ctx);await vm.runInContext('renderCharts()',ctx);return charts.slice();}
 let all=await view("state.view='networks';state.metric='peak_import_rating_ratio';state.area='';");
 assert(all.find(x=>x.id==='network-weekly'));
 ctx.selected=Object.keys(M.cells)[240];
 let selected=await view("state.area=selected;state.week=53;");
 let hour=selected.find(x=>x.id==='network-hourly');assert.equal(hour.traces[0].y.length,24);assert.equal(hour.layout.yaxis.title.text,'Power (kW)');
 assert.notEqual(hour.traces[0].y[0],all.find(x=>x.id==='network-hourly').traces[0].y[0]);
 for(const kind of ['pv','potential','heat','exports','electricity']){ctx.kind=kind;const c=await view('state.profile=kind');assert(c.find(x=>x.id==='network-weekly'));}
 await view("state.view='retrofit';state.topic='solar';state.geometry='buildings';state.metric='pv_kWh';state.area='';");assert(charts.find(x=>x.id==='roof-weekly'));
 ctx.roof=B.rows.find(r=>r.pv_kWh>10000).id;await view('state.area=roof');assert(charts.find(x=>x.id==='roof-hourly'));
 await view("state.view='archetypes';state.metric='share';state.area='';state.archetype='all';");
 vm.runInContext('geoKey="buildings";state.area=B.rows.find(r=>r.dwellings>0).id;renderInspector();',ctx);
 await view("state.geometry='areas';state.archetype=D.archetypes[0].construction_sa;");
 await view("state.view='retrofit';state.topic='solar';state.metric='pv_GWh';state.area='';");
 console.log('PASS: view integration, per-cell selection, all six time-series quantities, final week, building solar profiles and archetype inspector');
})().catch(e=>{console.error(e);process.exitCode=1;});
