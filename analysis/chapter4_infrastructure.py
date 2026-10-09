"""Extend the verified hourly PV balance to all six chapter scenarios."""
from pathlib import Path
import sys, json, hashlib
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import geopandas as gpd
from matplotlib.colors import Normalize

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analysis import u4_pv_balance as B
from ukubem import unified as U

OUT = ROOT/'outputs/current/chapter4_infrastructure'
OUT.mkdir(exist_ok=True)
(OUT/'figures').mkdir(exist_ok=True)
OLD = ROOT/'outputs/current/u4_pv_balance'
study = B.load_study(B.Settings(output_dir='outputs/current/chapter4_infrastructure', orientation_sensitivity=False))
B.solar_shapes(study)
for name in ['U2_envelope','U3_envelope_elec','U5_envelope_elec_pv']:
    study['source'][name] = pd.read_parquet(U.OUT/'scenarios'/f'{name}.parquet').loc[study['x'].index]
x = study['x']
B.check_close(study['source']['U5_envelope_elec_pv'].pv_generation_kWh, x.capped_kWh,
              'U5_reproduces_capped_PV', study['checks'])
ids = study['zone_ids']
rating = study['cells'].screen_rating_kW.to_numpy()
codes = ids.get_indexer(x.zone_id)
annual_pv = np.bincount(codes, weights=x.capped_kWh, minlength=len(ids))
pv = annual_pv[:,None]*study['pv_shapes']['south'][None,:]
spec = {'U5': {'annual_kWh':x.capped_kWh.to_numpy(), 'shape':study['pv_shapes']['south']}}
d2,_ = B.reconstruct(study, False, fabric=True)
d3,selfs = B.reconstruct(study, True, spec, fabric=True)
new = {'U2':B.balance(d2,np.zeros_like(d2),np.zeros_like(d2)),
       'U3':B.balance(d3,np.zeros_like(d3),np.zeros_like(d3)),
       'U5':B.balance(d3,pv,selfs['U5'])}
summaries=[];cell_tables=[];aggregates={}
previous=pd.read_csv(OLD/'scenario_summary.csv').set_index('case')
previous_cells=pd.read_csv(OLD/'cell_summary.csv')
for short,long in [('U0','U0_existing_no_pv'),('U1','U1_electrification_no_pv'),('U4','U4_capped_south')]:
    row=previous.loc[long].to_dict();row['scenario']=short;summaries.append(row)
    cells=previous_cells.loc[previous_cells['case'].eq(long)].copy();cells['scenario']=short
    cell_tables.append(cells)
    aggregates[short]=pd.read_csv(OLD/f'{long}_aggregate_hourly.csv',index_col=0,parse_dates=True)
for key,b in new.items():
    B.check_close(b['demand_kw'],b['cell_import_kw']+b['cell_absorbed_pv_kw'],key+'_load_identity',study['checks'])
    B.check_close(b['pv_kw'],b['cell_export_kw']+b['cell_absorbed_pv_kw'],key+'_PV_identity',study['checks'])
    B.check_close(b['meter_import_kw']-b['meter_export_kw'],b['net_kw'],key+'_meter_identity',study['checks'])
    ag=pd.DataFrame({k:v.sum(0) for k,v in b.items()},index=study['time'])
    ag.rename_axis('time').to_csv(OUT/f'{key}_aggregate_hourly.csv')
    aggregates[key]=ag
    annual=ag.sum()/1e6
    cp=b['cell_import_kw'].max(1);xp=b['cell_export_kw'].max(1)
    summaries.append(dict(scenario=key, gross_demand_GWh=annual.demand_kw, PV_GWh=annual.pv_kw,
        capacity_MWp=float(x.capped_kWp.sum()/1000) if key=='U5' else 0.0,
        additional_cell_coincidence_GWh=annual.meter_import_kw-annual.cell_import_kw,
        meter_import_GWh=annual.meter_import_kw,meter_export_GWh=annual.meter_export_kw,
        cell_import_GWh=annual.cell_import_kw,cell_export_GWh=annual.cell_export_kw,
        dwelling_self_consumption_pct=100*annual.dwelling_self_kw/annual.pv_kw if annual.pv_kw else np.nan,
        cell_PV_absorption_pct=100*annual.cell_absorbed_pv_kw/annual.pv_kw if annual.pv_kw else np.nan,
        coincident_cell_import_peak_MW=ag.cell_import_kw.max()/1000,
        coincident_cell_export_peak_MW=ag.cell_export_kw.max()/1000,
        cells_above_import_rating_screen=int((cp>rating).sum()),
        cells_above_export_rating_screen=int((xp>rating).sum())))
    cell_tables.append(pd.DataFrame(dict(scenario=key,zone_id=ids,
        cell_import_MWh=b['cell_import_kw'].sum(1)/1000,cell_export_MWh=b['cell_export_kw'].sum(1)/1000,
        cell_import_peak_kW=cp,cell_export_peak_kW=xp,
        peak_import_rating_ratio=cp/rating,peak_export_rating_ratio=xp/rating)))
