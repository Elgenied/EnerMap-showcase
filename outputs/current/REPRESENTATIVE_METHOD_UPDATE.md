# Current representation: typical construction at three median areas

The full main pipeline now uses the approved virtual construction profiles: whole-cluster numerical medians and modal categories, evaluated at exact median floor areas of the existing small/medium/large bands. Original template geometry descriptors and exposure are retained, and the engine recalculates surfaces. Roof summaries are conditional on exposure. Three household draws per size are retained.

The original dwelling input table, cluster assignments, individual areas/heating systems, annual usage assumptions and household model were not overwritten. This is not calibration. Previously inspected benchmarks are not independent validation.

All 408 baseline/reference simulations and 612 retrofit-package simulations were rerun in the main pipeline. R1/R2 are applied to the virtual baseline. Scenario outputs, demand/validation maps, executed notebook reports and PyPSA exports have been regenerated. Full hourly baseline heat profiles and annual stock results reproduce the approved experiment exactly. Non-heating electricity shapes are regenerated from the virtual-size household profiles; annual totals are unchanged.

| Metric | Previous current run | New current run |
|---|---:|---:|
| Gas WAPE (%) | 7.278 | 7.261 |
| Electricity WAPE (%) | 11.213 | 11.160 |

| Quantity | Previous GWh/year | New GWh/year |
|---|---:|---:|
| Q_H_kWh | 565.582 | 567.570 |
| delivered_gas_kWh | 719.688 | 722.860 |
| delivered_elec_kWh | 246.761 | 246.555 |

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
