"""
ukubem - a UK local-authority UBEM layer on top of EURAC's pyBuildingEnergy (EN ISO 52016-1).

Modules
-------
engine     pyBuildingEnergy adapter: one-off weather/solar pre-processing shared by every
           dwelling at a site, quiet parallel runs, annual summaries.
geometry   dwelling record (EPC + OS geometry + archetype labels) -> pyBuildingEnergy `BUI`
           dictionary, using the conventions of parameters/demand_model/geometry_defaults.json (plan aspect 1.5,
           party-wall fractions by built form, flats sharing roof/ground, WWR 0.25, RdSAP age
           bands for floor U and infiltration, ISO 13790 thermal-mass classes).
schedules  household sampling and CREST/CHAP-type stochastic occupancy, heating regime,
           internal gains and DHW schedules (wraps `uk_ubem_schedule_generator`).
systems    heating-system families -> delivered gas / electricity / other fuel.
runner     stock runs on all cores with checkpointing, LSOA aggregation.
"""
from .engine import PBE, run_building, annual_summary          # noqa: F401
from .geometry import UKGeometryAssumptions, build_bui           # noqa: F401
from .schedules import sample_household, dwelling_schedule       # noqa: F401
from .systems import delivered_energy, SYSTEM_DEFAULTS           # noqa: F401
