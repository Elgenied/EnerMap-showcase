# Interpretation of saved results

This review snapshot presents existing exploratory Guildford results. It does not rerun or certify the model.

* Both gas and electricity enter the calibration objective. Electricity agreement is fitted performance, even where older notebook prose calls it a prediction.
* Spatial refits use a full-data warm start. Their pooled scores are not a fully independent spatial validation.
* The source audit identified unresolved thermal-capacity unit and thermal-bridge implementation discrepancies. Reconciliation and rerunning are needed before treating the baseline and dependent scenarios as verified. Copying code for review does not fix these numerical issues.
* Building and archetype counts can differ between older exploratory outputs and the later calibrated run. The later design uses 34 construction groups, 102 size representatives and three household realisations. Three realisations do not demonstrate uncertainty convergence.
* The borough stock, calibration population and ten-ward planning population have different membership. Use the denominator attached to each result.
* OSM road-proximity classification is a separate exploration. Its outputs are not imported into the inspected MasterMap/EPC stock assembly.
* PV calculations start from an existing annual irradiation raster; full LiDAR/irradiation provenance is not reproduced here. Annual heat-pump SPF and hourly COP follow different assumptions.
* Peak values cover included residential loads. They exclude electric DHW, vehicles, non-residential demand and PV coincidence. Ward scenario peaks should be compared, not stacked. Nearest-substation assignment is spatial inference, not verified connectivity.
* Fabric flexibility is a passive thermal indicator, not demonstrated dispatch. The PyPSA notebook exports an interface; it does not complete an energy-system optimisation.
* Socioeconomic variables are area-level context. Synthesised dwelling attributes and incentive eligibility proxies are not observations at individual addresses. Financial comparisons mix historical prices with the stated incentive assumptions.

These points make useful supervisor discussion topics alongside the methods, saved charts and code.
