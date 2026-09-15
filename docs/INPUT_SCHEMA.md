# Inputs of EnerMap

Everything the tool reads sits in this folder; `config.py` names each file. To run EnerMap for another local
authority, replace these files (same layout, same column names) and, where they differ, the files in
`parameters/`. Column names in **bold** are required by `ukubem/stock.py`; the others are optional and the
loader fills them when absent.

## stock/

### `stock_table.parquet` — one row per dwelling

| group | columns | notes |
|---|---|---|
| identifiers, geography | **dwelling_id**, **UPRN**, **toid** (building footprint id), **LSOA**, **postcode**, **lon**, **lat** | `toid` links to `spatial/buildings.gpkg`; several dwellings share a `toid` in blocks of flats |
| provenance | **attributes_observed** (bool), **link_method**, **floor_area_prov**, **wall_U_prov**, **roof_U_prov**, **window_U_prov**, **age_band_prov**, **evidence_vintage** | `evidence_vintage`: `in_window` (certificate inside the benchmark window), `post_window`, `synthetic` (synthesised record for an un-certificated dwelling) |
| dating | **in_calibration_population** (bool) | the dwelling existed in the benchmark year (the calibration population) |
| resolved attributes | **accommodation_type**, **built_form_resolved**, **heat_system_resolved**, **wall_construction_resolved**, **wall_insulation_resolved**, **glazing_type_resolved**, **roof_type_resolved**, **tenure_resolved**, **age_band_resolved**, **main_fuel**, **mains_gas_flag** (`Y`/`N`), **flat_top_storey** (`Y`/`N`) | certificate vocabulary (RdSAP age bands A–L) |
| geometry | **floor_area_final** (m²), **area_m2** (footprint), **storeys_est**, **height_eaves_m**, **height_ridge_m**, **volume_m3**, **uprn_count** (dwellings in the building), **is_flat_roof** | from the footprint and height layers |
| envelope | **wall_U**, **roof_U** (null = a dwelling above), **window_U**, **window_SHGC**, **window_Tvis** | W/m²K; certificate values where recorded, otherwise RdSAP lookups (provenance says which) |
| typology labels | **typology**, **t_form**, **t_era**, **t_wall**, **t_heat** | descriptive labels used in tables and figures |

### `study_area_population.parquet` — the dwellings of the planning study area

`dwelling_id`, `ward`, `ward_code`, `in_urban_10wards` (bool; renamed `in_study_area` by the loader), `pv_kWp`,
`pv_annual_kWh` (rooftop PV potential of the dwelling's share of its building, from the solar layer).

### `epc_certificate_figures.parquet` — optional

`dwelling_id`, `energy_consumption_current` (kWh/m²/yr), `co2_emiss_curr_per_floor_area`, `current_energy_efficiency`
(SAP score), `heating_cost_current`. Certificate asset-rating figures, used only descriptively (notebooks 00–02).

## benchmarks/

* `desnz_lsoa_2021.csv` — DESNZ sub-national consumption per LSOA: `lsoa`, `gas_meters`, `gas_mean_kWh`,
  `gas_median_kWh`, `elec_meters`, `elec_mean_kWh`, `elec_median_kWh` (plus totals). The only calibration and
  validation basis of the tool.
* `spatial_cv_folds_lsoa.csv` — `LSOA`, `fold`: contiguous bands of LSOAs for the spatial cross-validation
  (built so that no building straddles a band boundary).

## spatial/ (EPSG:27700)

* `buildings.gpkg` — every footprint of the authority incl. non-residential (`toid`, geometry, residential flag);
  drawn under every map so the town has no empty spots.
* `wards.gpkg` — the study-area wards (`ward` name), `study_area_boundary.gpkg` — its outline.
* `lsoa/lsoa.shp` — LSOA polygons (`LSOA` code).
* `pv_building_potential.gpkg` — rooftop PV potential per building (`toid`, `peak_power_kwp`, annual yield),
  `pv_address_potential.csv` — the same per address.

## network/

* `buildings_nearest_substation.csv` — `egid` (UPRN), `id_transformer`, `x`, `y`: every building assigned to its
  nearest secondary substation (Voronoi-style cells). A spatial inference, never a verified connection.
* `substations.csv` — `functionallocation`, `onanrating` (kVA), `customer_count`, `easting`, `northing`.
* `secondary_substations.geojson` — the same substations as points (maps).

## social/ (optional)

* `dwelling_social_context.parquet` — `dwelling_id`, `social_context` (a neighbourhood context label).
* `lsoa_social_context.csv` — per LSOA: `imd_decile`, `fuel_poor_pct` and the tenure / built-form shares used for
  incentive eligibility (ECO4 proxy) and the equity maps.

## weather/

* one EnergyPlus weather file (`.epw`) — a typical meteorological year for the authority; `config.EPW` names it.

## Provenance of the Guildford files

The Guildford stock table was assembled from the EPC register linked to UPRNs, OS footprints and heights, RdSAP
lookups for missing U-values, and a conditional synthesis of the categorical attributes of un-certificated
dwellings (seeded by LSOA and accommodation type, reconciled with Census 2021 dwelling counts). The DESNZ tables
are the 2021 sub-national gas and electricity statistics; the substation and PV layers come from the network
operator's open data and a LiDAR rooftop study. The preparation pipelines are archived outside the tool
(`../_obsolete_no_longer_used/`); the tool only needs the files listed here.
