"""Export footprint display layers and cell-specific profiles from a completed run.

No thermal simulations are rerun. Electricity is reconstructed using the same
accounting functions as analysis/u4_pv_balance.py and reconciled before publishing.
"""
from pathlib import Path
import argparse, sys, json, gzip, hashlib
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import mapping as geom_mapping

P=argparse.ArgumentParser()
P.add_argument('--source', type=Path, required=True)
args=P.parse_args()
S=args.source.resolve(); C=S/'outputs/current'
OUT=Path(__file__).resolve().parents[1]/'docs/dashboard/data'
sys.path.insert(0,str(S))
from ukubem import measures, heatpumps as HP
from ukubem.accounting import family_masks, usage_for
from ukubem.profiles import epw_hourly

checks={}
def close(a,b,label,atol=0.001):
    np.testing.assert_allclose(a,b,rtol=1e-7,atol=atol,err_msg=label)
    checks[label]=float(np.max(np.abs(np.asarray(a)-np.asarray(b))))
def write(name,obj,compressed=False):
    data=json.dumps(obj,separators=(',',':'),allow_nan=False).encode()
    path=OUT/name; path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(gzip.compress(data,compresslevel=9,mtime=0) if compressed else data)
def arr(x): return np.round(x,3).tolist()

base=pd.read_parquet(C/'scenarios/U0_baseline.parquet')
print('Preparing building footprints',flush=True)
build=gpd.read_file(S/'data/inputs/spatial/buildings.gpkg').set_index('toid')
pv=gpd.read_file(S/'data/inputs/spatial/pv_building_potential.gpkg').set_index('toid')
counts=base.groupby(['toid','construction_sa']).size().rename('n').reset_index()
mix={k:dict(zip(v.construction_sa,v.n.astype(int))) for k,v in counts.groupby('toid')}
dom=counts.sort_values(['toid','n','construction_sa'],ascending=[True,False,True]).drop_duplicates('toid').set_index('toid')
tot=base.groupby('toid').size()
keep=build.index.intersection(tot.index.union(pv.index))
build=build.loc[keep].copy()
build['id']=['B'+str(i+1).zfill(5) for i in range(len(build))]
features=[]; data=[]
geo=build.geometry.simplify(0.15,preserve_topology=True).to_crs(4326)
for key,r in build.iterrows():
    geom=geo.loc[key]
    if geom is None or geom.is_empty: continue
    p=pv.loc[key] if key in pv.index else None
    d=dom.loc[key] if key in dom.index else None
    def val(k):
        return round(float(p[k]),3) if p is not None and pd.notna(p[k]) else None
    row=dict(id=r.id,name='Building '+r.id[1:],dwellings=int(tot.get(key,0)),
        archetype=str(d.construction_sa) if d is not None else None,
        archetype_pct=round(100*int(d.n)/tot[key],1) if d is not None else None,
        archetypes=mix.get(key,{}),
        pv_kWh=val('energy_kwh_yr'),pv_kWp=val('peak_power_kwp'),
        suitable_m2=val('suitable_area_m2'),irradiation=val('mean_irrad_kwh_m2'))
    data.append(row)
    # Six decimal places: display geometry; no address, UPRN or TOID exported.
    gj=geom_mapping(geom)
    def rounded(v):return [rounded(x) for x in v] if isinstance(v,(list,tuple)) else round(v,6)
    gj['coordinates']=rounded(gj['coordinates'])
    features.append(dict(type='Feature',properties={'id':r.id},geometry=gj))
write('buildings.json.gz',dict(rows=data,geometry=dict(type='FeatureCollection',features=features)),True)
close(sum(r['pv_kWh'] or 0 for r in data),pv.energy_kwh_yr.sum(),'footprint_PV_annual_kWh',20)
print(f'{len(data)} footprints; {sum(r["pv_kWh"] or 0 for r in data)/1e6:.3f} GWh roof potential',flush=True)

