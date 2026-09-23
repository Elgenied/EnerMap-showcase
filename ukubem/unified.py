"""Canonical uncalibrated pipeline: paired simulations, hourly accounting and export.

No calibration imports, fitted parameters or legacy result paths are used here.
"""
from pathlib import Path
import os
import json
import hashlib
import time
from dataclasses import asdict
import numpy as np
import pandas as pd
from joblib import Parallel, delayed, parallel_config
from .geometry import UKGeometryAssumptions
from .accounting import account, family_masks, usage_for
from .runner import simulate_rows, schedule_for_row, aggregate_lsoa, mape
from . import heatpumps as HP, measures, pypsa_export as PX
from .profiles import epw_hourly
from .representatives import virtual_profiles, METHOD as REPRESENTATION_METHOD

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get('ENERMAP_RESULTS_DIR', str(ROOT / 'outputs/current'))).resolve()
BASE = OUT / 'baseline'
PACK = OUT / 'packages'
EPW = ROOT / 'data/inputs/weather/GBR_ENG_Farnborough.AP.037680_TMYx.2007-2021.epw'
SRC = ROOT / 'deps/pyBuildingEnergy/src'
WORKERS = min(28, max(1, (os.cpu_count() or 2) - 4))


def write_json(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, indent=2, default=lambda v: v.item() if isinstance(v, np.generic) else str(v)), encoding='utf-8')
    # OneDrive / a concurrent progress reader can briefly hold a Windows file handle.
    for attempt in range(40):
        try:
            temp.replace(path)
            break
        except PermissionError:
            if attempt == 39: raise
            time.sleep(.25)


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def make_representatives(inputs, n_size_classes=3):
    inp = inputs.copy()
    inp['size_class'] = inp.groupby('construction_sa').floor_area_final.transform(
        lambda s: pd.qcut(s.rank(method='first'), n_size_classes, labels=['S','M','L'][:n_size_classes]).astype(str)
        if len(s) >= 3*n_size_classes else 'M').astype(str)
    reps = []
    for (sa, sc), g in inp.groupby(['construction_sa','size_class'], observed=True):
        reps.append({'construction_sa':sa, 'size_class':sc, 'n':len(g),
            'dwelling_id':int((g.floor_area_final-g.floor_area_final.median()).abs().idxmin())})
    return inp, pd.DataFrame(reps)


def load_inputs():
    return make_representatives(pd.read_parquet(ROOT/'outputs/representatives/dwelling_inputs.parquet'))


def load_virtual_representatives():
    inputs, reps = load_inputs()
    rows, changes = virtual_profiles(inputs, reps)
    return rows, reps, changes


def group_heat(package='R0'):
    folder = BASE if package == 'R0' else PACK/package
    z = np.load(folder/'group_hourly.npz')
    reps = pd.read_csv(BASE/'representatives.csv')
    return {(r.construction_sa,r.size_class): z[str(r.dwelling_id)].astype(float) for r in reps.itertuples()}


def worker(did, row, realisation, assumptions):
    result = simulate_rows(row, str(EPW), str(SRC), assumptions, stochastic=realisation>=0,
        seed=42, year=2021, keep_hourly=True, timestep_minutes=60,
        stratum=realisation if realisation>=0 else None, n_strata=3 if realisation>=0 else 1)[0]
    q = result.pop('hourly_Q_HC_W', None)
    result.pop('hourly_T_op_C', None)
    if result['status'] != 'ok' or q is None:
        raise RuntimeError(f'{did}, draw {realisation}: {result["status"]}')
    assert result['air_balance_max_abs_W'] < 1e-4
    q = np.clip(q.astype(float), 0, None)
    assert q.shape == (8760,) and np.isfinite(q).all()
    result['realisation'] = realisation
    result['key'] = f'{did}_' + ('det' if realisation<0 else f'r{realisation}')
    return result, q


