# Scenario scope and interpretation

The scenario energy calculations cover all 57,300 modelled borough dwellings. The separately labelled planning-ward subtotal covers 28,663 dwellings. These are nested scopes, not quantities to add together.

Fabric retrofit uses paired R1/R2 thermal simulations, with the same household draws as R0. New heat-pump conversions retain the existing configured heating-profile redistribution and 1.08 demand uplift; therefore, an electrification scenario can have more useful space heat than the baseline. This is a declared scenario assumption, not additional heat created by the COP calculation. Existing heat pumps do not receive that uplift a second time.

PV is applied only where the retained rooftop-potential inputs provide capacity. Those inputs concern the original planning area; missing potential elsewhere is not evidence of no physical PV potential. Borough PV scenarios therefore do not represent an exhaustive borough-wide rooftop assessment.

Detailed capital costs and incentive proxies retain the original planning-ward scope. Prices, carbon factors and incentive assumptions have not been updated to present-day values. They are historical model assumptions, not current tariffs or confirmed grant eligibility.

Annual net grid electricity subtracts the configured annual PV self-consumption estimate. The exported stock-hourly electricity profiles are gross demand before PV offsets: do not interpret their peaks as post-PV grid-import peaks. Technology choices, hourly dispatch and capacity expansion have not been optimised in PyPSA; notebook 11 exports and tests the interface.
