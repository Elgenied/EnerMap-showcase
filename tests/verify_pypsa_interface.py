"""Check stored parquet totals and import one exported cluster into a PyPSA network.

This is an interface/unit smoke test, not a capacity-expansion optimisation.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pypsa
from ukubem.unified import OUT,write_json


def main():
    folder=OUT/'pypsa_interface'
    totals=pd.read_csv(folder/'cluster_annual_totals.csv')
    expected=totals.groupby('package_id')[['space_heat_kWh','hot_water_kWh','non_heating_elec_kWh']].sum()
    observed={p:np.zeros(3) for p in expected.index}
    n=0
    for batch in pq.ParquetFile(folder/'demand_profiles.parquet').iter_batches(batch_size=262144,columns=['package_id','space_heat_kw','hot_water_heat_kw','non_heating_electricity_kw']):
        d=batch.to_pandas();n+=len(d)
        assert np.isfinite(d.iloc[:,1:].to_numpy()).all() and (d.iloc[:,1:]>=0).all().all()
        for p,g in d.groupby('package_id'):observed[p]+=g.iloc[:,1:].to_numpy(dtype=float).sum(axis=0)
    for p in expected.index:np.testing.assert_allclose(observed[p],expected.loc[p].to_numpy(),rtol=1e-7)
    assert n==len(totals)*8760
    cluster=totals[totals.package_id.eq('R0')].sort_values('space_heat_kWh').iloc[-1].cluster_id
    frame=pd.read_parquet(folder/'demand_profiles.parquet',filters=[('cluster_id','==',cluster),('package_id','==','R0')]).sort_values('timestamp_utc')
    assert len(frame)==8760 and frame.timestamp_utc.is_unique
    snapshots=pd.DatetimeIndex(frame.timestamp_utc).tz_localize(None)
    net=pypsa.Network();net.set_snapshots(snapshots)
    for carrier in ['electricity','space_heat','hot_water']:net.add('Carrier',carrier)
    for name in ['electricity','space_heat','hot_water']:net.add('Bus',name,carrier=name)
    for name,col in [('electricity','non_heating_electricity_kw'),('space_heat','space_heat_kw'),('hot_water','hot_water_heat_kw')]:
        net.add('Load',name,bus=name,p_set=pd.Series(frame[col].to_numpy(dtype=float)/1000,index=snapshots))
    cop=pd.read_csv(folder/'heat_pump_cop_hourly.csv')
    for service,column in [('space_heat','COP_space_heat'),('hot_water','COP_dhw')]:
        net.add('Link','HP_'+service,bus0='electricity',bus1=service,efficiency=pd.Series(cop[column].to_numpy(),index=snapshots),p_nom_extendable=True)
    net.add('Generator','grid_supply',bus='electricity',carrier='electricity',p_nom_extendable=True)
    assert len(net.snapshots)==8760 and len(net.loads)==3 and len(net.links)==2
    np.testing.assert_allclose(net.loads_t.p_set['space_heat'].sum()*1000,frame.space_heat_kw.to_numpy(dtype=float).sum(),rtol=1e-12)
    net.consistency_check()
    # NetCDF is a minimal import example, not an optimised planning model.
    net.export_to_netcdf(folder/'example_cluster_network.nc')
    write_json(folder/'pypsa_import_check.json',{'passed':True,'stored_parquet_rows':n,
        'all_package_totals_reconciled':True,'pypsa_version':pypsa.__version__,'example_cluster':cluster,
        'snapshots':8760,'loads':3,'COP_links':2,'optimization_run':False,
        'example_boundary':'Useful DHW demonstration. Add declared DHW losses/technology split for a full baseline fuel reconstruction; do not interpret this example as calibrated dispatch.'})
    print('PASS: complete parquet totals and PyPSA network import')


if __name__=='__main__':main()