def simulate_package(package='R0'):
    inputs, reps = load_inputs()
    folder = BASE if package=='R0' else PACK/package
    (folder/'hourly').mkdir(parents=True, exist_ok=True)
    (folder/'checkpoints').mkdir(exist_ok=True)
    assumptions = UKGeometryAssumptions()
    spec = PX.R_PACKAGES[package]
    virtual, changes = virtual_profiles(inputs, reps)
    # Apply retrofit to the SAME virtual construction library used for baseline.
    rows, applied = measures.apply_package(virtual, spec['measures'], 'planning')
    if 'draught_proofing' in spec['measures']:
        assumptions = assumptions.with_(infil_scale=measures.ASSUMPTIONS['targets']['planning']['draught_ach_factor'])
    paths = [ROOT/'outputs/representatives/dwelling_inputs.parquet', EPW]
    for dirname in ['ukubem','uk_ubem_schedule_generator','parameters','deps/pyBuildingEnergy/src']:
        paths += [p for p in (ROOT/dirname).rglob('*') if p.is_file() and p.suffix in ['.py','.json','.xlsx'] and p.name != 'reporting.py']
    hashes = {str(p.relative_to(ROOT)):sha(p) for p in paths}
    manifest = {'sources':hashes,'package':package,'assumptions':asdict(assumptions),'seed':42,'year':2021,'draws':3,'calibration':False,
        'representation_method':REPRESENTATION_METHOD,
        'virtual_rows_hash':hashlib.sha256(pd.util.hash_pandas_object(rows,index=True).values.tobytes()).hexdigest()}
    fingerprint = hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    manifest['fingerprint'] = fingerprint
    target = folder/'manifest.json'
    if target.exists():
        assert json.loads(target.read_text())['fingerprint']==fingerprint, 'Source/config changed: archive this package before rerunning'
    else: write_json(target, manifest)
    if package=='R0':
        reps.to_csv(BASE/'representatives.csv', index=False)
        inputs[['construction_sa','size_class','floor_area_final']].to_parquet(BASE/'stock_assignment.parquet')
        changes.to_json(BASE/'virtual_input_changes.json',orient='records',indent=2)
    rows.to_parquet(folder/'virtual_representative_inputs.parquet')
    applied.to_parquet(folder/'representative_applied_measures.parquet')
    tasks=[]; results=[]
    draws=[-1,0,1,2] if package=='R0' else [0,1,2]
    for did in reps.dwelling_id:
        for k in draws:
            key=f'{did}_'+('det' if k<0 else f'r{k}')
            ck=folder/'checkpoints'/f'{key}.json'
            if ck.exists() and (folder/'hourly'/f'{key}.npz').exists(): results.append(json.loads(ck.read_text()))
            else: tasks.append((int(did),rows.loc[[did]],k,assumptions))
    start=time.time()
    print(f'{package}: {len(tasks)} fresh annual simulations; {WORKERS} workers',flush=True)
    with parallel_config(backend='loky',inner_max_num_threads=1):
        for result,q in Parallel(n_jobs=WORKERS,batch_size=1,max_nbytes=None,return_as='generator_unordered')(delayed(worker)(*task) for task in tasks):
            np.savez_compressed(folder/'hourly'/f'{result["key"]}.npz',Q_H_W=q.astype(np.float32))
            write_json(folder/'checkpoints'/f'{result["key"]}.json',result)
            results.append(result)
            write_json(OUT/'progress.json',{'stage':package,'done':len(results),'total':len(reps)*len(draws),'elapsed_minutes':(time.time()-start)/60})
            if len(results)%12==0: print(f'{package}: {len(results)}/{len(reps)*len(draws)} complete ({(time.time()-start)/60:.1f} min)',flush=True)
    result=pd.DataFrame(results).sort_values(['dwelling_id','realisation'])
    assert len(result)==len(reps)*len(draws) and result.status.eq('ok').all()
    result.to_parquet(folder/'simulation_summaries.parquet',index=False)
    groups={}; intensity=[]
    for r in reps.itertuples():
        A=float(rows.loc[r.dwelling_id,'floor_area_final'])
        q=np.mean([np.load(folder/'hourly'/f'{r.dwelling_id}_r{k}.npz')['Q_H_W'].astype(float) for k in range(3)],axis=0)/A
        groups[str(r.dwelling_id)]=q
        intensity.append({'dwelling_id':r.dwelling_id,'construction_sa':r.construction_sa,'size_class':r.size_class,
            'representative_area_m2':A,'Q_H_kWh_m2':q.sum()/1000.})
    np.savez_compressed(folder/'group_hourly.npz',**groups)
    pd.DataFrame(intensity).to_csv(folder/'archetype_intensities.csv',index=False)
    write_json(folder/'complete.json',{'completed':True,'simulations':len(result),'fingerprint':fingerprint})
    return group_heat(package)


