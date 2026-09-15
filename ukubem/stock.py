"""
The stock inputs of EnerMap (`data/inputs/`, see data/inputs/README.md) in the tool's column names.

  stock/stock_table.parquet            one row per dwelling: ids and geography, resolved attributes with provenance,
                                       building geometry, U-values, dating flags (the schema is documented in the README)
  stock/study_area_population.parquet  the dwellings of the planning study area: ward, rooftop PV potential
  stock/epc_certificate_figures.parquet optional certificate figures (energy, CO2, rating, heating cost)
  social/dwelling_social_context.parquet optional LSOA social context per dwelling (IMD decile, fuel poverty)
  benchmarks/desnz_lsoa_2021.csv       DESNZ sub-national gas and electricity per LSOA (meters and means)
  benchmarks/spatial_cv_folds_lsoa.csv spatial cross-validation bands of the LSOAs
  spatial/*.gpkg, spatial/lsoa/*.shp   footprints (incl. non-residential), wards, study-area boundary, LSOAs, PV potential
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# stock-table column -> tool column (identity where the name is unchanged)
FIELD_MAP = {
    "dwelling_id": "dwelling_id", "UPRN": "UPRN", "toid": "toid", "LSOA": "LSOA", "postcode": "postcode",
    "lon": "lon", "lat": "lat", "attributes_observed": "attributes_observed", "link_method": "link_method",
    "floor_area_prov": "floor_area_source",
    "accommodation_type": "accommodation_type", "built_form_resolved": "built_form",
    "heat_system_resolved": "heat_system", "wall_construction_resolved": "wall_construction_resolved",
    "wall_insulation_resolved": "wall_insulation", "glazing_type_resolved": "glazing_type",
    "roof_type_resolved": "roof_type", "tenure_resolved": "tenure", "age_band_resolved": "age_band_resolved",
    "floor_area_final": "floor_area_final", "area_m2": "area_m2", "storeys_est": "storeys_est",
    "height_eaves_m": "height_eaves_m", "height_ridge_m": "height_ridge_m", "volume_m3": "volume_m3",
    "uprn_count": "uprn_count", "is_flat_roof": "is_flat_roof",
    "wall_U": "wall_U", "roof_U": "roof_U", "window_U": "window_U", "window_SHGC": "window_SHGC", "window_Tvis": "window_Tvis",
    # provenance, dating, gas connection, flat position, typology labels
    "wall_U_prov": "wall_U_prov", "roof_U_prov": "roof_U_prov", "window_U_prov": "window_U_prov",
    "age_band_prov": "age_band_prov", "evidence_vintage": "evidence_vintage",
    "in_calibration_population": "in_calibration_population", "flat_top_storey": "flat_top_storey",
    "mains_gas_flag": "mains_gas_flag", "main_fuel": "main_fuel", "typology": "typology",
    "t_form": "t_form", "t_era": "t_era", "t_wall": "t_wall", "t_heat": "t_heat",
}
AGE_BAND_ORD = {b: i for i, b in enumerate("ABCDEFGHIJKL", start=1)}
AGE_BAND_LABEL = {"A": "pre-1900", "B": "1900-29", "C": "1930-49", "D": "1950-66", "E": "1967-75", "F": "1976-82",
                  "G": "1983-90", "H": "1991-95", "I": "1996-2002", "J": "2003-06", "K": "2007-11", "L": "2012+"}


def paths(cfg) -> dict:
    return {
        "stock": Path(cfg.STOCK_TABLE), "study_area": Path(cfg.STUDY_AREA_POPULATION),
        "certificates": Path(cfg.EPC_CERTIFICATE_FIGURES), "social": Path(cfg.SOCIAL_CONTEXT),
        "folds": Path(cfg.CV_FOLDS), "bench_lsoa": Path(cfg.BENCH_LSOA_RAW),
        "buildings": Path(cfg.BUILDINGS_GPKG), "wards": Path(cfg.WARDS_GPKG), "town": Path(cfg.TOWN_GPKG),
        "lsoas": Path(cfg.LSOA_SHP), "pv_buildings": Path(cfg.PV_BUILDINGS_GPKG), "pv_addresses": Path(cfg.PV_ADDRESSES_CSV),
        "network_buildings": Path(cfg.NETWORK_BUILDINGS_CSV), "substations": Path(cfg.SUBSTATIONS_CSV),
    }


def load_stock(cfg) -> pd.DataFrame:
    """The stock table in the tool's column names, plus ward / PV / fold / social-context / certificate extras."""
    P = paths(cfg)
    s = pd.read_parquet(P["stock"])
    missing = [c for c in FIELD_MAP if c not in s.columns]
    if missing:
        raise KeyError(f"stock table lacks columns {missing}; see data/inputs/README.md")
    df = s[list(FIELD_MAP)].rename(columns=FIELD_MAP).copy()
    df["tenure"] = df["tenure"].astype("object").str.lower().replace({"nan": "unknown"}).fillna("unknown")
    df["heat_system"] = df["heat_system"].fillna("Unknown")
    df["age_band_ord"] = df["age_band_resolved"].map(AGE_BAND_ORD).astype(float)
    df["is_flat_roof"] = df["is_flat_roof"].fillna(False).astype(bool)
    df["roof_exposed"] = df["roof_U"].notna().astype(int)                 # a null roof U-value means a dwelling above
    df["gas_connected"] = df["mains_gas_flag"].astype(str).str.upper().eq("Y")
    # ward, rooftop PV potential and the study-area flag (dwellings outside the study area stay NaN / False)
    u = pd.read_parquet(P["study_area"], columns=["dwelling_id", "ward", "ward_code", "pv_annual_kWh", "pv_kWp", "in_urban_10wards"])
    df = df.merge(u.rename(columns={"in_urban_10wards": "in_study_area"}), on="dwelling_id", how="left")
    df["in_study_area"] = df["in_study_area"].fillna(False).astype(bool)
    df["pv_annual_kWh"] = df["pv_annual_kWh"].fillna(0.0); df["pv_kWp"] = df["pv_kWp"].fillna(0.0)
    # spatial cross-validation band of the LSOA
    folds = pd.read_csv(P["folds"]); df = df.merge(folds.rename(columns={"fold": "cv_fold"}), on="LSOA", how="left")
    # optional: LSOA social context and certificate figures
    if P["social"].exists():
        df = df.merge(pd.read_parquet(P["social"], columns=["dwelling_id", "social_context"]), on="dwelling_id", how="left")
    else:
        df["social_context"] = np.nan
    if P["certificates"].exists():
        df = df.merge(pd.read_parquet(P["certificates"]), on="dwelling_id", how="left")
    df["construction_sa"] = df["typology"]; df["systems_sa"] = df["t_heat"]; df["loads_sa"] = "none"
    return df


def load_benchmark_lsoa(cfg) -> pd.DataFrame:
    """DESNZ LSOA benchmark (gas + electricity meters and means) in the tool's column names."""
    return pd.read_csv(paths(cfg)["bench_lsoa"]).rename(columns={"lsoa": "LSOA"})


def load_folds(cfg) -> pd.DataFrame:
    return pd.read_csv(paths(cfg)["folds"])


def load_spatial(cfg, layers=("buildings", "wards", "town", "lsoas", "pv_buildings")) -> dict:
    """GeoDataFrames (EPSG:27700): all footprints incl. non-residential, wards, study-area boundary, LSOAs, PV per building."""
    import geopandas as gpd
    P = paths(cfg)
    out = {}
    for k in layers:
        if P[k].exists():
            g = gpd.read_file(P[k])
            out[k] = g.to_crs(27700) if g.crs is not None and g.crs.to_epsg() != 27700 else g
    return out


# backwards-compatible names used by earlier notebooks
load_v2_stock = load_stock
load_v2_spatial = load_spatial
load_v2_folds = load_folds