mapping=pd.read_csv(C/'u4_pv_balance/dwelling_pv_inputs.csv').set_index('dwelling_id')
stock=pd.read_parquet(S/'outputs/representatives/dwelling_inputs.parquet')
assign=pd.read_parquet(C/'baseline/stock_assignment.parquet')
x=stock.loc[mapping.index].copy(); x['size_class']=assign.loc[x.index,'size_class']
ids=pd.Index(sorted(mapping.zone_id.unique()))
zi=ids.get_indexer(mapping.zone_id)
reps=pd.read_csv(C/'baseline/representatives.csv')
sh=np.load(C/'baseline/annual_shapes.npz')
eshapes={(r.construction_sa,r.size_class):sh[str(r.dwelling_id)].astype(float) for r in reps.itertuples()}
t=epw_hourly(S/'data/inputs/weather/GBR_ENG_Farnborough.AP.037680_TMYx.2007-2021.epw').T_out_C.to_numpy()
water=HP.annual_dhw_shape(); cop=HP.cop_hourly(t,kind='ASHP',emitter='radiator'); copw=HP.cop_hourly(t,kind='ASHP',emitter='dhw')
series={}
scenario_names=['U0_baseline','U1_electrification','U2_envelope','U3_envelope_elec']
fabric=['cavity_fill','solid_wall_iwi','loft_topup','flat_roof','glazing_upgrade','draught_proofing']
for n,name in enumerate(scenario_names):
    print('Reconstructing '+name,flush=True)
    inp,applied=measures.apply_package(x,(fabric if n>=2 else [])+(['ashp'] if n%2 else []),'planning')
    masks=family_masks(inp.systems_fam.to_numpy(),inp.gas_connected.fillna(False).to_numpy(bool))
    area=inp.floor_area_final.to_numpy(float); usage=usage_for(area,masks); conv=applied.ashp.to_numpy(bool)
    heat=np.load(C/('packages/R2/group_hourly.npz' if n>=2 else 'baseline/group_hourly.npz'))
    hg={(r.construction_sa,r.size_class):heat[str(r.dwelling_id)].astype(float)/1000 for r in reps.itertuples()}
    demand=np.zeros((len(ids),8760)); useful=np.zeros_like(demand); annual=np.zeros(len(x))
    for g,pos in inp.groupby(['construction_sa','size_class'],observed=True).indices.items():
        for isnew in [False,True]:
            ii=np.asarray(pos)[conv[pos]==isnew]
            if not len(ii):continue
            q=HP.heat_pump_hourly(hg[g]*1000,t)/1000 if isnew else hg[g]
            weights=[area[ii],usage['elec_nonheat_kWh'][ii],usage['dhw_pou_elec_kWh'][ii],
                area[ii]*masks['hp'][ii],usage['dhw_system_kWh'][ii]*masks['hp'][ii],
                area[ii]*masks['direct'][ii],usage['dhw_system_kWh'][ii]*masks['direct'][ii]]
            w=[np.bincount(zi[ii],weights=a,minlength=len(ids))[:,None] for a in weights]
            useful+=w[0]*q
            bases=[eshapes[g],water,q/cop,water/copw,q,water]
            for a,basis in zip(w[1:],bases):demand+=a*basis
            annual[ii]=sum(a*basis.sum() for a,basis in zip(weights[1:],bases))
    target=pd.read_parquet(C/'scenarios'/f'{name}.parquet').loc[x.index]
    close(annual,target.delivered_elec_kWh.to_numpy(),name+'_all_dwelling_annual')
    oldname=['U0_existing_no_pv','U1_electrification_no_pv'][n] if n<2 else f'U{n}'
    ag=pd.read_csv(C/('u4_pv_balance' if n<2 else 'chapter4_infrastructure')/f'{oldname}_aggregate_hourly.csv')
    close(demand.sum(0),ag.demand_kw.to_numpy(),name+'_all_hours')
    series[f'e{n}']=demand;series[f'h{n}']=useful

shape=pd.read_csv(C/'u4_pv_balance/pv_hourly_shapes.csv').south.to_numpy()
close(shape.sum(),1,'PV_shape_normalised')
pvannual=np.bincount(zi,weights=mapping.capped_kWh,minlength=len(ids))
potential=np.bincount(zi,weights=mapping.pv_annual_kWh,minlength=len(ids))
generation=pvannual[:,None]*shape
expected=pd.read_csv(C/'chapter4_infrastructure/all_scenario_cell_summary.csv')
agg={k:v.sum(0) for k,v in series.items()}
for n in range(6):
    d=series['e'+str(n if n<4 else (1 if n==4 else 3))]
    p=generation if n>=4 else 0
    imp=np.maximum(d-p,0); exp=np.maximum(p-d,0)
    tar=expected.loc[expected.scenario.eq('U'+str(n))].set_index('zone_id').loc[ids]
    close(imp.sum(1)/1000,tar.cell_import_MWh.to_numpy(),f'U{n}_cell_annual_imports')
    close(exp.sum(1)/1000,tar.cell_export_MWh.to_numpy(),f'U{n}_cell_annual_exports')
    close(imp.max(1),tar.cell_import_peak_kW.to_numpy(),f'U{n}_cell_peak_imports')
    agg['i'+str(n)]=imp.sum(0);agg['x'+str(n)]=exp.sum(0)
cells=gpd.read_file(C/'u4_pv_balance/substation_cells.gpkg',layer='inferred_cells').set_index('zone_id')
meta={}
for j,z in enumerate(ids):
    name=cells.loc[z,'substationalias']
    meta[z]=dict(file=f'profiles/c{j:03}.json.gz',label=str(name) if pd.notna(name) else z.replace('SUB:',''),
        pv_kWh=round(float(pvannual[j]),3),potential_kWh=round(float(potential[j]),3))
    write(meta[z]['file'],{k:arr(v[j]) for k,v in series.items()},True)
write('profiles/all.json.gz',{k:arr(v) for k,v in agg.items()},True)
write('profile_manifest.json',dict(start='2021-01-01T00:00:00Z',hours=8760,unit='kW',cells=meta,
    shape=shape.tolist(),all=dict(file='profiles/all.json.gz',pv_kWh=float(pvannual.sum()),potential_kWh=float(potential.sum())),
    solar_note='Annual rooftop yield distributed with the common south-facing hourly profile; no storage or curtailment.',
    footprint_count=len(data),roof_potential_GWh=float(pv.energy_kwh_yr.sum()/1e6)))
write('detail_checks.json',dict(checks=checks,source=str(S.name),
    inputs={str(p.relative_to(S)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [
        C/'baseline/group_hourly.npz',C/'packages/R2/group_hourly.npz',C/'baseline/annual_shapes.npz',
        C/'chapter4_infrastructure/all_scenario_cell_summary.csv',S/'data/inputs/spatial/pv_building_potential.gpkg']}))
print('Passed',len(checks),'reconciliations; published',len(ids),'cell profile bundles',flush=True)
