"""Refresh chapter financial figures and priorities from the completed scenario run."""
from pathlib import Path
import json,sys
import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap,Normalize
from matplotlib.patches import Patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import config
OUT=ROOT/'outputs/current/chapter4_infrastructure'
FIG=OUT/'figures'; FIG.mkdir(exist_ok=True)
e=pd.read_csv(ROOT/'outputs/current/scenarios/planning_ward_economics_U5.csv',index_col='dwelling_id')
old=pd.read_csv(ROOT/'_archive/20261009_before_incentive_revision/outputs/current/scenarios/planning_ward_economics_U5.csv',index_col='dwelling_id')
social=pd.read_csv(config.LSOA_SOCIAL_CSV).set_index('LSOA')
mapping=pd.read_csv(OUT/'dwelling_pv_inputs.csv',index_col=0)
cells=pd.read_csv(OUT/'all_scenario_cell_summary.csv')
util=cells.loc[cells.scenario.eq('U1')].set_index('zone_id').peak_import_rating_ratio*100

def priorities(frame):
    f=frame.join(social[['imd_decile','fuel_poor_pct']],on='LSOA')
    imd=f.imd_decile.fillna(5); fp=f.fuel_poor_pct.fillna(f.fuel_poor_pct.median())
    rent=f.tenure.astype(str).str.contains('rent',case=False).astype(float)
    f['equity']=(11-imd)/10+fp/fp.max()+.5*rent+.5*f.HTD.astype(float)
    f['utilisation']=mapping.zone_id.map(util).reindex(f.index).fillna(0)
    f['infrastructure']=f.utilisation*1e6+f.benefit_cost_kg_per_GBP.fillna(0)
    result={};audit={}
    for name,key in [('Carbon-first','benefit_cost_kg_per_GBP'),('Equity-first','equity'),('Infrastructure-led','infrastructure')]:
        ranked=f.sort_values(key,ascending=False,kind='stable').copy()
        ranked['budget_pct']=100*ranked.net_upfront_GBP.cumsum()/f.net_upfront_GBP.sum()
        selected=ranked.loc[ranked.budget_pct<=25]
        result[name]=ranked
        audit[name]={'dwellings':len(selected),'carbon_kt':float(selected.carbon_saving_kg.sum()/1e6),
                     'net_budget_MGBP':float(selected.net_upfront_GBP.sum()/1e6)}
    return result,audit

ranked,pri=priorities(e);_,old_pri=priorities(old)
ward=pd.read_csv(ROOT/'outputs/current/scenarios/planning_ward_summary.csv')
finance=ward.groupby('scenario')[['capex_gross_MGBP','incentives_MGBP']].sum()
finance['net_capex_MGBP']=finance.capex_gross_MGBP-finance.incentives_MGBP
finance.to_csv(OUT/'finance_summary.csv')
scheme_cols=['inc_BUS_GBP','inc_low_income_GBP','inc_social_housing_GBP','inc_GBIS_GBP']
scheme_summary=pd.DataFrame({'grant_MGBP':e[scheme_cols].sum()/1e6,'recipients':e[scheme_cols].gt(0).sum()})
scheme_summary.to_csv(OUT/'incentive_scheme_summary_U5.csv')
stats={'group_A_dwellings':int(e.group.eq('A').sum()),
       'group_A_median_ratio':float(e.loc[e.group.eq('A'),'benefit_cost_kg_per_GBP'].median()),
       'group_A_median_net_GBP':float(e.loc[e.group.eq('A'),'net_upfront_GBP'].median()),
       'payback_ok_pct':float(e.payback_ok.mean()*100),
       'payback_median_HTD':float(e.loc[e.HTD & np.isfinite(e.payback_years),'payback_years'].median()),
       'payback_median_other':float(e.loc[~e.HTD & np.isfinite(e.payback_years),'payback_years'].median()),
       'epc_imputed_count':int(e.epc_source.ne('certificate').sum()),
       'epc_imputed_pct':float(100*e.epc_source.ne('certificate').mean()),
       'priority_25pct':pri,'old_priority_reproduction':old_pri}
for name,frame in ranked.items():frame.to_csv(OUT/(name.lower().replace('-','_')+'_priorities.csv'))
(OUT/'finance_refresh_stats.json').write_text(json.dumps(stats,indent=2),encoding='utf-8')
print(json.dumps(stats,indent=2),flush=True)

plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
colors=['#72777d','#b25b38','#7b9853','#277f8e','#d5a43f','#695a8c']
wards=ward.loc[ward.scenario.eq('U0_baseline'),'ward'].tolist()
base=ward.loc[ward.scenario.eq('U0_baseline')].set_index('ward')
fig,axs=plt.subplots(1,3,figsize=(10,5),layout='constrained',sharey=True)
for k,(case,part) in enumerate(ward.groupby('scenario')):
    p=part.set_index('ward').loc[wards]
    y=np.arange(len(wards))+(k-2.5)*.10
    for ax,values in zip(axs,[p.carbon_kt,100*(p.bills_MGBP/base.loc[wards,'bills_MGBP']-1),p.capex_gross_MGBP-p.incentives_MGBP]):
        ax.scatter(values,y,s=19,color=colors[k],label=case[:2],zorder=3)
