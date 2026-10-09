# U4 hourly PV and substation-cell balance

| item                                                 |   count |
|:-----------------------------------------------------|--------:|
| Planning-area dwellings                              |   28663 |
| Included mapped dwellings                            |   28662 |
| Unassigned dwellings excluded                        |       1 |
| Included substation cells                            |     271 |
| Dwellings with positive PV potential                 |   22219 |
| Reported ratings available                           |     271 |
| Multi-transformer stations (rating basis unverified) |       6 |

## Results

| case                     |   capacity_MWp |   PV_GWh |   cell_import_GWh |   cell_export_GWh |   dwelling_self_consumption_pct |   cell_PV_absorption_pct |   coincident_cell_import_peak_MW |
|:-------------------------|---------------:|---------:|------------------:|------------------:|--------------------------------:|-------------------------:|---------------------------------:|
| U0_existing_no_pv        |          0     |    0     |           121.277 |             0     |                         nan     |                  nan     |                           42.132 |
| U1_electrification_no_pv |          0     |    0     |           202.778 |             0     |                         nan     |                  nan     |                          110.419 |
| U4_capped_south          |         69.363 |   75.247 |           161.367 |            33.835 |                          45.705 |                   55.034 |                          110.419 |
| U4_capped_east_west      |         69.363 |   75.247 |           160.4   |            32.868 |                          47.045 |                   56.319 |                          110.419 |

U0 is the current stock's gross modelled electricity demand with its existing heating systems. It is not measured substation loading and does not deduct unknown existing installed PV. U1 electrifies the eligible fossil-heated dwellings. U4 has exactly U1's gross electricity demand; only PV differs. The primary U4 case uses a 4 kWp-per-dwelling cap, limited by the available allocated roof potential. Full potential is an optional sensitivity, disabled by default. No source scenario outputs or parameters were changed.

## Relation to the Sheffield study

Park and Wang (2024), *An Integrated Urban Building Energy Benchmarking Workflow to Support Urban Energy Evaluation: A Case Study of Sheffield UK*, Buildings 14, 3398, https://doi.org/10.3390/buildings14113398, use hourly D-G balances. Their Section 4 uses 12% module efficiency, 80% performance ratio and 60% roof use. No separate kWp-per-property cap is stated. Therefore they use suitable-roof potential under those assumptions, not every square metre at unrestricted output.

Here the existing EnerMap solar assessment supplies annual yield and capacity. Its roof filters and yield assumptions are retained. The paper's factors are not applied again. Capped U4 reproduces the configured 4 kWp/dwelling capacity and annual generation; full-potential U4 removes that extra dwelling cap while retaining the assessed allocation to modelled residential addresses. It is neither all roofs in the borough nor observed installed capacity. The largest allocations are separately listed for review.

## Interpretation

* PV offsets electricity, including hourly heat-pump electricity; it is never subtracted from useful heat or gas.
* Dwelling import/export applies min(D,G) at each dwelling PV allocation. Shared roofs retain their existing apportioned shares; this is not a verified metering arrangement.
* Cell import/export uses max(sum(D-G),0) / max(sum(G-D),0). Within-cell coincidence is the difference between local absorption and dwelling self-use. This is an accounting boundary comparison, not additional physical generation, guaranteed household bill savings, peer-to-peer trading or proof of switchable microgrids.
* The overall import/export series sums separate cell boundaries; it never cancels one cell's export against another cell's import. Signed whole-area net balance is reported separately.
* Transformer ratings remain fixed. Reported kVA is converted to an illustrative kW threshold at PF=0.95. Multi-transformer records are flagged, not multiplied. These are residential-load screening ratios, not actual network headroom or overload findings. Non-residential demand, other customers, reactive power, cable impedances, phases, voltage limits and export permissions are unresolved.
* Every positive annual PV yield is preserved in the hourly profile. The shared south-facing 35-degree shape is a modelling assumption, with equal east/west sensitivity. The same weather clock is used for heat and PV. Hourly roof shading and individual roof directions are not resolved; annual shading/roof suitability remain in the input potential. Inverter clipping and export curtailment are not modelled.
* The fixed 45% self-consumption calculation is included only as a comparison. It is not applied on top of the hourly balance. Canonical U4 annual results are not overwritten.
* Maps regenerate nearest-point Voronoi polygons from all supplied substation points and clip to the planning boundary. Hourly membership always uses the original mapping; any geometric exceptions are exported.

## Validation and files

Every included dwelling's annual demand is reconciled to stored U0/U1 outputs. All 8760 aggregated hours are checked against canonical scenario accounting, without a thermal rerun. Capped PV reproduces current U4 capacity and annual yield, source roofs are checked for duplicate allocations, night-time PV is zero, and import/export energy identities are checked. A two-dwelling synthetic test distinguishes household export from cell export.

`scenario_summary.csv` and `cell_summary.csv` are the principal results. `*_cell_hourly.parquet` contains full per-cell series; `*_aggregate_hourly.csv` contains simultaneous sums. `coverage.csv`, `roof_allocation_audit.csv`, `largest_pv_allocations.csv`, `map_geometry_exceptions.csv`, `checks.json` and `manifest.json` document scope and reproducibility. Figures are saved as PNG and PDF.

`annual_solar_potential_by_substation.csv` and the `annual_solar_potential` layer in `substation_cells.gpkg` give full assessed and capped annual PV generation potential for each included secondary-substation Voronoi cell. Figure 08 maps both on the same colour scale. These are sums of residential dwelling roof shares in the planning area, not all residential/non-residential roofs across the complete service area, measured installed generation, irradiance, self-consumption or export. The uncapped annual inventory is provided even when the full-potential hourly sensitivity is disabled.

Method references: https://pvlib-python.readthedocs.io/en/stable/reference/generated/pvlib.iotools.read_epw.html and https://pvlib-python.readthedocs.io/en/stable/reference/generated/pvlib.irradiance.get_total_irradiance.html
