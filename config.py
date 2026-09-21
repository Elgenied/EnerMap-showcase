"""
EnerMap - a UBEM tool for local area energy planning: shared configuration.

Every notebook starts with `from config import *`. Paths point only inside this folder:
    data/inputs/     what a local authority supplies (see data/inputs/README.md for the schema)
    parameters/      every modelling assumption, grouped by topic (see parameters/README.md)
    data/derived/    tables notebook 00 builds from the inputs
    outputs/         everything the notebooks write (tables, figures, the PyPSA-LAEP interface)
    deps/            the two bundled engines (pyBuildingEnergy ISO 52016 hourly model, pybuildingcluster)
To run the tool for another local authority replace the files in data/inputs/ and, where they differ, the
parameter files; nothing else refers to the study area by name except STUDY_AREA below.
"""
from pathlib import Path
import os

HERE = Path(__file__).resolve().parent
STUDY_AREA = "Guildford"                            # used in titles and file descriptions only
DEPS = HERE / "deps"
PBE_SRC = DEPS / "pyBuildingEnergy" / "src"         # ISO 52016-1 hourly engine (EURAC pyBuildingEnergy, patched: see ukubem/engine.py)
PBC_SRC = DEPS / "pybuildingcluster" / "src"        # clustering helpers (EURAC pybuildingcluster)

# ---------------------------------------------------------------- folders
DATA = HERE / "data"
INPUTS = DATA / "inputs"
DERIVED = DATA / "derived"
PARAMS = HERE / "parameters"
OUT = HERE / "outputs"
FIG = OUT / "figures"
FIG_COMMON = FIG                                    # alias kept for the archetype notebooks
CLUSTERS_DIR = OUT / "archetypes"                   # notebook 01: construction sub-archetypes x systems families
CLUSTERS_STRAT_DIR = CLUSTERS_DIR                   # alias
EXPORT_DIR = OUT / "representatives"                # notebook 02: engine inputs per dwelling + representatives
CURRENT_OUT = OUT / 'current'                        # single authoritative result tree
UBEM_OUT = CURRENT_OUT / 'baseline'
PYPSA_OUT = CURRENT_OUT / 'pypsa_interface'
for _d in (DERIVED, OUT, FIG, CLUSTERS_DIR, EXPORT_DIR, UBEM_OUT, PYPSA_OUT):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- inputs (read-only; replace for another area)
STOCK_TABLE = INPUTS / "stock" / "stock_table.parquet"                     # one row per dwelling: attributes, geometry, geography, provenance
STUDY_AREA_POPULATION = INPUTS / "stock" / "study_area_population.parquet" # dwellings of the planning study area: ward, rooftop PV potential
EPC_CERTIFICATE_FIGURES = INPUTS / "stock" / "epc_certificate_figures.parquet"  # optional: certificate energy / cost / rating figures
SOCIAL_CONTEXT = INPUTS / "social" / "dwelling_social_context.parquet"    # optional: LSOA deprivation / fuel-poverty context per dwelling
LSOA_SOCIAL_CSV = INPUTS / "social" / "lsoa_social_context.csv"            # optional: IMD decile and fuel-poverty share per LSOA (incentive eligibility, equity maps)
BENCH_LSOA_RAW = INPUTS / "benchmarks" / "desnz_lsoa_2021.csv"             # DESNZ sub-national gas + electricity per LSOA (meters, means)
CV_FOLDS = INPUTS / "benchmarks" / "spatial_cv_folds_lsoa.csv"             # spatial cross-validation bands of the LSOAs
BUILDINGS_GPKG = INPUTS / "spatial" / "buildings.gpkg"                     # every footprint incl. non-residential (EPSG:27700)
WARDS_GPKG = INPUTS / "spatial" / "wards.gpkg"                             # the planning study area's wards
TOWN_GPKG = INPUTS / "spatial" / "study_area_boundary.gpkg"                # the study-area boundary
LSOA_SHP = INPUTS / "spatial" / "lsoa" / "lsoa.shp"                        # LSOA polygons of the authority
PV_BUILDINGS_GPKG = INPUTS / "spatial" / "pv_building_potential.gpkg"      # rooftop PV potential per building
PV_ADDRESSES_CSV = INPUTS / "spatial" / "pv_address_potential.csv"
NETWORK_BUILDINGS_CSV = INPUTS / "network" / "buildings_nearest_substation.csv"  # building -> nearest secondary substation (Voronoi cells)
SUBSTATIONS_CSV = INPUTS / "network" / "substations.csv"                   # secondary substations: location, rating, customers
SUBSTATIONS_GEOJSON = INPUTS / "network" / "secondary_substations.geojson"  # the same substations as points (maps)
EPW = INPUTS / "weather" / "GBR_ENG_Farnborough.AP.037680_TMYx.2007-2021.epw"  # typical meteorological year (EnergyPlus format)

