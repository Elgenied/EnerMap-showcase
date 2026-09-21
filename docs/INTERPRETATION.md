# Interpretation of the current results

This snapshot uses the completed 18 September 2026 engine run. Gas WAPE is 7.28% and electricity WAPE is 11.21%. No calibration or spatial refitting is used. Observations were inspected during development; this is not untouched holdout validation.

The rerun corrected active-surface thermal-capacity units, configured thermal bridges, nested internal-gain overrides, explicit no-ground geometry and heat-balance reporting. Earlier statements that these implementation discrepancies await a rerun describe the superseded version. Passing implementation checks is distinct from validating every physical assumption.

The baseline retains 57,300 dwellings, 34 construction groups, 102 size representatives and three household draws per representative. The 306 stochastic runs drive stock expansion; 102 deterministic reference runs are separate. R1/R2 each use 306 paired stochastic runs. Three draws do not establish uncertainty convergence.

Space heat comes from the hourly thermal model. Annual BREDEM/SAP usage still defines appliances, lighting, cooking and hot water; normalized profiles distribute those annual quantities. Useful tap heat, system-side DHW heat including losses and electric-shower electricity are separate quantities. Existing heat pumps use the hourly ASHP curve for space heat and hot water, with annual electricity obtained by summing hourly values. The framework does not rescale HP electricity to a fixed seasonal factor.

WAPE and signed bias use meter-count-weighted area means. Observed means are per consuming meter and modelled means per eligible dwelling. All 84 available LSOAs are retained. Annual validation does not establish individual-building or hourly-peak accuracy. The Canet-inspired figure is a presentation of this study's delivered-fuel errors, not a replication of Canet's heat boundary or sign convention.

Planning scenarios default to the dense ten-ward case study (28,663 dwellings). Borough totals use 57,300 dwellings. These nested populations must not be added together. Gross hourly electricity peaks cover the borough, before PV offsets. They are simultaneous sums, not sums of each area's separate maximum. Annual PV offsets do not define hourly grid imports. PV-input coverage, historical cost/carbon assumptions and new-conversion uplift are explained in [scenario notes](SCENARIO_NOTES.md).

Legacy calibration modules and seasonal-factor settings remain in the inspectable source for compatibility. Their presence does not make them active stages of the current run. In particular, older explanatory notes in cop_model.json describe former seasonal accounting; the active path is documented in ENGINE_WORKFLOW.md and implemented in unified.py/accounting.py.

Old calibration notebooks, tables and scenario graphics are removed from the current showcase tree and remain recoverable in Git history. Current figures and aggregate tables have source hashes in snapshot_manifest.json. The showcase does not include raw household microdata or address-level model data. The separate road-proximity experiment is not an integrated step in the footprint-to-EPC pipeline.
