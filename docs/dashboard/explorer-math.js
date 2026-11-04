/* Pure calculations shared by the dashboard and its numerical checks. */
(function(root){
 'use strict';
 const M={
  series(p,meta,shape,scenario,kind){
   const n=Number(scenario.slice(1)), k=n<4?n:(n===4?1:3);
   const demand=p['e'+k];
   if(kind==='electricity')return demand;
   if(kind==='heat')return p['h'+k];
   if(kind==='potential')return shape.map(x=>x*meta.potential_kWh);
   const pv=shape.map(x=>x*(n>=4?meta.pv_kWh:0));
   if(kind==='pv')return pv;
   if(kind==='imports')return p['i'+n]||demand.map((x,i)=>Math.max(x-pv[i],0));
   if(kind==='exports')return p['x'+n]||demand.map((x,i)=>Math.max(pv[i]-x,0));
   throw new Error('Unknown profile: '+kind);
  },
  weeks(a){return Array.from({length:Math.ceil(a.length/168)},(_,w)=>a.slice(w*168,Math.min((w+1)*168,a.length)).reduce((s,x)=>s+x,0)/1000);},
  solarBin(v){return v==null?-1:v===0?0:v<=2000?1:v<=5000?2:v<=10000?3:v<=25000?4:5;}
 };
 root.ExplorerMath=M;
 if(typeof module!=='undefined')module.exports=M;
})(typeof globalThis!=='undefined'?globalThis:this);
