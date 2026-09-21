# EnerMap — unified uncalibrated demand framework

This is the main working project. **Use `outputs/current/` for all new demand, validation, scenario and PyPSA-interface results.** Earlier calibrated results are historical and are archived; they are not inputs to the current workflow.

## Start here

- `app.py`: Streamlit explorer of the completed current run. Install `requirements-dashboard.txt`, then run `python -m streamlit run app.py`. Includes validation, demand profiles/maps, planning scenarios and modelled electricity peaks.
- `outputs/current/INDEX.html`: current figures, headline results and report links.
- `05_archetype_run.ipynb`: corrected thermal simulations and consistent annual/hourly demand.
- `06_validation.ipynb`: observed versus modelled consumption, LSOA maps and Canet-inspired error distributions.
- `07_consistency_checks.ipynb`: physical and accounting tests, not spatial refitting.
- `09_lsoa_atlas.ipynb`: demand totals, building/LSOA maps and hourly electricity.
- `10_ward_scenarios.ipynb`: existing U0–U5 scenario definitions, with the new baseline and hourly HP conversion.
- `11_pypsa_interface.ipynb`: borough-wide R0/R1/R2 retrofit and demand export.
- `12_planning_visuals.ipynb`: current chapter-figure index and summaries.

Notebooks 00–02 and the clustering diagnostics retain the existing data preparation, archetypes and dwelling inputs. Notebooks 03–04 retain engine/household demonstrations; their old output cells were cleared and their paths updated, not their household algorithms. Run them separately when needed. The default demand rerun does not re-cluster or regenerate inputs.

## One results tree

```text
outputs/current/
  baseline/          stock, representatives, annual/hourly demand, validation and discussion
  packages/R1,R2/    paired retrofit-package simulations and profiles
  checks/            physics and accounting verification
  scenarios/         borough results plus labelled planning-ward summaries
  pypsa_interface/   mapping, retrofit catalogue, quantities, demand, COP and flexibility
  figures/           chapter figures in PNG, PDF and SVG
  reports/           executed notebook HTML reports
  INDEX.html         readable entry point
```

The main stock is 57,300 dwellings and the annual comparison retains all 84 available Guildford LSOAs. Borough demand/validation maps are not clipped to the ten planning wards. Existing network coverage and planning-ward economic scope are explicitly labelled; assumed LSOA electricity zones are not verified network connections.

## What changed

- Corrected active-surface thermal-capacity units, configured thermal bridges, nested internal-gain overrides, explicit no-ground geometry and heat-balance reporting in the main engine.
- Removed calibration and spatial-refitting notebooks from the active workflow. No fitted physics or old calibrated outputs are read.
- Preserved completed-stock inputs, construction groups, size selection, household probabilities, three stochastic draws, seeds, annual BREDEM usage and boiler assumptions.
- Annual space heat comes from the mean of the three hourly thermal simulations for each size representative, expanded by each dwelling's own area. The 102 deterministic reference cases are not the stock prediction.
- Existing heat pumps use hourly ASHP COP: radiator temperature = 40 minus outdoor temperature, minimum lift 15 K, COP = max(1, 6.08 − 0.09 ΔT + 0.0005 ΔT²). DHW uses the configured 50°C sink.
- Annual HP electricity is the sum of hourly heat/COP. The core accounting API rejects missing hourly HP electricity instead of silently using fixed seasonal factors.
- Annual appliances, lighting, cooking and DHW remain BREDEM/SAP-based. Normalized hourly profiles distribute these annual quantities; raw generator sums are not separate final estimates.
- DHW distinguishes useful tap heat, main-system heat including losses, and electric-shower electricity. The configured daily DHW profile is normalized to the annual quantities. Do not apply HP COP to the separately counted electric showers.
- New electrification scenarios retain their pre-existing Watson space-heat reshaping/uplift. This assumption applies only to newly converted homes, not again to existing heat pumps. Scenario annual electricity uses the same hourly COP path.
- Retrofit, PV, finance and carbon assumptions are retained historical project inputs, not updated market or policy advice. Detailed retrofit-cost/incentive calculations retain the original planning-ward scope.
- Legacy calibration modules and fixed-factor configuration fields remain only for historical compatibility/reference; they are not used by the new notebook chain. Legacy scenario/app entry points were archived.

See `SCENARIO_NOTES.md` for the distinction between borough and planning-ward results, limited PV-input coverage, and gross hourly demand versus annual PV offsets.

## Run

Use the existing conda `pypsa` environment:

```text
python -B run_all.py
python -B run_all.py 06 09
python -B run_all.py 10 11 12
```

The default sequence is 05, 06, 07, 09, 10, 11, 12. `--upstream` also runs 00–04; do not use it unless intentionally rebuilding the retained inputs. The engine uses up to 28 processes with one BLAS thread each. Thermal checkpoints are accepted only when the source/configuration fingerprint matches; changed numerical assumptions require an explicitly archived new run, not mixed caches.

The baseline comprises 306 stochastic annual simulations and 102 deterministic references. R1 and R2 each use 306 paired stochastic simulations. This is an archetype-tier expansion, not 57,300 independent simulations.

## Reading the results

WAPE and signed bias are meter-count-weighted discrepancies in area means. Model means are per eligible dwelling; observed means are per consuming meter. No low-performing areas are removed. The Canet-inspired plot reproduces the style of error-distribution presentation, not Canet's exact heat boundary or sign convention.

Hourly electricity peaks are simultaneous stock totals. They have not been validated against hourly metering. The ASHP curve omits defrost, backup, cycling and part-load effects and does not resolve GSHPs. Three draws per representative do not establish behavioural convergence. Annual observations were already inspected during development; this is not untouched holdout validation.

## Preservation

`_archive/20260918_before_unified/` contains the full recoverable pre-change project (including its old results, notebooks and applications). Retired live notebooks and outputs are additionally separated beneath its `retired_live/` directory. No original stock data or household-generator logic was intentionally changed. Separate experimental folders outside this main project are historical comparisons, not current dependencies.
