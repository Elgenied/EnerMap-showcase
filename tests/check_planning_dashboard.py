"""Verify the public atlas against the packaged research results, without raw data."""
from pathlib import Path
import json, math, csv
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/dashboard'
D=json.loads((OUT/'data/planning.json').read_text(encoding='utf-8'))
H=json.loads((OUT/'data/hourly.json').read_text(encoding='utf-8'))
def close(a,b,tol=1e-4): assert math.isclose(a,b,abs_tol=tol,rel_tol=1e-6),(a,b)
assert len(D['lsoa'])==84
assert sum(r['dwellings'] for r in D['lsoa'])==57300
assert sum(r['n'] for r in D['archetypes'])==57300
assert sum(len(r['sizes']) for r in D['archetypes'])==102
for key,target in [('gas_GWh','delivered_gas_kWh'),('electricity_GWh','delivered_elec_kWh'),('heat_GWh','Q_H_kWh')]:
    close(sum(r[key] for r in D['lsoa']),D['summary']['totals_GWh'][target],1e-3)
assert len(D['cells'])==271*6
assert len(D['cell_info'])==271
assert sum(r['modelled_dwellings'] for r in D['cell_info'])==28662
assert sum(r['dwellings'] for r in D['retrofit']['ward'])==28663
assert D['priority']['carbon_first'][5]['dwellings']==9063
assert D['priority']['equity_first'][5]['dwellings']==6850
for mode,curve in D['priority'].items():
    assert [x['budget_pct'] for x in curve]==list(range(0,101,5))
    assert curve[-1]['dwellings']==28663
    assert all(a['dwellings']<=b['dwellings'] for a,b in zip(curve,curve[1:]))
    for point in curve:
        assert sum(w['selected'] for w in point['wards'])==point['dwellings']
        close(sum(w['carbon_kt'] for w in point['wards']),point['carbon_kt'])
for s in D['scenarios']:
    wards=[w for w in D['wards'] if w['scenario']==s['scenario']]
    assert len(wards)==10 and sum(w['dwellings'] for w in wards)==28663
    for k in ['carbon_kt','bills_MGBP']: close(sum(w[k] for w in wards),s[k])
    f=next(f for f in D['finance'] if f['scenario']==s['scenario'])
    close(f['capex_gross_MGBP']-f['incentives_MGBP'],f['net_capex_MGBP'])
for s in D['infrastructure']:
    key=s['scenario'];cells=[r for r in D['cells'] if r['scenario']==key]
    assert sum(r['peak_import_rating_ratio']>1 for r in cells)==s['cells_above_import_rating_screen']
    series=H['series'][key]
    assert all(len(v)==8760 for v in series.values())
    close(max(series['imports']),s['coincident_cell_import_peak_MW'])
    close(sum(series['imports'])/1000,s['cell_import_GWh'],1e-3)
for name,n in [('lsoa',84),('wards',10),('cells',271)]:
    geo=json.loads((OUT/f'data/{name}.geojson').read_text())
    assert len(geo['features'])==n
    assert all(set(f['properties'])=={'id'} for f in geo['features'])
    assert all(f['geometry']['type'] in ['Polygon','MultiPolygon'] for f in geo['features'])
for r in D['gallery']: assert (OUT/r['src']).is_file()
# No raw household identifiers or addresses may enter the public data.
for p in (OUT/'data').glob('*.json'):
    if p.name=='provenance.json': continue
    txt=p.read_text(encoding='utf-8').lower()
    for field in ['"dwelling_id":','"uprn":','"postcode":','"toid":','"buildingaddress":','"lat":','"lon":']:
        assert field not in txt,(p.name,field)
print('PASS: area totals, 6 scenarios, financial balances, priority curves, hourly peaks, geometry and public-data fields')

html=(OUT/'index.html').read_text(encoding='utf-8')
app=(OUT/'app.js').read_text(encoding='utf-8')
assert 'data-view="validation"' not in html
assert 'Uncalibrated' not in app and 'Agreement with annual consumption' not in app
assert all(r['section']!='Validation' for r in D['gallery'])
print('PASS: validation view and comparison figures excluded from public planning interface')
