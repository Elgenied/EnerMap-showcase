"""Verify persisted scenario energy boundaries and the unchanged baseline."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from ukubem.unified import OUT, BASE, PACK, write_json, REPRESENTATION_METHOD
from ukubem import measures, pypsa_export as PX

def main():
    virtual=pd.read_parquet(BASE/'virtual_representative_inputs.parquet')
    for package in ['R1','R2']:
        expected,flags=measures.apply_package(virtual,PX.R_PACKAGES[package]['measures'],'planning')
        actual=pd.read_parquet(PACK/package/'virtual_representative_inputs.parquet')
        pd.testing.assert_frame_equal(actual,expected)
        pd.testing.assert_frame_equal(pd.read_parquet(PACK/package/'representative_applied_measures.parquet'),flags)
    folder=OUT/'scenarios'
    summary=pd.read_csv(folder/'scenario_summary.csv')
    base=pd.read_parquet(BASE/'stock_demand.parquet')
    results=[]
    for name in summary.scenario.unique():
        stock=pd.read_parquet(folder/f'{name}.parquet')
        hourly=pd.read_csv(folder/f'{name}_hourly.csv')
        assert stock.index.equals(base.index) and len(hourly)==8760
        for annual,hour in [('Q_H_kWh','space_heat_kWh'),('delivered_elec_kWh','delivered_electricity_kWh'),('elec_heat_kWh','heating_electricity_kWh')]:
            assert stock[annual].ge(0).all()
            np.testing.assert_allclose(stock[annual].sum(),hourly[hour].sum(),rtol=1e-10)
        np.testing.assert_allclose(stock.delivered_elec_kWh,stock.appliances_kWh+stock.elec_heat_kWh+stock.dhw_pou_elec_kWh,rtol=1e-10)
        assert (stock.elec_net_kWh<=stock.delivered_elec_kWh+1e-8).all()
        assert stock.elec_net_kWh.ge(0).all()
        for scope,mask in [('borough',pd.Series(True,index=stock.index)),('planning_wards',stock.in_study_area.astype(bool))]:
            row=summary.loc[summary.scenario.eq(name)&summary.scope.eq(scope)].iloc[0]
            assert row.dwellings==mask.sum()
            for col in ['Q_H_kWh','delivered_gas_kWh','delivered_elec_kWh','delivered_other_kWh','elec_net_kWh']:
                np.testing.assert_allclose(stock.loc[mask,col].sum()/1e6,row[col.replace('_kWh','_GWh')],rtol=1e-10)
        if name=='U0_baseline':pd.testing.assert_frame_equal(stock[base.columns],base)
        results.append({'scenario':name,'passed':True,'dwellings':len(stock),'hours':len(hourly)})
    assert len(results)==6
    write_json(OUT/'checks/scenarios.json',{'passed':True,'checks':results,'representation_method':REPRESENTATION_METHOD,
        'retrofit_applied_to_virtual_baseline':True,'boundary':'Hourly gross electricity reconciles before annual PV self-consumption offsets.'})
    print('PASS: six scenarios, baseline preservation and annual/hourly reconciliation')

if __name__=='__main__':main()