def shapes():
    """Preserve household draws; normalize only the non-heating electricity shape.

    Annual totals remain BREDEM. DHW uses the configured shape shared with HP accounting.
    """
    path=BASE/'annual_shapes.npz'
    if path.exists():
        z=np.load(path); reps=pd.read_csv(BASE/'representatives.csv')
        return {(r.construction_sa,r.size_class):z[str(r.dwelling_id)] for r in reps.itertuples()}
    inp,reps,_=load_virtual_representatives(); groups={}
    for r in reps.itertuples():
        ss=[]
        for k in range(3):
            _,sch=schedule_for_row(inp.loc[r.dwelling_id],r.dwelling_id,UKGeometryAssumptions(),True,42,2021,60,k,3)
            v=np.asarray(sch['elec_w'],float)
            ss.append(v/v.sum() if v.sum()>0 else np.full(8760,1/8760))
        groups[str(r.dwelling_id)]=np.mean(ss,axis=0)
    np.savez_compressed(path,**groups)
    return {(r.construction_sa,r.size_class):groups[str(r.dwelling_id)] for r in reps.itertuples()}


def stock_accounting(inputs, group_profiles, converted=None):
    """Area expansion and one annual/hourly energy account for every end use."""
    t=epw_hourly(EPW).T_out_C.to_numpy()
    eshapes=shapes(); water=HP.annual_dhw_shape()
    masks=family_masks(inputs.systems_fam.to_numpy(),inputs.gas_connected.fillna(False).to_numpy(bool))
    area=inputs.floor_area_final.to_numpy(float); usage=usage_for(area,masks)
    conv=pd.Series(False,index=inputs.index) if converted is None else converted.reindex(inputs.index).fillna(False).astype(bool)
    qyear=np.zeros(len(inputs)); hpannual=np.zeros(len(inputs))
    hourly=pd.DataFrame(0.,index=pd.date_range('2021-01-01',periods=8760,freq='h'),columns=[
        'space_heat_kWh','dhw_useful_kWh','nonheating_electricity_kWh','heating_electricity_kWh','pou_electricity_kWh','hp_electricity_kWh'])
    for g, positions in inputs.groupby(['construction_sa','size_class'],observed=True).indices.items():
        ii=np.asarray(positions); q=np.asarray(group_profiles[g],float)/1000.
        qhp=HP.heat_pump_hourly(q*1000,t)/1000. if conv.iloc[ii].any() else q
        for is_converted in [False,True]:
            jj=ii[conv.iloc[ii].to_numpy()==is_converted]
            if not len(jj):continue
            h=qhp if is_converted else q
            qyear[jj]=h.sum()*area[jj]
            hourly.space_heat_kWh += h*area[jj].sum()
            hj=jj[masks['hp'][jj]]; dj=jj[masks['direct'][jj]]
            if len(hj):
                eh,ew=HP.hourly_electricity(h*area[hj].sum(),usage['dhw_system_kWh'][hj].sum(),t)
                hourly.hp_electricity_kWh += eh+ew
                hourly.heating_electricity_kWh += eh+ew
                hpannual[hj]=area[hj]*np.sum(h/HP.cop_hourly(t,kind='ASHP',emitter='radiator'))+usage['dhw_system_kWh'][hj]*np.sum(water/HP.cop_hourly(t,kind='ASHP',emitter='dhw'))
            if len(dj): hourly.heating_electricity_kWh += h*area[dj].sum()+water*usage['dhw_system_kWh'][dj].sum()
        hourly.dhw_useful_kWh += water*usage['dhw_useful_kWh'][ii].sum()
        hourly.nonheating_electricity_kWh += eshapes[g]*usage['elec_nonheat_kWh'][ii].sum()
        hourly.pou_electricity_kWh += water*usage['dhw_pou_elec_kWh'][ii].sum()
    acc=account(qyear,area,masks,usage=usage,hp_electric_kWh=hpannual)
    stock=pd.DataFrame({**acc,'fuel':masks['fuel'],'systems_fam':inputs.systems_fam.to_numpy(),
        'floor_area_final':area,'Q_H_kWh_m2':qyear/area},index=inputs.index)
    hourly['delivered_electricity_kWh']=hourly.nonheating_electricity_kWh+hourly.heating_electricity_kWh+hourly.pou_electricity_kWh
    for hc,sc in [('space_heat_kWh','Q_H_kWh'),('delivered_electricity_kWh','delivered_elec_kWh'),('nonheating_electricity_kWh','appliances_kWh')]:
        np.testing.assert_allclose(hourly[hc].sum(),stock[sc].sum(),rtol=1e-10)
    assert np.isfinite(stock.select_dtypes('number')).all().all()
    return stock,hourly


