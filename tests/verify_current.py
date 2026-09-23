"""Non-fitting regression checks for the canonical annual/hourly pipeline."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
import numpy as np
import pandas as pd
from ukubem import unified as U, heatpumps as HP
from ukubem.accounting import account,family_masks


def main():
    stock=pd.read_parquet(U.BASE/'stock_demand.parquet')
    hourly=pd.read_csv(U.BASE/'stock_hourly.csv')
    inputs,reps=U.load_inputs()
    tests=[]
    def check(name,condition):
        assert bool(condition),name
        tests.append({'test':name,'passed':True})
    check('All stock retained',len(stock)==len(inputs)==57300 and stock.index.is_unique)
    check('Representative membership retained',len(reps)==102 and reps.n.sum()==len(stock))
    virtual,_,changes=U.load_virtual_representatives()
    saved=pd.read_parquet(U.BASE/'virtual_representative_inputs.parquet')
    pd.testing.assert_frame_equal(saved[virtual.columns],virtual)
    check('Saved thermal inputs match virtual construction profiles',True)
    expected=inputs.groupby(['construction_sa','size_class']).floor_area_final.median()
    for r in reps.itertuples():
        check(f'Exact size-band median area {r.construction_sa}/{r.size_class}',
            np.isclose(virtual.loc[r.dwelling_id,'floor_area_final'],expected.loc[(r.construction_sa,r.size_class)]))
    check('Unexposed representative ceilings retain zero roof U',
        virtual.loc[~virtual.roof_exposed.astype(bool),'roof_U'].eq(0).all())
    experimental=ROOT/'outputs/experiments/20260923_median_profile_three_areas/stock_demand.parquet'
    if experimental.exists():
        reference=pd.read_parquet(experimental)
        pd.testing.assert_frame_equal(stock,reference)
        check('Promoted baseline reproduces approved experiment stock results',True)
    for h,s in [('space_heat_kWh','Q_H_kWh'),('delivered_electricity_kWh','delivered_elec_kWh'),('heating_electricity_kWh','elec_heat_kWh')]:
        check('Hourly annual agreement: '+s,np.isclose(hourly[h].sum(),stock[s].sum(),rtol=1e-10))
    check('Hourly HP sum',np.isclose(hourly.hp_electricity_kWh.sum(),stock.loc[stock.systems_fam.eq('S.HeatPump'),'elec_heat_kWh'].sum(),rtol=1e-10))
    eh,ew=HP.hourly_electricity(np.ones(8760),1000,np.zeros(8760))
    check('Constant-temperature space heat COP',np.allclose(eh,1/3.28))
    check('Constant-temperature DHW COP',np.isclose(ew.sum(),1000/2.83))
    rejected=False
    try:account([1000],[90],family_masks(['S.HeatPump']))
    except ValueError:rejected=True
    check('No accidental fixed-SPF fallback',rejected)
    before=ROOT/'_archive/20260918_before_unified'
    for folder in ['data','outputs/archetypes','outputs/representatives','parameters','uk_ubem_schedule_generator']:
        files=[p for p in (before/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
        check('Preserved '+folder,all((ROOT/p.relative_to(before)).exists() and U.sha(p)==U.sha(ROOT/p.relative_to(before)) for p in files))
    new=pd.read_parquet(U.BASE/'simulation_summaries.parquet')
    original=before/'outputs/ubem/05_representatives_stochastic.parquet'
    if original.exists():
        prior=pd.read_parquet(original)
        fields=['household_archetype','heating_pattern','people','setpoint_c','heating_share_of_hours']
        a=new[new.realisation>=0].set_index(['dwelling_id','realisation'])[fields].sort_index()
        b=prior.set_index(['dwelling_id','realisation'])[fields].sort_index()
        pd.testing.assert_frame_equal(a,b,check_dtype=False,check_exact=True)
        check('Household draws unchanged (values; deterministic NaNs promote people to float)',True)
    U.write_json(U.OUT/'checks/accounting.json',{'passed':True,'checks':tests})
    print('PASSED',len(tests),'annual/hourly and preservation checks')


if __name__=='__main__':main()