summary=pd.DataFrame(summaries).set_index('scenario').sort_index()
cells=pd.concat(cell_tables,ignore_index=True)
summary['median_peak_import_rating_pct']=cells.groupby('scenario').peak_import_rating_ratio.median()*100
summary['peak_hour']=[str(aggregates[k].cell_import_kw.idxmax()) for k in summary.index]
summary.to_csv(OUT/'all_scenario_summary.csv')
cells.to_csv(OUT/'all_scenario_cell_summary.csv',index=False)
(OUT/'checks.json').write_text(json.dumps(study['checks'],indent=2),encoding='utf-8')
meta={'population':len(x),'cells':len(ids),'excluded_unassigned':1,
      'scope':'Residential allocations in ten wards; inferred nearest-substation cells; unchanged ONAN rating x 0.95',
      'PV':'4 kWp cap per allocated dwelling; annual rooftop yield; south-facing hourly shape; no storage or curtailment',
      'source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
          [Path(__file__),ROOT/'analysis/u4_pv_balance.py',OLD/'scenario_summary.csv',OLD/'cell_summary.csv']+
          [U.OUT/'scenarios'/f'{s}.parquet' for s in study['source']]}}
(OUT/'manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')

plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
colors=['#72777d','#b25b38','#7b9853','#277f8e','#d5a43f','#695a8c']
labels=['U0','U1','U2','U3','U4','U5']
fig,axs=plt.subplots(2,2,figsize=(8.0,6.3),layout='constrained')
for ax,col,title,unit,fmt in [
    (axs[0,0],'cell_import_GWh','(a) Annual electricity imports','GWh/year','.1f'),
    (axs[0,1],'coincident_cell_import_peak_MW','(b) Coincident annual import peak','MW','.1f'),
    (axs[1,0],'cells_above_import_rating_screen','(c) Import screening exceedances','Number of cells','.0f'),
    (axs[1,1],'cell_export_GWh','(d) Annual electricity exports','GWh/year','.1f')]:
    vals=summary[col].to_numpy();bars=ax.bar(range(6),vals,color=colors,width=.65)
    ax.bar_label(bars,labels=[format(v,fmt) for v in vals],padding=3,fontsize=9)
    ax.set(xticks=range(6),xticklabels=labels,ylabel=unit,title=title,ylim=(0,max(vals)*1.2))
    ax.title.set_fontsize(10)
    ax.tick_params(axis='x',labelsize=10);ax.grid(axis='y',alpha=.18);ax.set_axisbelow(True)
fig.savefig(OUT/'figures/scenario_infrastructure.png',dpi=260,bbox_inches='tight')
plt.close(fig)

poly=gpd.read_file(OLD/'substation_cells.gpkg',layer='inferred_cells').set_index('zone_id')
fig,axs=plt.subplots(2,2,figsize=(9.4,7.7),layout='constrained')
upper=max(1.1,cells.peak_import_rating_ratio.quantile(.98))
for ax,key,title in zip(axs.flat,['U0','U1','U3','U5'],['(a) U0 Baseline','(b) U1 Electrification','(c) U3 Fabric and electrification','(d) U5 Fabric, electrification and PV']):
    tab=cells.loc[cells.scenario.eq(key)].set_index('zone_id')
    g=poly[['geometry']].join(tab[['peak_import_rating_ratio']])
    g.plot(column='peak_import_rating_ratio',ax=ax,cmap='YlOrRd',vmin=0,vmax=upper,edgecolor='#555555',linewidth=.15)
    ax.set_axis_off();ax.set_title(title,fontsize=11)
cb=fig.colorbar(plt.cm.ScalarMappable(norm=Normalize(0,upper),cmap='YlOrRd'),ax=axs,shrink=.75,extend='max')
cb.set_label('Peak residential import / screening rating')
fig.savefig(OUT/'figures/scenario_screening_maps.png',dpi=260,bbox_inches='tight')
plt.close(fig)
print(summary.to_string(),flush=True)
print('All checks passed:',all(c['passed'] for c in study['checks'].values()),flush=True)
