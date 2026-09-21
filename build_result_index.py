"""Write the single readable index after verifying the current workflow completed."""
from pathlib import Path
import json
import html
import hashlib
from datetime import datetime, timezone
import nbformat
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'outputs/current'
summary=json.loads((OUT/'baseline/summary.json').read_text())
checks=json.loads((OUT/'checks/accounting.json').read_text())
interface=json.loads((OUT/'pypsa_interface/pypsa_import_check.json').read_text())
assert summary['completed'] and checks['passed'] and interface['passed']
physics=json.loads((OUT/'checks/physics/PASSED.json').read_text())
assert physics['passed']
scenario_checks=json.loads((OUT/'checks/scenarios.json').read_text())
assert scenario_checks['passed']
packages={p:json.loads((OUT/("baseline" if p=='R0' else f'packages/{p}')/'complete.json').read_text()) for p in ['R0','R1','R2']}
assert all(x['completed'] for x in packages.values())
assert [packages[p]['simulations'] for p in ['R0','R1','R2']]==[408,306,306]
for prefix in ['05','06','07','09','10','11','12']:
    paths=list(ROOT.glob(prefix+'_*.ipynb'))
    assert len(paths)==1
    notebook=nbformat.read(paths[0],as_version=4)
    code=[c for c in notebook.cells if c.cell_type=='code' and c.source.strip()]
    assert code and all(c.execution_count is not None for c in code), paths[0]
    assert not any(o.output_type=='error' for c in code for o in c.get('outputs',[])), paths[0]
    assert (OUT/'reports'/paths[0].with_suffix('.html').name).exists()
assert (OUT/'scenarios/planning_ward_economics_U5.csv').exists()
assert (OUT/'scenarios/planning_ward_summary.csv').exists()
metrics=summary['metrics']
report_links=''.join(f'<li><a href="reports/{p.name}">{html.escape(p.stem)}</a></li>' for p in sorted((OUT/'reports').glob('*.html')))
figures=''.join(f'<section><h2>{html.escape(p.stem.replace("_"," "))}</h2><img src="figures/{p.name}"><p><a href="figures/{p.stem}.pdf">PDF</a> · <a href="figures/{p.stem}.svg">SVG</a></p></section>' for p in sorted((OUT/'figures').glob('*.png')))
content=f'''<!doctype html><html><head><meta charset="utf-8"><title>EnerMap — current results</title><style>
body{{font:16px/1.6 system-ui,sans-serif;max-width:1150px;margin:40px auto;padding:0 22px;color:#20343c;background:white}}
h1,h2{{line-height:1.2}}a{{color:#167789}}img{{max-width:100%;height:auto}}section{{margin:45px 0;border-top:1px solid #ddd;padding-top:20px}}
.summary{{background:#eff6f5;padding:20px;border-radius:8px}}table{{border-collapse:collapse}}td,th{{padding:8px 20px;text-align:left;border-bottom:1px solid #ddd}}
</style></head><body><h1>EnerMap: current uncalibrated results</h1><p>One source of current results, generated from the corrected main project. Historical results remain recoverable in <code>_archive/20260918_before_unified</code>.</p>
<div class="summary"><strong>Complete:</strong> 408 baseline/reference annual simulations, 612 paired retrofit simulations, annual validation, demand maps and full-borough PyPSA interface. No calibration or spatial refitting.<br>
57,300 dwellings · 84 LSOAs · annual/hourly accounting verified · PyPSA import checked (no optimisation run).</div>
<h2>Annual comparison</h2><table><tr><th>Fuel</th><th>WAPE</th><th>Signed bias</th><th>Predictive R²</th></tr>
<tr><td>Gas</td><td>{metrics['gas_WAPE_pct']:.2f}%</td><td>{metrics['gas_NMBE_pct']:+.2f}%</td><td>{metrics['gas_R2_predictive']:.3f}</td></tr>
<tr><td>Electricity</td><td>{metrics['elec_WAPE_pct']:.2f}%</td><td>{metrics['elec_NMBE_pct']:+.2f}%</td><td>{metrics['elec_R2_predictive']:.3f}</td></tr></table>
<p>Observed means are per consuming meter; modelled means are per eligible dwelling. Annual agreement does not validate hourly peaks or individual homes. The modelled gross whole-stock electricity peak is {summary['electricity_peak_MW']:.2f} MW; it is not an observed or independently validated peak.</p>
<h2>Reports and data</h2><ul>{report_links}<li><a href="baseline/DISCUSSION.md">Validation discussion</a></li><li><a href="baseline/MAP_NOTES.md">Map definitions</a></li><li><a href="pypsa_interface/README_pypsa_interface.md">PyPSA interface specification</a></li><li><a href="baseline/lsoa_validation.csv">All LSOA comparisons</a></li><li><a href="baseline/lsoa_demand_totals.csv">LSOA demand totals</a></li></ul>
<p>Only the existing heat-pump electricity conversion changed relative to the corrected baseline experiment. The fixed seasonal factors are not used in the active accounting path. New electrification scenarios retain the separately declared trial-based heating shape/uplift. Prices, policy proxies and carbon factors are retained project assumptions, not updated advice.</p>{figures}</body></html>'''
(OUT/'INDEX.html').write_text(content,encoding='utf-8')
source_paths=[ROOT/p for p in ['ukubem/unified.py','ukubem/reporting.py','run_all.py','build_result_index.py']]
source_paths+=list((ROOT/'tests').glob('verify_*.py'))
hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
(OUT/'completion.json').write_text(json.dumps({'completed':True,'completed_utc':datetime.now(timezone.utc).isoformat(),
    'calibration':False,'baseline_simulations':408,'retrofit_simulations':612,'interface_import_passed':True,
    'physics_tests':physics['tests'],'accounting_tests':len(checks['checks']),
    'package_fingerprints':{p:v['fingerprint'] for p,v in packages.items()},'pipeline_source_hashes':hashes},indent=2),encoding='utf-8')
print(OUT/'INDEX.html')
