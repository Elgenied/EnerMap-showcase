<img src="assets/EnerMap_logo.png" width="440" alt="EnerMap logo">

# EnerMap research showcase

A private supervisor-review snapshot of the Guildford urban building energy modelling workflow: stock development, construction archetypes, household schedules, hourly demand, calibration, rooftop solar potential and decarbonisation planning.

**Start with the saved notebook outputs and the dashboard. No research calculations need to be rerun.** This is an isolated copy; the original working folders are not part of this Git repository. The results are exploratory: see [interpretation notes](docs/INTERPRETATION.md).

## Suggested presentation route

1. **Stock development** — `notebooks/01_stock`: footprint preparation, classification, census reconciliation, EPC completion and UPRN linkage.
2. **Archetypes and households** — `notebooks/02_model/00`–`04`: modelling inputs, construction groups, representatives, thermal setup and occupancy.
3. **Demand and calibration** — model notebooks `05`–`09`: representative runs, calibration, spatial refits, dwelling-tier comparison and demand atlas.
4. **Solar resource** — `notebooks/03_solar`: the supplied irradiation raster, rooftop suitability and PV yield.
5. **Planning** — model notebooks `10`–`12`: six cases U0–U5, heat-pump peaks, fabric flexibility, prioritisation and the PyPSA export interface.
6. **Interactive demonstration** — open the Streamlit app to compare saved scenarios and weekly profiles.

The road-proximity experiment is retained separately in `notebooks/04_explorations`. It is not presented as a completed link into the stock pipeline.

## Run the results explorer

Use Python 3.11 or 3.12 in a separate environment:

```sh
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

The app reads the included small aggregate CSV/JSON files and saved figures. No model engine, raw datasets or simulation run is required. Selecting scenarios or weeks changes the displayed saved results. This repository does not by itself host a live website; run the app locally after cloning or downloading the private repository.

## Notebook guide

Open the `.ipynb` files directly in GitHub to inspect code and cached static outputs. If a large notebook preview does not render, download it and open it locally in Jupyter or VS Code without executing its cells. Interactive widget payloads are excluded.

| Notebook | Purpose and status |
|---|---|
| [01_footprints_and_classification.ipynb](notebooks/01_stock/01_footprints_and_classification.ipynb) | OS MasterMap footprint preparation and evidence-based use classification. The source notebook has no saved outputs; no results have been invented. |
| [02_census_reconciliation.ipynb](notebooks/01_stock/02_census_reconciliation.ipynb) | Upstream stock completion and linkage, with retained safe cached results. |
| [03_epc_stock_completion.ipynb](notebooks/01_stock/03_epc_stock_completion.ipynb) | Upstream stock completion and linkage, with retained safe cached results. |
| [04_uprn_linkage.ipynb](notebooks/01_stock/04_uprn_linkage.ipynb) | Upstream stock completion and linkage, with retained safe cached results. |
| [00_data_basis.ipynb](notebooks/02_model/00_data_basis.ipynb) | Main EnerMap modelling sequence. Existing results are retained without rerunning calculations. |
| [01_archetypes.ipynb](notebooks/02_model/01_archetypes.ipynb) | Main EnerMap modelling sequence. Existing results are retained without rerunning calculations. |
| [02_representatives.ipynb](notebooks/02_model/02_representatives.ipynb) | Main EnerMap modelling sequence. Existing results are retained without rerunning calculations. |
| [03_engine_setup.ipynb](notebooks/02_model/03_engine_setup.ipynb) | Main EnerMap modelling sequence. Existing results are retained without rerunning calculations. |
| [04_household_schedules.ipynb](notebooks/02_model/04_household_schedules.ipynb) | Main EnerMap modelling sequence. Existing results are retained without rerunning calculations. |
| [05_archetype_run.ipynb](notebooks/02_model/05_archetype_run.ipynb) | Main EnerMap modelling sequence. Existing results are retained without rerunning calculations. |
| [06_calibration.ipynb](notebooks/02_model/06_calibration.ipynb) | Main EnerMap modelling sequence. Existing results are retained without rerunning calculations. |
| [07_spatial_cv.ipynb](notebooks/02_model/07_spatial_cv.ipynb) | Main EnerMap modelling sequence. Existing results are retained without rerunning calculations. |
| [08_dwelling_tier_check.ipynb](notebooks/02_model/08_dwelling_tier_check.ipynb) | Main EnerMap modelling sequence. Existing results are retained without rerunning calculations. |
| [09_lsoa_atlas.ipynb](notebooks/02_model/09_lsoa_atlas.ipynb) | Main EnerMap modelling sequence. Existing results are retained without rerunning calculations. |
| [10_ward_scenarios.ipynb](notebooks/02_model/10_ward_scenarios.ipynb) | Main EnerMap modelling sequence. Existing results are retained without rerunning calculations. |
| [11_pypsa_interface.ipynb](notebooks/02_model/11_pypsa_interface.ipynb) | Main EnerMap modelling sequence. Existing results are retained without rerunning calculations. |
| [12_planning_visuals.ipynb](notebooks/02_model/12_planning_visuals.ipynb) | Main EnerMap modelling sequence. Existing results are retained without rerunning calculations. |
| [01_rooftop_pv.ipynb](notebooks/03_solar/01_rooftop_pv.ipynb) | Rooftop PV suitability and yield from a supplied annual irradiation raster. This notebook does not itself establish the full upstream LiDAR-to-irradiation run provenance. |
| [01_osm_road_proximity.ipynb](notebooks/04_explorations/01_osm_road_proximity.ipynb) | Separate OSM road/land-use feature and decision-tree experiment. Its predictions are not imported by the inspected MasterMap-to-EPC pipeline; it must not be presented as an integrated classification stage. |

## Code and supporting material

* `ukubem/` — modelling, accounting, calibration, retrofit, flexibility and spatial-export code.
* `uk_ubem_schedule_generator/` — household and schedule source; underlying microdata excluded.
* `parameters/` — saved assumptions and their recorded sources.
* `docs/INPUT_SCHEMA.md` — what the original modelling pipeline expects.
* `docs/DATA_AND_REUSE.md` — included/excluded files and review-copy treatment.
* `docs/notebook_manifest.json` — source hashes, retained figure counts and removed-output reasons.

## Access

Keep the repository private. The owner can add supervisors under **Settings → Collaborators → Add people**. No collaborators are automatically invited and no public app deployment is created by this package.