# ---------------------------------------------------------------- derived tables (notebook 00)
TABLE_NUMERIC = DERIVED / "stock_numeric.parquet"        # numeric attributes per dwelling (clustering + engine inputs)
TABLE_NUMERIC_CSV = DERIVED / "stock_numeric.csv"
TABLE_LABELS = DERIVED / "stock_labels.parquet"          # ids + categorical labels + geography + provenance
BENCH_LSOA = DERIVED / "benchmark_lsoa_2021.csv"         # DESNZ LSOA benchmark in the tool's column names
DATA_WITH_CLUSTERS = CLUSTERS_DIR / "data_with_clusters.parquet"
CLUSTER_LABELS_CSV = CLUSTERS_DIR / "cluster_labels.csv"
ARCHETYPE_LOOKUP = CLUSTERS_DIR / "archetype_lookup.csv"  # cluster id -> archetype name
# stock columns carried from notebook 00 through the labels table into the engine export
EXTRA_LABELS = ["in_calibration_population", "evidence_vintage", "cv_fold", "mains_gas_flag", "gas_connected",
                "flat_top_storey", "ward_code", "pv_kWp", "pv_annual_kWh", "wall_U_prov", "roof_U_prov",
                "window_U_prov", "age_band_prov", "typology", "occupancy_N", "qhnd_imputed"]
V2_EXTRA_LABELS = EXTRA_LABELS                            # alias

# ---------------------------------------------------------------- modelling choices
SEED = 42
N_JOBS = min(28, max(1, (os.cpu_count() or 2) - 4))    # one BLAS thread per worker

# --- notebook 01 (construction sub-archetypes per built-form stratum, after Li & Dogan 2025) --------
STRATA_COL = "accommodation_type"
STRATA = ["Detached", "Semi-detached", "Terraced", "Flat"]   # clustered; any other type -> one sub-archetype each
STRATA_ABBREV = {"Detached": "Det", "Semi-detached": "Semi", "Terraced": "Ter", "Flat": "Flat",
                 "Caravan or mobile": "Park", "Unknown": "Unk"}
MIN_STRATUM_OBS = 150                  # below this a stratum gets a single sub-archetype

# Theme 1 - CONSTRUCTION (envelope): attributes every dwelling of the stock carries. Floor area, storeys and
# heights are building-specific geometry inputs, never archetype properties.
CONSTRUCTION_CAT = ["wall_construction_resolved", "wall_insulation", "glazing_type", "roof_type"]
CONSTRUCTION_NUM = ["age_band_ord", "wall_U", "roof_U", "window_U"]
CONSTRUCTION_LOG = []
# Theme 2 - SYSTEMS: the stock's heating-system attribute gives deterministic families.
SYSTEMS_FAMILY = {
    "Gas boiler": "GasBoiler", "Gas communal heating": "GasCommunal", "Oil boiler": "OilBoiler",
    "Bottled gas boiler": "LPGBoiler", "Solid fuel boiler": "SolidFuel",
    "Resistive heating": "ElecResistive", "Storage heater": "ElecStorage", "Heat pump": "HeatPump",
    "Unknown": "GasBoiler",            # modal system
}
# Theme 3 - ENERGY LOADS: not clustered (certificates carry no surveyed usage); household behaviour is
# sampled from the CHAP microdata; demand is validated against meters without fitting.

# selection rule per stratum with a stability gate
K_RANGE_STRATUM = (2, 12)              # candidate k per stratum, both K-Means and GMM-diag
MIN_CLUSTER_SIZE = 300                 # a solution with a smaller cluster is rejected
STABILITY_MIN = 0.80                   # bootstrap adjusted Rand index gate (von Luxburg 2010)
STABILITY_BOOT = 5
STABILITY_FRAC = 0.80
SILHOUETTE_CAP = 10_000
K_OVERRIDES = {}                       # e.g. {"Flat": ("kmeans", 6)} to force a stratum's solution

# "known attributes" for the assignability diagnostic: what an authority knows about a dwelling with no certificate
KNOWN_CAT = ["built_form", "tenure", "is_flat_roof"]
KNOWN_NUM = ["age_band_ord", "floor_area_final", "storeys_est", "height_eaves_m", "area_m2", "uprn_count"]
KNOWN_LOG = ["floor_area_final", "area_m2", "uprn_count"]
PRED_ACC_REF = 0.70

# categorical / numeric attribute lists (stock table columns)
CAT_COLS = ["accommodation_type", "built_form", "heat_system", "wall_construction_resolved",
            "wall_insulation", "glazing_type", "roof_type", "tenure"]
NUM_COLS = ["floor_area_final", "area_m2", "storeys_est", "height_eaves_m", "height_ridge_m",
            "volume_m3", "uprn_count", "age_band_ord", "wall_U", "roof_U", "window_U",
            "window_SHGC", "window_Tvis", "lon", "lat"]

# RdSAP age bands (England & Wales) for naming
AGE_BAND_LABEL = {"A": "pre-1900", "B": "1900-29", "C": "1930-49", "D": "1950-66", "E": "1967-75",
                  "F": "1976-82", "G": "1983-90", "H": "1991-95", "I": "1996-2002", "J": "2003-06", "K": "2007-11", "L": "2012+"}
