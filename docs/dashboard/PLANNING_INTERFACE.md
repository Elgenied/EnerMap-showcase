# EnerMap planning interface

The default public dashboard is a static Leaflet/Plotly application on GitHub Pages.
It opens the map directly and has no Python runtime download. The original browser-hosted
Streamlit explorer remains available at `streamlit.html`, with its pinned research snapshot.

## Views

- Energy atlas: LSOA gas, electricity, useful heat, emissions and heat intensity; area totals
  or per-dwelling values; daily and hourly baseline profiles.
- Archetypes: 34 construction groups, 102 virtual representatives, construction properties,
  heating-family counts, LSOA shares and linked building footprints. A shared footprint
  shows its dominant construction group; the inspector lists the full dwelling mix.
- Scenarios: U0–U5 ward maps, baseline differences, energy, emissions, bills and investment.
- Retrofit & equity: fabric savings, capped/assessed residential solar, construction difficulty,
  tenure, neighbourhood fuel poverty/deprivation, payback and two budget priority orders.
- Electricity networks: 271 inferred cells, import/export screening, solar potential, annual
  imports and exports, coincident peaks and cell-specific time series. Select a secondary
  substation by name or on the map; annual charts, KPIs and profiles follow the selection.
  Compare U0–U5 for electricity imports, exports, gross electricity, useful space heat,
  capped scenario PV generation and full assessed residential PV potential.
- Figure library: detailed research maps, solar maps and financial/priority figures.

Charts export PNGs at 3× resolution. The export button downloads aggregate view data as CSV.
Methods and population boundaries remain accessible in About & data. The footprint display
layer contains simplified building polygons, roof potential and construction-group counts.
It omits private addresses, UPRN/TOID linkage and household-level financial records.
Original mapping-provider terms apply to derived display geometry.

Solar maps default to full assessed potential per building, including non-residential roofs.
Cream-to-maroon classes are >0–2, >2–5, >5–10, >10–25 and >25 MWh/year; zero yield is grey.
Capacity, suitable area and retained-roof irradiation are additional map layers. These are
separate from the capped residential scenarios. Clicking a building also updates its solar
weekly and hourly profiles. All solar time series use the study's common south-facing
profile to distribute annual yield; they are not building-specific hourly SEBE simulations.

Weekly energy is the sum of hourly kW values divided by 1,000 (MWh). Weeks begin on
1 January in consecutive 168-hour blocks; week 53 contains the final 24 hours. Hourly
profiles cover a selected week. CSV controls export all 53 weeks or all 8,760 hours.
All-cell imports/exports sum each cell's separately netted balance, preserving spatial
coincidence; they are not the clipped net balance of the entire study area.

## Rebuild from a completed run

```text
python analysis/export_planning_dashboard.py --source /path/to/enermap_ubem --figure-dir /path/to/Part_two_fig
python analysis/export_explorer_details.py --source /path/to/enermap_ubem
python tests/check_planning_dashboard.py
node tests/check_explorer_details.cjs
node --check docs/dashboard/app.js
```

Requires pandas, numpy, geopandas and pyarrow only for export. The website needs no build
step. `data/provenance.json` records input hashes; `planning.json` and the GeoJSON files are
curated aggregate exports. The case-study name and run date are stored in the bundle.
Do not copy raw input directories into the public repository.

The detail exporter imports the source project's accounting and heat-pump functions. It
reconstructs four unique electricity/heat profiles from completed representative simulations,
then reconciles all individual annual electricity demands, every aggregate hour, and all
271 cell annual imports, exports and peaks in six scenarios. `detail_checks.json` records
these checks and source hashes. No thermal engine is rerun. Profiles are rounded to 0.001 kW
and gzip-compressed; `profile_manifest.json` supplies solar weights, cell labels and files.
The browser uses the native DecompressionStream API and loads one cell on demand.

Preview with `python -m http.server 8767 --directory docs`, then open `/dashboard/`.
After pushing, verify the GitHub Pages deployment and public view. Keep the existing URL.

## Display and accounting rules

- Scenario maps use a common scale across U0–U5. Percentage-change maps use a symmetric scale.
- Network screening uses explicit 50%, 100%, 150%, 200% bands; above 100% is an exceedance.
- Missing values are grey, not zero. No statistical smoothing is applied to geography.
- LSOA demand covers the borough. Ward scenarios cover the ten-ward residential stock.
- Network profiles cover 28,662 assigned dwellings, not the entire borough. Voronoi cells
  do not establish electrical connectivity or available capacity.
- PV financial annual accounting and hourly network accounting are distinct.
- Priority curves retain full dwelling packages and the original stable sorting order.
- Financial/support assumptions are study inputs, not current quotations or entitlements.

## Libraries and attribution

Leaflet 1.9.4 (BSD-2-Clause) and Plotly 3.3.1 (MIT) are bundled in `vendor/`, with their
upstream licence files. Basemap requests are made directly to OpenStreetMap tile servers;
attribution is visible on the map. The light option desaturates the same OSM tiles.
ONS boundary attribution is displayed; source figure attribution is retained.