for ax,title,xlabel in zip(axs,['(a) Operational emissions','(b) Annual bill change','(c) Net capital expenditure'],['kt CO₂e/year','Change from baseline (%)','£ million']):
    ax.set_title(title,fontsize=10);ax.set_xlabel(xlabel);ax.grid(axis='x',alpha=.18)
axs[0].set(yticks=range(len(wards)),yticklabels=wards);axs[0].invert_yaxis()
axs[1].axvline(0,color='#999999',lw=.7)
fig.legend(*axs[2].get_legend_handles_labels(),ncol=6,loc='outside lower center',fontsize=9,frameon=False)
fig.savefig(FIG/'refresh_figure6.png',dpi=300,bbox_inches='tight');plt.close(fig)

buildings=gpd.read_file(config.BUILDINGS_GPKG).to_crs(27700).set_index('toid')
boundary=gpd.read_file(config.TOWN_GPKG).to_crs(27700)
wardgeo=gpd.read_file(config.WARDS_GPKG).to_crs(27700)
bounds=boundary.total_bounds
buildings=buildings.cx[bounds[0]:bounds[2],bounds[1]:bounds[3]]
def map_base(ax):
    buildings.plot(ax=ax,color='#eceeef',edgecolor='none',zorder=1)
    wardgeo.boundary.plot(ax=ax,color='#6b7478',linewidth=.45,zorder=3)
    boundary.boundary.plot(ax=ax,color='#35434a',linewidth=.7,zorder=4)
    ax.set_xlim(bounds[0]-100,bounds[2]+100);ax.set_ylim(bounds[1]-100,bounds[3]+100);ax.set_axis_off()
    x=bounds[0]+.05*(bounds[2]-bounds[0]);y=bounds[1]+.045*(bounds[3]-bounds[1])
    ax.plot([x,x+1000],[y,y],color='black',lw=2.5,zorder=5);ax.text(x+500,y+60,'1 km',ha='center',fontsize=9)

group=e.dropna(subset=['group']).groupby('toid').group.agg(lambda s:s.mode().sort_values().iloc[0])
geo=buildings[['geometry']].join(group)
palette=['#a80056','#df6756','#edb451','#c6d77e','#60b5a6','#4476a4']
fig,ax=plt.subplots(figsize=(8,6.0),layout='constrained');map_base(ax)
for g,color in zip('ABCDEF',palette):
    part=geo.loc[geo.group.eq(g)]
    if len(part):part.plot(ax=ax,color=color,edgecolor='none',zorder=2)
ax.legend(handles=[Patch(facecolor=c,label=g) for g,c in zip('ABCDEF',palette)],ncol=6,loc='upper center',title='Benefit–cost group',frameon=False)
fig.savefig(FIG/'refresh_figure9a.png',dpi=300,bbox_inches='tight');plt.close(fig)

fig,axs=plt.subplots(1,3,figsize=(10,3.9),layout='constrained')
vals=e.benefit_cost_kg_per_GBP.dropna().sort_values(ascending=False)
axs[0].plot(np.linspace(0,100,len(vals)),vals,color='#287a86',lw=1)
axs[0].set(yscale='log',xlabel='Dwelling rank (%)',ylabel='15-year carbon saving (kgCO₂e / £)',title='(b) Carbon per net cost')
finite=e.loc[np.isfinite(e.payback_years)]
axs[1].boxplot([finite.loc[finite.HTD,'payback_years'],finite.loc[~finite.HTD,'payback_years']],tick_labels=['Flagged','Other'],showfliers=False)
axs[1].axhline(12.5,color='#cc884b',ls='--',lw=1)
axs[1].set(ylabel='Simple payback (years)',title='(c) Construction difficulty')
rented=e.tenure.astype(str).str.contains('rent',case=False)
shares=rented.groupby(e.group).mean().reindex(list('ABCDEF'))*100
axs[2].bar(shares.index,shares,color='#8d76ae');axs[2].axhline(rented.mean()*100,color='#666666',ls='--',lw=1)
axs[2].set(ylabel='Rented dwellings (%)',xlabel='Benefit–cost group',title='(d) Tenure by ranking group')
for ax in axs:ax.title.set_fontsize(10)
fig.savefig(FIG/'refresh_figure9bcd.png',dpi=300,bbox_inches='tight');plt.close(fig)

for suffix,(name,frame) in zip('abc',ranked.items()):
    values=frame.groupby('toid').budget_pct.mean()
    g=buildings[['geometry']].join(values).dropna(subset=['budget_pct'])
    fig,ax=plt.subplots(figsize=(8,5.5),layout='constrained');map_base(ax)
    g.plot(column='budget_pct',ax=ax,cmap='viridis',vmin=0,vmax=100,edgecolor='none',zorder=2)
    fig.colorbar(plt.cm.ScalarMappable(norm=Normalize(0,100),cmap='viridis'),ax=ax,shrink=.77,label='Cumulative net budget position (%)')
    ax.set_title(name,loc='left',fontsize=12)
    fig.savefig(FIG/f'refresh_figure17{suffix}.png',dpi=300,bbox_inches='tight');plt.close(fig)
print('Financial figures refreshed',flush=True)
