"""Independent interface and controlled thermal tests for the isolated revision."""
import os
os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
import copy
import json
import hashlib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT/'deps/pyBuildingEnergy/src'
EPW = ROOT/'data/inputs/weather/GBR_ENG_Farnborough.AP.037680_TMYx.2007-2021.epw'
OUT = ROOT/'outputs/current/checks/physics'
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
from ukubem.geometry import UKGeometryAssumptions, build_bui
from ukubem.engine import PBE, run_building
from ukubem.runner import simulate_rows
PBE.load(SRC)
from pybuildingenergy import ISO52016
from pybuildingenergy.source.check_input import sanitize_and_validate_BUI
from pybuildingenergy.source.ventilation import VentilationInternalGains


def run_checks():
    tests = []
    def check(name, passed, detail):
        tests.append({'test':name, 'passed':bool(passed), 'detail':str(detail)})
        (OUT/'tests.json').write_text(json.dumps(tests, indent=2), encoding='utf-8')
        print(('PASS ' if passed else 'FAIL ')+name+': '+str(detail), flush=True)
        assert passed, name

    inputs = pd.read_parquet(ROOT/'outputs/representatives/dwelling_inputs.parquet')
    reps = pd.read_csv(ROOT/'outputs/current/baseline/representatives.csv')
    a = UKGeometryAssumptions()
    rows=[]
    for did in reps.dwelling_id:
        bui, derived = build_bui(inputs.loc[did], a)
        total=sum(s['thermal_capacity']*(s['area'] if s['type']=='opaque' else 1.) for s in bui['building_surface'])
        checked, issues = sanitize_and_validate_BUI(copy.deepcopy(bui), fix=True)
        assert not [x for x in issues if x['level']=='ERROR'], (did, issues)
        # Conservation after sanitisation too: no area or unit changes hidden downstream.
        after=sum(s['thermal_capacity']*(s['area'] if s['type']=='opaque' else 1.) for s in checked['building_surface'])
        rows.append({'dwelling_id':int(did),'target_J_K':derived['C_total_J_K'],'adapter_J_K':total,'sanitized_J_K':after})
    caps=pd.DataFrame(rows)
    caps.to_csv(OUT/'capacity_conservation.csv', index=False)
    check('Fabric capacity conserved for every representative', np.allclose(caps.target_J_K,caps.adapter_J_K) and np.allclose(caps.target_J_K,caps.sanitized_J_K), f'{len(caps)} representatives')

    b,d=build_bui(inputs.loc[47095],a)
    # Stochastic representation sets nested full-load built-ins to zero.
    for gain in b['building_parameters']['internal_gains']: gain['full_load']=0.
    vi=VentilationInternalGains(b)
    zero=vi.internal_gains(b['building']['building_type_class'],d['A_floor'])
    check('Nested zero built-in gains are respected', zero==0., zero)
    b['building_parameters']['internal_gains'][0]['full_load']=4.
    value=vi.internal_gains(b['building']['building_type_class'],d['A_floor'],h_occup=.5)
    check('Nested nonzero gains respect occupancy multiplier', np.isclose(value,2*d['A_floor']),value)
    for gain in b['building_parameters']['internal_gains']: gain['full_load']=0.
    bc,_=sanitize_and_validate_BUI(copy.deepcopy(b),fix=True)
    ground=[]
    for psi in [0.,.05,.5]:
        g=ISO52016.Temp_calculation_of_ground(copy.deepcopy(bc),psi_k=psi,path_weather_file=str(EPW),weather_source='epw')
        ground.append(g)
        check(f'Configured whole-envelope bridge independent of floor-junction psi={psi}',np.isclose(g.thermal_bridge_heat,d['H_tb_W_K']),g.thermal_bridge_heat)
    check('Ground-junction term cancels from virtual temperature',all(np.allclose(g.Theta_gr_ve,ground[0].Theta_gr_ve,atol=1e-10) for g in ground),np.max(abs(ground[-1].Theta_gr_ve-ground[0].Theta_gr_ve)))
    b2=copy.deepcopy(bc)
    b2['building_parameters']['construction']['thermal_bridges']*=2
    g2=ISO52016.Temp_calculation_of_ground(b2,path_weather_file=str(EPW),weather_source='epw')
    check('Doubling configured bridges leaves ground temperature unchanged', np.allclose(g2.Theta_gr_ve,ground[1].Theta_gr_ve) and np.isclose(g2.thermal_bridge_heat,2*d['H_tb_W_K']), 'Bridge coefficient doubles; no duplicated change to ground boundary')

    # One uniform outdoor envelope; no ground, solar, built-in or external gains.
    zone=copy.deepcopy(b)
    zone['building'].update(net_floor_area=50.,exposed_perimeter=10.,construction_class='class_i')
    surf=copy.deepcopy(b['building_surface'][0])
    surf.update(name='Test envelope',area=120.,u_value=.4,thermal_capacity=100000.,solar_absorptance=0.,
                convective_heat_transfer_coefficient_internal=2.5,
                radiative_heat_transfer_coefficient_internal=5.13,
                convective_heat_transfer_coefficient_external=20.,
                radiative_heat_transfer_coefficient_external=4.14)
    zone['building_surface']=[surf]
    bp=zone['building_parameters']
    bp['construction']['thermal_bridges']=.2
    bp['ventilation']['custom_heat_transfer_coefficient_ventilation']=20.
    bp['temperature_setpoints'].update(heating_setpoint=20.,heating_setback=20.)
    bp['heating_profile']={'weekday':[1.]*24,'weekend':[1.]*24}
    bp['system_capacities']['heating_capacity']=100000.
    bp.pop('hourly_profiles',None)
    checked, issues=sanitize_and_validate_BUI(copy.deepcopy(zone),fix=True)
    assert len(checked['building_surface'])==1
    w=PBE.weather(EPW,checked).copy()
    w['T2m']=5.
    for c in w:
        if c in ['G(h)','Gb(n)','Gd(h)','IR(h)','HOR','NV','EV','SV','WV'] or str(c).startswith(('I_sol','solar_irradiance')): w[c]=0.
    def run(z, gains=None, short=False):
        return run_building(copy.deepcopy(z),EPW,SRC,use_weather_cache=False,
            weather_sim_df=w.iloc[:240].copy() if short else w,
            external_internal_gains_w=np.zeros(240 if short else 8760) if gains is None else gains,
            delta_Theta_er=0.,**({'warmup_hours':0} if short else {}))[0]
    h=run(zone)
    h.to_parquet(OUT/'analytical_steady_case.parquet')
    # With one interior surface, mean radiant temperature equals its temperature;
    # interior longwave exchange cancels. All HVAC output is convective.
    hci=2.5; hri=5.13; u=.4; area=120.; tout=5.; target=20.
    g_out=1/(1/u-1/(hci+hri))
    # hci*(Ta-Ts) = g_out*(Ts-To), with (Ta+Ts)/2 = target.
    ta=(2*target*(hci+g_out)-g_out*tout)/(2*hci+g_out)
    ts=2*target-ta
    q_ref=area*hci*(ta-ts)+(20.+2.)*(ta-tout)
    tail=h.tail(240)
    pd.DataFrame([{'analytical_heat_W':q_ref,'simulated_heat_W':tail.Q_HC.mean(),'analytical_air_C':ta,'simulated_air_C':tail.T_air.mean()}]).to_csv(OUT/'analytical_steady_comparison.csv',index=False)
    check('Steady heat matches independent analytical solution',np.isclose(tail.Q_HC.mean(),q_ref,rtol=1e-6),f'{tail.Q_HC.mean():.6f} W vs {q_ref:.6f} W')
    check('No ground loss in no-ground zone',np.isfinite(h.Q_ground).all() and np.allclose(h.Q_ground,0.),h.Q_ground.abs().max())
    check('Zero gains produce zero reported internal gains',np.allclose(h.Q_internal_gains,0.),h.Q_internal_gains.abs().max())
    check('Air-node equation closes',h.Q_air_balance_residual_W.abs().max()<1e-5,h.Q_air_balance_residual_W.abs().max())
    hg=run(zone,np.full(8760,300.))
    check('External gains enter once and are reported',np.allclose(hg.Q_internal_gains,300.) and np.isclose(hg.tail(240).Q_HC.mean(),q_ref-300.,rtol=1e-6),hg.tail(240).Q_HC.mean())
    # Thermostat continues to control the same operative temperature.
    free=copy.deepcopy(zone);free['building_parameters']['system_capacities']['heating_capacity']=0.
    low=run(free,short=True)
    heavy=copy.deepcopy(free);heavy['building_surface'][0]['thermal_capacity']*=10
    high=run(heavy,short=True)
    low.to_parquet(OUT/'free_decay_corrected.parquet');high.to_parquet(OUT/'free_decay_tenfold_capacity.parquet')
    check('Zero installed capacity gives zero heating',np.allclose(low.Q_HC,0.),low.Q_HC.max())
    check('Larger capacity cools more slowly',high.T_air.iloc[23]>low.T_air.iloc[23],f'24 h: {low.T_air.iloc[23]:.3f} C vs {high.T_air.iloc[23]:.3f} C')
    residual=max(low.Q_air_balance_residual_W.abs().max(),high.Q_air_balance_residual_W.abs().max())
    check('Air-node equation also closes during free cooling',residual<1e-5,residual)
    pd.DataFrame(tests).to_csv(OUT/'tests.csv',index=False)
    source_hashes={}
    for rel in ['ukubem/geometry.py','ukubem/engine.py','deps/pyBuildingEnergy/src/pybuildingenergy/source/utils.py','deps/pyBuildingEnergy/src/pybuildingenergy/source/ventilation.py']:
        with (ROOT/rel).open('rb') as f:source_hashes[rel]=hashlib.file_digest(f,'sha256').hexdigest()
    (OUT/'PASSED.json').write_text(json.dumps({'passed':True,'tests':len(tests),'source_hashes':source_hashes}),encoding='utf-8')
    return tests

if __name__=='__main__': run_checks()