def baseline():
    profiles=simulate_package('R0')
    inputs,_=load_inputs(); stock,hourly=stock_accounting(inputs,profiles)
    stock.to_parquet(BASE/'stock_demand.parquet')
    hourly.rename_axis('time').to_csv(BASE/'stock_hourly.csv')
    labels=pd.read_parquet(ROOT/'data/derived/stock_labels.parquet')
    bench=pd.read_csv(ROOT/'data/derived/benchmark_lsoa_2021.csv')
    validation=aggregate_lsoa(stock.reset_index(),labels,bench).join(bench.set_index('LSOA')[['lsoa_name']])
    validation.to_csv(BASE/'lsoa_validation.csv')
    write_json(BASE/'metrics.json',mape(validation))
    totals={c:float(stock[c].sum()/1e6) for c in ['Q_H_kWh','delivered_gas_kWh','delivered_elec_kWh','delivered_other_kWh']}
    write_json(BASE/'summary.json',{'completed':True,'calibration':False,'stock_dwellings':len(stock),
        'representation_method':REPRESENTATION_METHOD,
        'simulations':408,'stochastic_simulations':306,'existing_heat_pumps':int(stock.systems_fam.eq('S.HeatPump').sum()),
        'totals_GWh':totals,'metrics':mape(validation),'electricity_peak_MW':float(hourly.delivered_electricity_kWh.max()/1000),
        'peak_is_modelled_not_validated':True,'annual_hourly_consistency_passed':True})
    print('BASELINE COMPLETE',totals,flush=True)
    return stock


def packages():
    return {p:simulate_package(p) for p in ['R1','R2']}


