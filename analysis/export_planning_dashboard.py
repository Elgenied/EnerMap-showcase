"""Publish aggregate planning layers; never export household records or OS footprints.

Usage: python analysis/export_planning_dashboard.py --source /path/to/enermap_ubem
The source is a completed research run. This does not execute thermal simulations.
"""
from pathlib import Path
import argparse, json, hashlib, shutil
import numpy as np
import pandas as pd
import geopandas as gpd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/dashboard/data'
OUT.mkdir(parents=True, exist_ok=True)
parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--figure-dir', type=Path, help='Optional current publication figures')
args = parser.parse_args()
SOURCE = args.source
C = SOURCE / 'outputs/current'
used = []
def csv(p):
    used.append(p); return pd.read_csv(p)
def pq(p):
    used.append(p); return pd.read_parquet(p)
def js(p):
    used.append(p); return json.loads(p.read_text(encoding='utf-8'))
def clean(obj):
    if isinstance(obj, dict): return {str(k): clean(v) for k,v in obj.items()}
    if isinstance(obj, (list,tuple)): return [clean(v) for v in obj]
    if isinstance(obj, (np.integer,)): return int(obj)
    if isinstance(obj, (np.floating,float)): return round(float(obj),5) if np.isfinite(obj) else None
    if isinstance(obj, np.bool_): return bool(obj)
    if obj is pd.NA: return None
    return obj
def write(name,obj):
    (OUT/name).write_text(json.dumps(clean(obj), separators=(',',':'),allow_nan=False),encoding='utf-8')
def records(df): return df.replace([np.inf,-np.inf],np.nan).astype(object).where(df.notna(),None).to_dict('records')
def geo(p,key,name,layer=None):
    used.append(p)
    g=gpd.read_file(p,layer=layer)
    g=g[[key,'geometry']].rename(columns={key:'id'})
    g.geometry=g.geometry.simplify(12,preserve_topology=True)
    g=g.to_crs(4326)
    obj=json.loads(g.to_json(drop_id=True))
    write(name,obj)
    return g

base=pq(C/'scenarios/U0_baseline.parquet')
fabric=pq(C/'scenarios/U2_envelope.parquet')
u5=pq(C/'scenarios/U5_envelope_elec_pv.parquet')
assert len(base)==57300 and base.index.is_unique
totals=csv(C/'baseline/lsoa_demand_totals.csv').set_index('LSOA')
validation=csv(C/'baseline/lsoa_validation.csv').set_index('LSOA')
social=csv(SOURCE/'data/inputs/social/lsoa_social_context.csv').set_index('LSOA')
lsoa=totals.join(validation[['lsoa_name','sim_gas_mean_kWh','sim_elec_mean_kWh','gas_mean_kWh','elec_mean_kWh']])
lsoa=lsoa.join(social[['imd_decile','fuel_poor_pct']])
lsoa['carbon_kt']=base.groupby('LSOA').carbon_kg.sum()/1e6
lsoa['heat_GWh']=lsoa.Q_H_kWh/1e6
lsoa['gas_GWh']=lsoa.delivered_gas_kWh/1e6
lsoa['electricity_GWh']=lsoa.delivered_elec_kWh/1e6
lsoa['heat_intensity']=lsoa.Q_H_kWh/lsoa.floor_area_final
lsoa['gas_error_pct']=100*(lsoa.sim_gas_mean_kWh/lsoa.gas_mean_kWh-1)
lsoa['elec_error_pct']=100*(lsoa.sim_elec_mean_kWh/lsoa.elec_mean_kWh-1)
lookup=csv(SOURCE/'outputs/archetypes/archetype_lookup.csv')
chars=csv(SOURCE/'outputs/archetypes/characterisation_construction.csv')
reps=pq(C/'baseline/virtual_representative_inputs.parquet')
mix=base.groupby(['LSOA','construction_sa']).size().unstack(fill_value=0)
dominant=mix.idxmax(axis=1)
lsoa['archetype']=dominant
lsoa['archetype_pct']=100*mix.max(axis=1)/mix.sum(axis=1)
types=lookup.set_index('construction_sa').stratum
base['form']=base.construction_sa.map(types)
for typ in ['Detached','Semi-detached','Terraced','Flat']:
    lsoa[typ]=100*base.form.eq(typ).groupby(base.LSOA).mean()
geo(C/'baseline/lsoa_demand.gpkg','LSOA','lsoa.geojson')
geo(SOURCE/'data/inputs/spatial/wards.gpkg','WD25NM','wards.geojson')
geo(C/'u4_pv_balance/substation_cells.gpkg','zone_id','cells.geojson',layer='inferred_cells')

