"""End-to-end provenance and old/new comparison for the median-profile rollout."""
from pathlib import Path
import sys,json
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from ukubem import unified as U


def main():
    current=U.OUT
    archive=ROOT/'_archive/20260923_before_median_profile_promotion'
    old=archive/'outputs/current' if (archive/'outputs/current').exists() else ROOT/'outputs/current'
    if current.resolve()==old.resolve():
        raise ValueError('Old/new comparison requires a preserved pre-promotion snapshot')
    fresh=json.loads((current/'baseline/summary.json').read_text())
    prior=json.loads((old/'baseline/summary.json').read_text())
    complete=json.loads((current/'completion.json').read_text())
    assert complete['completed'] and complete['representation_method']==U.REPRESENTATION_METHOD
    sources={}
    for package in ['R0','R1','R2']:
        folder=current/('baseline' if package=='R0' else f'packages/{package}')
        m=json.loads((folder/'manifest.json').read_text())
        assert m['representation_method']==U.REPRESENTATION_METHOD and not m['calibration']
        for rel,expected in m['sources'].items():assert U.sha(ROOT/rel)==expected,rel
        sources[package]=m['fingerprint']
        a=pd.read_parquet(folder/'virtual_representative_inputs.parquet')
        b=pd.read_parquet(current/f'pypsa_interface/virtual_representatives_{package}.parquet')
        pd.testing.assert_frame_equal(a,b)
    assert sources==complete['package_fingerprints']
    for name in ['checks/accounting.json','checks/scenarios.json','checks/physics/PASSED.json','pypsa_interface/pypsa_import_check.json']:
        assert json.loads((current/name).read_text())['passed'],name
    stock=pd.read_parquet(current/'baseline/stock_demand.parquet')
    expected=pd.read_parquet(ROOT/'outputs/experiments/20260923_median_profile_three_areas/stock_demand.parquet')
    pd.testing.assert_frame_equal(stock,expected)
    oldstock=pd.read_parquet(old/'baseline/stock_demand.parquet')
    assert stock.index.equals(oldstock.index)
    for field in ['floor_area_final','systems_fam','dhw_kWh','appliances_kWh','dhw_pou_elec_kWh','cooking_gas_kWh']:
        pd.testing.assert_series_equal(stock[field],oldstock[field])
    # Complete library comparison, not just a headline annual-total match.
    lib=np.load(current/'baseline/group_hourly.npz')
    reference=np.load(ROOT/'outputs/experiments/20260923_median_profile_three_areas/group_hourly.npz')
    assert set(lib.files)==set(reference.files)
    for did in lib.files:np.testing.assert_allclose(lib[did],reference[did],rtol=0,atol=0)
    rows=[]
    before=pd.read_csv(old/'scenarios/scenario_summary.csv').set_index(['scenario','scope'])
    after=pd.read_csv(current/'scenarios/scenario_summary.csv').set_index(['scenario','scope'])
    for ix,new in after.iterrows():
        previous=before.loc[ix]
        rows.append(dict(scenario=ix[0],scope=ix[1],**{f'{c}_{tag}':float(row[c]) for c in ['Q_H_GWh','delivered_gas_GWh','delivered_elec_GWh','carbon_kt'] for tag,row in [('before',previous),('after',new)]}))
    comparison=dict(passed=True,representation_method=U.REPRESENTATION_METHOD,old_results=str(old),
        baseline_before=prior,baseline_after=fresh,package_fingerprints=sources,scenario_comparison=rows,
        experiment_hourly_and_annual_reproduced=True,raw_stock_preserved=True,pypsa_representative_inputs_reconciled=True,
        optimization_run=False)
    U.write_json(current/'checks/representative_promotion.json',comparison)
    changes='\n'.join(f"| {label} | {prior['metrics'][key]:.3f} | {fresh['metrics'][key]:.3f} |" for key,label in [('gas_WAPE_pct','Gas WAPE (%)'),('elec_WAPE_pct','Electricity WAPE (%)')])
    table='\n'.join(f"| {p} | {prior['totals_GWh'][p]:.3f} | {fresh['totals_GWh'][p]:.3f} |" for p in ['Q_H_kWh','delivered_gas_kWh','delivered_elec_kWh'])
    (current/'REPRESENTATIVE_METHOD_UPDATE.md').write_text(f'''# Current representation: typical construction at three median areas

The full main pipeline now uses the approved virtual construction profiles: whole-cluster numerical medians and modal categories, evaluated at exact median floor areas of the existing small/medium/large bands. Original template geometry descriptors and exposure are retained, and the engine recalculates surfaces. Roof summaries are conditional on exposure. Three household draws per size are retained.

The original dwelling input table, cluster assignments, individual areas/heating systems, annual usage assumptions and household model were not overwritten. This is not calibration. Previously inspected benchmarks are not independent validation.

All 408 baseline/reference simulations and 612 retrofit-package simulations were rerun in the main pipeline. R1/R2 are applied to the virtual baseline. Scenario outputs, demand/validation maps, executed notebook reports and PyPSA exports have been regenerated. Full hourly baseline heat profiles and annual stock results reproduce the approved experiment exactly. Non-heating electricity shapes are regenerated from the virtual-size household profiles; annual totals are unchanged.

| Metric | Previous current run | New current run |
|---|---:|---:|
{changes}

| Quantity | Previous GWh/year | New GWh/year |
|---|---:|---:|
{table}

All physics, annual/hourly accounting, scenario and full PyPSA parquet checks passed. An 8,760-hour PyPSA example network with three loads and two COP links was imported/exported successfully; no network optimization was run.

## Remaining approximation

Virtual medians/modes do not preserve all construction correlations. Each size template's roof exposure still approximates other members of its size band. Per-dwelling retrofit eligibility, costs and flexibility retain individual stock attributes, while simulated savings are shared archetype-tier estimates. These quantities are not independently validated by area-level meter agreement.

## Locations

- `outputs/current/INDEX.html`: results, plots and reports.
- `outputs/current/baseline/virtual_representative_inputs.parquet`: exact baseline virtual inputs.
- `outputs/current/packages/R1,R2/virtual_representative_inputs.parquet`: exact retrofit inputs.
- `outputs/current/baseline/stock_demand.parquet`: per-dwelling heating intensity, annual heat and delivered energy.
- `outputs/current/pypsa_interface/`: all regenerated interface files and successful import checks.
- `_archive/20260923_before_median_profile_promotion/`: previous code, notebooks and `outputs/current` results.

The source notebooks 05, 06, 07, 09, 10, 11 and 12 were executed; preprocessing/cluster notebooks and source stock were retained. The Word manuscripts and remote GitHub showcase were not changed by this run.
''',encoding='utf-8')
    print('PASS: approved experiment reproduced; all packages and interface share the new representation; original stock preserved')


if __name__=='__main__':main()