def scenarios():
    for p in ['R1','R2']: simulate_package(p)
    inputs,_=load_inputs(); folder=OUT/'scenarios'; folder.mkdir(parents=True,exist_ok=True)
    baseline_stock=pd.read_parquet(BASE/'stock_demand.parquet'); rows=[]
    planning=inputs.index[inputs.in_study_area.astype(bool)]
    a0=measures.envelope_areas(inputs.loc[planning])
    fabric_inputs,_=measures.apply_package(inputs,PX.R_PACKAGES['R2']['measures'],'planning')
    af=measures.envelope_areas(fabric_inputs.loc[planning],UKGeometryAssumptions().with_(infil_scale=measures.ASSUMPTIONS['targets']['planning']['draught_ach_factor']))
    assert a0.notna().all().all() and af.notna().all().all()
    social_path=ROOT/'data/inputs/social/lsoa_social_context.csv'
    social=pd.read_csv(social_path).set_index('LSOA')[['imd_decile','fuel_poor_pct']] if social_path.exists() else None
    results={};ward_rows=[]
    for name,package in measures.PACKAGES.items():
        inp,applied=measures.apply_package(inputs,package,'planning')
        fabric=any(m in package for m in PX.R_PACKAGES['R2']['measures'])
        converted=applied['ashp'] if 'ashp' in applied else pd.Series(False,index=inp.index)
        st,hr=stock_accounting(inp,group_heat('R2' if fabric else 'R0'),converted)
        if name=='U0_baseline':pd.testing.assert_frame_equal(st,baseline_stock)
        pv=measures.pv_terms(inp,applied['pv'])
        bc=measures.bills_and_carbon(st,pv)
        result=st.join(bc).join(pv).join(inp[['LSOA','ward','toid','construction_sa','in_study_area']])
        # Wet-system installation cost depends on the original heating system,
        # not the already-converted S.HeatPump label.
        costs=measures.measure_costs(inputs.loc[planning],applied.loc[planning],a0,
            (af.HTC_fabric_W_K+af.H_ve_W_K) if fabric else (a0.HTC_fabric_W_K+a0.H_ve_W_K))
        incentives=measures.incentives(inp.loc[planning],costs,social)
        result=result.join(costs).join(incentives).join(applied.add_prefix('applied_'))
        results[name]=result
        result.to_parquet(folder/f'{name}.parquet')
        hr.rename_axis('time').to_csv(folder/f'{name}_hourly.csv')
        for scope,ids in [('borough',inp.index),('planning_wards',inp.index[inp.in_study_area.astype(bool)])]:
            x=result.loc[ids]
            rows.append({'scenario':name,'scope':scope,'dwellings':len(x),**{c.replace('_kWh','_GWh'):x[c].sum()/1e6 for c in ['Q_H_kWh','delivered_gas_kWh','delivered_elec_kWh','delivered_other_kWh','elec_net_kWh']},
                'carbon_kt':x.carbon_kg.sum()/1e6,'bills_MGBP':x.bill_GBP.sum()/1e6})
        print(name,'complete',flush=True)
        for ward,x in result.loc[planning].groupby('ward'):
            ward_rows.append({'scenario':name,'ward':ward,'dwellings':len(x),'gas_GWh':x.delivered_gas_kWh.sum()/1e6,
                'electricity_GWh':x.delivered_elec_kWh.sum()/1e6,'net_electricity_GWh':x.elec_net_kWh.sum()/1e6,
                'carbon_kt':x.carbon_kg.sum()/1e6,'bills_MGBP':x.bill_GBP.sum()/1e6,
                'capex_gross_MGBP':x.capex_gross_GBP.sum()/1e6,'incentives_MGBP':x.incentives_GBP.sum()/1e6})
    pd.DataFrame(rows).to_csv(folder/'scenario_summary.csv',index=False)
    pd.DataFrame(ward_rows).to_csv(folder/'planning_ward_summary.csv',index=False)
    base=results['U0_baseline'].loc[planning];deep=results['U5_envelope_elec_pv'].loc[planning]
    econ=measures.economics(base,deep,deep,deep)
    econ['group']=measures.rank_groups(econ);econ['HTD']=measures.hard_to_decarbonise(inputs.loc[planning])
    econ=econ.join(inputs.loc[planning,['ward','LSOA','toid','tenure']])
    econ.to_csv(folder/'planning_ward_economics_U5.csv')
    write_json(folder/'manifest.json',{'calibration':False,'hourly_COP':True,'space_heat_uplift':'Existing configuration applied only to newly converted HP dwellings',
        'economic_assumptions':'Existing project prices/carbon factors retained, not current quotes; retrofit costs and policy incentives only for original planning-ward scope',
        'scope':'borough plus separately labelled planning-ward subtotal'})