ward_summary=csv(C/'scenarios/planning_ward_summary.csv')
scenario_summary=csv(C/'scenarios/scenario_summary.csv')
scenario_summary=scenario_summary[scenario_summary.scope=='planning_wards']
ward_summary['net_capex_MGBP']=ward_summary.capex_gross_MGBP-ward_summary.incentives_MGBP
finance=csv(C/'chapter4_infrastructure/finance_summary.csv')
econ=csv(C/'scenarios/planning_ward_economics_U5.csv')
assert len(econ)==28663
plan=base[base.in_study_area].copy()
plan['heat_saving_kWh']=plan.Q_H_kWh-fabric.loc[plan.index,'Q_H_kWh']
plan['heat_saving_pct']=100*plan.heat_saving_kWh/plan.Q_H_kWh
plan['pv_generation_kWh']=u5.loc[plan.index,'pv_generation_kWh']
potential=pq(SOURCE/'data/inputs/stock/study_area_population.parquet')
if 'dwelling_id' in potential: potential=potential.set_index('dwelling_id')
plan['pv_potential_kWh']=potential.pv_annual_kWh.reindex(plan.index)
plan['pv_capacity_kWp']=u5.loc[plan.index,'pv_kWp_installed']
econ=econ.set_index('dwelling_id')
for col in ['HTD','tenure','payback_years','payback_ok','carbon_saving_kg','net_upfront_GBP','benefit_cost_kg_per_GBP']:
    plan[col]=econ[col].reindex(plan.index)
plan['rented']=plan.tenure.astype(str).str.contains('rent',case=False)
plan['imd_decile']=plan.LSOA.map(social.imd_decile)
plan['fuel_poor_pct']=plan.LSOA.map(social.fuel_poor_pct)
def retrofit_agg(group):
    return pd.Series(dict(dwellings=len(group),heat_saving_GWh=group.heat_saving_kWh.sum()/1e6,
        saving_per_home=group.heat_saving_kWh.mean(),heat_saving_pct=100*group.heat_saving_kWh.sum()/group.Q_H_kWh.sum(),
        htd_pct=100*group.HTD.mean(),rented_pct=100*group.rented.mean(),
        payback_ok_pct=100*group.payback_ok.mean(),median_payback=group.payback_years.replace([np.inf,-np.inf],np.nan).median(),
        imd_decile=group.imd_decile.mean(),fuel_poor_pct=group.fuel_poor_pct.mean(),
        carbon_saving_kt=group.carbon_saving_kg.sum()/1e6,net_cost_MGBP=group.net_upfront_GBP.sum()/1e6,
        pv_GWh=group.pv_generation_kWh.sum()/1e6,pv_potential_GWh=group.pv_potential_kWh.sum()/1e6,
        pv_MWp=group.pv_capacity_kWp.sum()/1000))
retrofit={k:records(plan.groupby(k).apply(retrofit_agg,include_groups=False).reset_index()) for k in ['ward','LSOA']}
priority={}
for mode in ['carbon_first','equity_first']:
    ranked=csv(C/f'chapter4_infrastructure/{mode}_priorities.csv')
    rows=[]
    for pct in range(0,101,5):
        selected=ranked[ranked.budget_pct<=pct+1e-8]
        wards=[]
        for ward,group in ranked.groupby('ward'):
            a=group[group.budget_pct<=pct+1e-8]
            wards.append(dict(ward=ward,selected=len(a),coverage_pct=100*len(a)/len(group),
                carbon_kt=a.carbon_saving_kg.sum()/1e6,investment_MGBP=a.net_upfront_GBP.sum()/1e6))
        rows.append(dict(budget_pct=pct,dwellings=len(selected),carbon_kt=selected.carbon_saving_kg.sum()/1e6,
            investment_MGBP=selected.net_upfront_GBP.sum()/1e6,wards=wards))
    priority[mode]=rows

arch=[]
for r in lookup.to_dict('records'):
    key=r['construction_sa']; ch=chars.set_index('label').loc[key]
    rp=reps[reps.construction_sa==key]
    sel=base[base.construction_sa==key]
    r.update(wall_U=ch.wall_U__median,roof_U=ch.roof_U__median,window_U=ch.window_U__median,
        floor_area=ch.floor_area_median,wall=ch.wall_construction_resolved__mode,
        insulation=ch.wall_insulation__mode,glazing=ch.glazing_type__mode,
        roof=ch.roof_type__mode,heat_intensity=sel.Q_H_kWh.sum()/sel.floor_area_final.sum(),
        sizes=records(rp[['size_class','floor_area_final']].sort_values('size_class',key=lambda s:s.map({'S':0,'M':1,'L':2}))),
        systems=sel.systems_fam.value_counts().to_dict())
    arch.append(r)

cells=csv(C/'chapter4_infrastructure/all_scenario_cell_summary.csv')
cols=['zone_id','scenario','cell_import_MWh','cell_export_MWh','cell_import_peak_kW',
      'cell_export_peak_kW','peak_import_rating_ratio','peak_export_rating_ratio']
solar=csv(C/'u4_pv_balance/annual_solar_potential_by_substation.csv')
infra=csv(C/'chapter4_infrastructure/all_scenario_summary.csv')
all_geo=gpd.read_file(C/'u4_pv_balance/substation_cells.gpkg',layer='inferred_cells')
cellinfo=records(all_geo[['zone_id','modelled_dwellings','reported_rating_kVA','screen_rating_kW']])

