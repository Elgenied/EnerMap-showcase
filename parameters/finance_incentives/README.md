# finance_incentives

| file | what it holds | used by |
|---|---|---|
| `finance.json` | horizon, acceptable payback, priority-group quantiles | `ukubem/measures.py` `economics()`, `rank_groups()` |
| `incentives_2026.json` | **active** incentive rules (since 9 October 2026): Boiler Upgrade Scheme (heat pump, limited to its cost, oil/LPG top-up), low-income fabric grant (ECO4 / Warm Homes: Local Grant), social housing grant (Warm Homes: Social Housing Fund) and GBIS, with eligibility by tenure, EPC band and LSOA deprivation | `ukubem/measures.py` `epc_bands()`, `incentives()`; notebook 10 |
| `incentives.json` | previous incentive proxies (BUS, ECO4, GBIS); retained unchanged for provenance and the preservation check in `tests/verify_current.py`; not used | — |

The EPC band comes from the certificate SAP score (`data/inputs/stock/epc_certificate_figures.parquet`) or, where a dwelling has no certificate, the median SAP of certified dwellings of the same construction archetype and age band. Incentives reduce upfront cost only; they do not change energy, carbon or the PyPSA-LAEP interface. The previous result set is preserved in `_archive/20261009_before_incentive_revision`.