def interface():
    import config as cfg
    folder=OUT/'pypsa_interface'; folder.mkdir(parents=True,exist_ok=True)
    inputs,_=load_inputs()
    profiles={p:group_heat(p) for p in PX.R_PACKAGES}
    zones=PX.electricity_zones(inputs,cfg); elig=PX.eligibility_class(inputs,'planning')
    cid,clusters=PX.build_clusters(inputs,zones,elig,resolution='planning',min_dwellings=10)
    bmap=PX.building_cluster_map(inputs,cid,zones,elig)
    areas=measures.envelope_areas(inputs)
    assert areas.notna().all().all(), 'Missing envelope quantities must not be silently exported'
    options,msets,tsets=PX.retrofit_options(inputs,cid,areas,'planning')
    qty=PX.retrofit_quantities(inputs,cid,areas,'planning')
    for name,frame in [('building_cluster_map',bmap),('cluster_summary',clusters),('retrofit_options',options),('retrofit_quantities',qty)]:frame.to_csv(folder/f'{name}.csv',index=False)
    write_json(folder/'measure_sets.json',msets);write_json(folder/'thermal_parameter_sets.json',tsets)
    usage=usage_for(inputs.floor_area_final.to_numpy(float),family_masks(inputs.systems_fam,inputs.gas_connected.fillna(False)))
    dsh={g:HP.annual_dhw_shape() for g in profiles['R0']}; esh=shapes()
    index=pd.date_range('2021-01-01',periods=8760,freq='h',tz='UTC')
    case={'case_id':'GF2021_TMY_UNCAL_MEDIAN3_HOURLY_COP','year':2021,'description':'Uncalibrated ISO 52016; typical cluster construction at three exact median areas; three paired stochastic draws per size; BREDEM annual usage; hourly ASHP COP; borough scope; no meter fitting'}
    lib,weights,totals=PX.write_demand_profiles(folder/'demand_profiles.parquet',inputs,cid,profiles,dsh,esh,usage,case['case_id'],index,chunk_clusters=32)
    lib.to_parquet(folder/'demand_profile_library.parquet',index=False)
    weights.to_csv(folder/'cluster_profile_weights.csv',index=False)
    totals.to_csv(folder/'cluster_annual_totals.csv',index=False)
    pd.DataFrame([case]).to_csv(folder/'cases.csv',index=False)
    baseline_stock=pd.read_parquet(BASE/'stock_demand.parquet')
    expected=baseline_stock.groupby(cid).Q_H_kWh.sum()
    actual=totals[totals.package_id=='R0'].set_index('cluster_id').space_heat_kWh.reindex(expected.index)
    np.testing.assert_allclose(actual,expected,rtol=1e-10,atol=1e-6)
    for col,values in [('hot_water_kWh',usage['dhw_useful_kWh']),('non_heating_elec_kWh',usage['elec_nonheat_kWh'])]:
        np.testing.assert_allclose(totals.loc[totals.package_id=='R0',col].sum(),sum(values),rtol=1e-10)
    t=epw_hourly(EPW).T_out_C.to_numpy()
    pd.DataFrame({'timestamp_utc':index,'T_out_C':t,'COP_space_heat':HP.cop_hourly(t,kind='ASHP',emitter='radiator'),
        'COP_dhw':HP.cop_hourly(t,kind='ASHP',emitter='dhw')}).to_csv(folder/'heat_pump_cop_hourly.csv',index=False)
    hp_lib=lib.copy()
    for _,idxs in hp_lib.groupby(['construction_sa','size_class','package_id'],sort=False).groups.items():
        hp_lib.loc[idxs,'space_heat_w_per_m2']=HP.heat_pump_hourly(hp_lib.loc[idxs,'space_heat_w_per_m2'].to_numpy(),t)
    hp_lib.to_parquet(folder/'demand_profile_library_heat_pump.parquet',index=False)
    # Exact DHW splits and annual weights permit reconstruction of the baseline fuel boundary.
    dw=pd.DataFrame({'cluster_id':cid,'construction_sa':inputs.construction_sa,'size_class':inputs.size_class,'systems_fam':inputs.systems_fam,
        'area_m2':inputs.floor_area_final,'dhw_useful_kWh':usage['dhw_useful_kWh'],'dhw_system_kWh':usage['dhw_system_kWh'],
        'dhw_pou_elec_kWh':usage['dhw_pou_elec_kWh'],'nonheating_elec_kWh':usage['elec_nonheat_kWh'],'cooking_gas_kWh':usage['cooking_gas_kWh']})
    dw.groupby(['cluster_id','construction_sa','size_class','systems_fam']).sum().reset_index().to_csv(folder/'cluster_end_use_weights.csv',index=False)
    baseline_stock.groupby(cid)[['Q_H_kWh','delivered_gas_kWh','delivered_elec_kWh','delivered_other_kWh']].sum().to_csv(folder/'cluster_baseline_fuel_totals.csv')
    from . import flexibility as FX
    flex=FX.dwelling_flexibility(pd.DataFrame({'floor_area_final':inputs.floor_area_final,'HTC_W_K':areas.HTC_fabric_W_K+areas.H_ve_W_K}),t_ref=5.)
    flex['cluster_id']=cid
    FX.aggregate(flex,'cluster_id').reset_index().to_csv(folder/'cluster_flexibility.csv',index=False)
    PX.interface_readme(folder,{'dwellings':len(inputs),'clusters':len(clusters),'buildings':bmap.building_id.nunique(),'zones':clusters.electricity_zone_id.nunique()},case)
    readme=folder/'README_pypsa_interface.md'
    text=readme.read_text(encoding='utf-8').replace('weather, occupancy and calibration assumptions','weather, occupancy and uncalibrated assumptions').replace('under the calibrated\nphysics','under the uncalibrated\nphysics').replace('hourly shape from the CHAP hot-water draws of the household','configured normalized DHW hourly shape')
    text+='''\n\n## Current accounting contract\n\nThis export covers the entire borough. No calibration, spatial refit or annual level matching is used. `heat_pump_cop_hourly.csv` supplies the same radiator and 50°C DHW COP used in the baseline. Compute HP electricity hour by hour and sum; do not rescale to prescribed SCOPs. All existing aggregate HPs use the ASHP curve.\n\n`cluster_end_use_weights.csv` retains annual tap heat, system-side DHW including losses, electric-shower electricity, non-heating electricity and gas cooking by construction/size/system family. This permits the baseline end-use split to be reconstructed. The useful-DHW load includes the tap heat served by electric showers; do not also add the shower vector without removing that heat from the main DHW service. `cluster_baseline_fuel_totals.csv` is the annual delivered-energy reconciliation target.\n\n`demand_profile_library_heat_pump.parquet` is an optional scenario library for newly converted homes, carrying the configured Watson reshaping/uplift. Existing HP baseline heat remains the unchanged UBEM profile. Never add the baseline and conversion libraries together for the same dwellings. Baseline DHW uses the configured Watson daily shape normalized to the BREDEM annual total. Non-heating electricity uses normalized household shapes with BREDEM annual totals.\n\nThe exported timeline is a fixed 8760-hour model clock (no daylight-saving adjustment), represented as UTC for interface compatibility. R0/R1/R2 describe alternatives for the same homes and cannot be summed as independent stock. COP excludes defrost, backup heating and part-load/cycling effects. Peaks and flexibility are modelled, not hourly validated.\n'''
    readme.write_text(text,encoding='utf-8')
    # Export the exact physical inputs behind each alternative profile library.
    for package in PX.R_PACKAGES:
        source=BASE if package=='R0' else PACK/package
        pd.read_parquet(source/'virtual_representative_inputs.parquet').to_parquet(folder/f'virtual_representatives_{package}.parquet')
    text+='''\n\n## Construction representation\n\nR0/R1/R2 use the same baseline cluster-median construction profiles at exact median S/M/L floor areas. Each profile averages three household draws; each actual dwelling retains its original floor area for expansion. The source dwelling ID is a traceable template ID, not a claim that virtual inputs are its measured attributes. `virtual_representatives_R0/R1/R2.parquet` documents the exact simulated inputs. The retrofit package is applied to the virtual baseline before simulation; the construction profiles are not recomputed from retrofitted stock.\n\nPer-dwelling retrofit eligibility, quantities, investment costs and flexibility retain original completed-stock attributes. They are individual-stock estimates, whereas demand savings are archetype-tier approximations; eligibility differences within a construction/size group are not separately simulated. Roof exposure is retained from each size template and roof summaries are exposure-conditioned; within-band exposure differences remain unresolved.\n'''
    readme.write_text(text,encoding='utf-8')
    write_json(folder/'checks.json',{'completed':True,'dwellings':len(inputs),'clusters':len(clusters),
        'baseline_heat_reconciled':True,'DHW_and_nonheating_reconciled':True,'thermal_profile_runs':918,
        'representation_method':REPRESENTATION_METHOD,
        'newly_converted_hp_library':'Includes configured reshaping/uplift; do not apply to existing HPs again',
        'schema':'Useful DHW excludes losses; use cluster_end_use_weights for baseline system/POU split. COP is a conversion efficiency, never a load.'})
    print('PYPSA INTERFACE COMPLETE',len(clusters),'clusters',flush=True)
