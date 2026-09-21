"""Check the packaged data and app without raw inputs or thermal simulations.

Run from the repository: python tests/check_dashboard.py
Other verify_*.py scripts inspect the research engine and require its full inputs.
"""
from pathlib import Path
import hashlib
import json
import math

import pandas as pd
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "outputs/current"
manifest = json.loads((ROOT / "docs/snapshot_manifest.json").read_text(encoding="utf-8"))
for entry in manifest["files"]:
    assert hashlib.sha256((ROOT / entry["file"]).read_bytes()).hexdigest() == entry["packaged_sha256"], entry["file"]
v = pd.read_csv(CURRENT / "baseline/lsoa_validation.csv")
m = json.loads((CURRENT / "baseline/metrics.json").read_text())
assert len(v) == 84 and v.LSOA.is_unique
for fuel in ["gas", "elec"]:
    obs, pred, weight = (v[f"{fuel}_mean_kWh"], v[f"sim_{fuel}_mean_kWh"], v[f"{fuel}_meters"])
    wape = 100 * ((pred-obs).abs()*weight).sum()/(obs*weight).sum()
    assert math.isclose(wape, m[f"{fuel}_WAPE_pct"], rel_tol=1e-10)
summary = pd.read_csv(CURRENT / "scenarios/scenario_summary.csv")
for row in summary[summary.scope.eq("borough")].itertuples():
    hourly = pd.read_csv(CURRENT / f"scenarios/{row.scenario}_hourly.csv")
    assert len(hourly) == 8760 and hourly.time.is_unique
    assert math.isclose(hourly.delivered_electricity_kWh.sum()/1e6, row.delivered_elec_GWh, rel_tol=1e-10)
    assert math.isclose(hourly.space_heat_kWh.sum()/1e6, row.Q_H_GWh, rel_tol=1e-10)
app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
assert not app.exception, [e.message for e in app.exception]
assert len(app.tabs) == 7
app.radio[0].set_value("Borough stock").run()
assert not app.exception
app.selectbox[0].set_value("U1_electrification").run()
assert not app.exception
app.slider[0].set_value(53)
app.slider[1].set_value(53)
app.multiselect[0].set_value([])
app.multiselect[1].set_value([])
app.run()
assert not app.exception
print("PASS: snapshot hashes, WAPE, annual/hourly agreement and dashboard controls")