# A complete figure library supplements interactive aggregate layers.
gallery=[]
target=ROOT/'docs/dashboard/figures';target.mkdir(exist_ok=True)
figs=[('baseline/../figures/09_building_demand_maps.png','Building energy demand','Demand'),
 ('figures/09_neighbourhood_demand_detail.png','Neighbourhood detail','Demand'),
 ('figures/09_demand_totals_maps.png','Annual demand by LSOA','Demand'),
 ('figures/10_retrofit_saving_map.png','Fabric retrofit savings','Retrofit'),
 ('chapter4_infrastructure/figures/refresh_figure17a.png','Carbon-first priority map','Priorities'),
 ('chapter4_infrastructure/figures/refresh_figure17b.png','Equity-first priority map','Priorities'),
 ('chapter4_infrastructure/figures/refresh_figure9a.png','Investment and financial screening','Finance'),
 ('chapter4_infrastructure/figures/refresh_figure9bcd.png','Construction difficulty and tenure','Equity'),
 ('chapter4_infrastructure/figures/scenario_screening_maps.png','Electricity import screening','Networks'),
 ('chapter4_infrastructure/figures/scenario_infrastructure.png','Electricity imports and peaks','Networks')]
for path,title,section in figs:
    src=C/path
    if src.exists():
        dest=target/src.name;shutil.copy2(src,dest)
        gallery.append(dict(title=title,section=section,src='figures/'+src.name))
if args.figure_dir:
    for filename,title,section in [
        ('Figure_PV_building_overview_and_detail.png','Rooftop solar · overview and building detail','Solar'),
        ('Figure_PV_technical_potential_by_ward.png','Solar generation potential by ward','Solar'),
        ('Figure_hard_to_decarbonise_prevalence_by_ward.png','Construction-difficulty prevalence by ward','Equity')]:
        src=args.figure_dir/filename
        if src.exists():
            shutil.copy2(src,target/filename)
            gallery.append(dict(title=title,section=section,src='figures/'+filename))

bundle=dict(case_study='Guildford',run_date=js(C/'completion.json')['completed_utc'][:10],
    summary=js(C/'baseline/summary.json'),lsoa=records(lsoa.reset_index()),
    archetypes=arch,archetype_mix=records(mix.reset_index()),scenarios=records(scenario_summary),
    wards=records(ward_summary),finance=records(finance),retrofit=retrofit,priority=priority,
    cells=records(cells[cols]),cell_info=cellinfo,solar=records(solar),infrastructure=records(infra),
    validation=records(validation.reset_index()),gallery=gallery,
    incentive_summary=records(csv(C/'chapter4_infrastructure/incentive_scheme_summary_U5.csv')),
    finance_stats=js(C/'chapter4_infrastructure/finance_refresh_stats.json'))
write('planning.json',bundle)

# Hourly profiles keep the borough and network-screening populations separate.
hourly={}
for short,long in [('U0','U0_baseline'),('U1','U1_electrification'),('U2','U2_envelope'),('U3','U3_envelope_elec'),('U4','U4_elec_pv'),('U5','U5_envelope_elec_pv')]:
    h=csv(C/f'scenarios/{long}_hourly.csv')
    if short in ['U0','U1','U4']:
        f={'U0':'U0_existing_no_pv','U1':'U1_electrification_no_pv','U4':'U4_capped_south'}[short]
        net=csv(C/f'u4_pv_balance/{f}_aggregate_hourly.csv')
    else: net=csv(C/f'chapter4_infrastructure/{short}_aggregate_hourly.csv')
    hourly[short]=dict(electricity=(h.delivered_electricity_kWh/1000).round(4).tolist(),
        heat=(h.space_heat_kWh/1000).round(4).tolist(),
        imports=(net.cell_import_kw/1000).round(4).tolist(),exports=(net.cell_export_kw/1000).round(4).tolist())
write('hourly.json',dict(start=str(h.time.iloc[0]),series=hourly))

# Internal verification of exported sums and key published findings.
assert np.isclose(lsoa.gas_GWh.sum(),bundle['summary']['totals_GWh']['delivered_gas_kWh'])
assert int(lookup.n.sum())==57300 and len(reps)==102
assert priority['carbon_first'][5]['dwellings']==9063
assert priority['equity_first'][5]['dwellings']==6850
assert np.isclose(sum(r['heat_saving_GWh'] for r in retrofit['ward']),plan.heat_saving_kWh.sum()/1e6)
write('provenance.json',dict(source_run=bundle['run_date'],aggregation='LSOA, ward, construction group and inferred substation cell; no household identifiers',
    geometry='ONS LSOA and ward boundaries, simplified to 12 m; inferred Voronoi cells, not electrical feeder boundaries',
    sources=[dict(file=str(p.relative_to(SOURCE)).replace('\\','/'),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(set(used))]))
print('Export verified:',len(lsoa),'LSOAs;',len(arch),'construction groups; 102 representatives; 10 wards; 271 cells')
