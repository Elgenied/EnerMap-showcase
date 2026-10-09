# Scenario scope and interpretation

The scenario energy calculations cover all 57,300 modelled borough dwellings. The separately labelled planning-ward subtotal covers 28,663 dwellings. These are nested scopes, not quantities to add together.

Fabric retrofit uses paired R1/R2 thermal simulations, with the same household draws as R0. New heat-pump conversions retain the existing configured heating-profile redistribution and 1.08 demand uplift; therefore, an electrification scenario can have more useful space heat than the baseline. This is a declared scenario assumption, not additional heat created by the COP calculation. Existing heat pumps do not receive that uplift a second time.

PV is applied only where the retained rooftop-potential inputs provide capacity. Those inputs concern the original planning area; missing potential elsewhere is not evidence of no physical PV potential. Borough PV scenarios therefore do not represent an exhaustive borough-wide rooftop assessment.

Detailed capital costs and incentives retain the original planning-ward scope. Prices and carbon factors have not been updated to present-day values. Incentives follow the 2025–26 scheme rules in `parameters/finance_incentives/incentives_2026.json` on the 2021 cost basis (see the incentive update below); they are a simplified representation, not confirmed grant eligibility.

Annual net grid electricity subtracts the configured annual PV self-consumption estimate. The exported stock-hourly electricity profiles are gross demand before PV offsets: do not interpret their peaks as post-PV grid-import peaks. Technology choices, hourly dispatch and capacity expansion have not been optimised in PyPSA; notebook 11 exports and tests the interface.

## Representative construction update (23 September 2026)

The demand library uses cluster-median construction profiles at exact median S/M/L floor areas, with three household draws per size. R0, R1 and R2 are generated from the same virtual baseline; retrofit measures are applied after constructing that baseline, not before recomputing medians. Source template IDs preserve reproducibility, not actual-dwelling construction values. Whole-stock expansion retains each dwelling's original floor area and heating system.

Individual retrofit eligibility, cost quantities, sizing and flexibility continue to use completed-stock attributes. These outputs have individual-stock detail, while simulated demand savings use a shared construction/size profile. Within-group eligibility and exposure variation are therefore not independently simulated. This approximation is retained rather than silently overwriting individual stock inputs with group medians.

## Incentive update (9 October 2026)

Incentives are applied after the energy simulation and reduce upfront cost only; energy, bills, carbon and the PyPSA interface are unchanged. Four schemes are represented: the Boiler Upgrade Scheme (GBP 7,500, plus GBP 1,500 for oil/LPG homes, limited to the heat-pump cost; not social housing), a low-income fabric grant representing ECO4 and the Warm Homes: Local Grant (90 % of insulation and draught-proofing cost up to GBP 10,000; not social housing, EPC D–G, LSOA fuel poverty at least 12 % or IMD decile 3 or below), a social housing grant representing the Warm Homes: Social Housing Fund (50 % of fabric and heat-pump cost up to GBP 7,500; social rented, EPC D–G) and GBIS (75 % of cavity and loft cost up to GBP 1,500; not social housing, EPC D–G, under 120 m², not low-income eligible). Each measure is funded by at most one scheme. The EPC band comes from the certificate SAP score or, for the 29 % of borough dwellings (26 % in the planning wards) without one, the median SAP of certified dwellings of the same archetype and age band (77 % correct D–G versus A–C on a 20 % hold-out). The previous result set is preserved in `_archive/20261009_before_incentive_revision`.

## Hourly infrastructure extension

Notebook 14 compares all six scenarios using 28,662 residential allocations in 271 inferred secondary-substation cells; one unassigned dwelling is excluded. Imports and exports are summed separately across cells. Four-kWp capped rooftop yields are distributed using a south-facing 35° annual-normalised hourly profile. No storage, clipping or export curtailment is modelled. Reported ratings × 0.95 provide a screening threshold, not verified hosting capacity. The incentive rerun leaves these physical results unchanged.

The active incentive configuration is a study representation. Area-level deprivation, fuel poverty and imputed EPC bands do not establish household entitlement. Operating prices and carbon factors retain the 2021 basis.
