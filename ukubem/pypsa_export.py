"""
The PyPSA-LAEP interface: what the optimiser needs from EnerMap UBEM, in the agreed files.

1. `building_cluster_map.csv` + `cluster_summary.csv`  - every modelled building -> cluster (thermal archetype x
   retrofit-eligibility class x electricity supply zone), ward, dwelling count, heated floor area, shared block,
   and HOW the zone was assigned (a nearest-substation Voronoi inference is labelled as such, never as a verified
   connection).
2. `retrofit_options.csv` + `measure_sets.json` + `thermal_parameter_sets.json` - per cluster the baseline R0 and
   the feasible packages R1 (light fabric) / R2 (deep fabric) with construction-based eligibility, the explicit
   measure list and the modified building parameters (before / after U-values, infiltration, ventilation).
   Packages are simulated as COMBINATIONS (the representatives are re-run with the whole package applied).
3. `retrofit_quantities.csv` - the physical quantities that determine the bill: exposed opaque wall area, roof
   area, floor area, window area, dwellings, buildings or blocks needing shared enabling works, with the cost
   scope (dwelling / building) and the quantity source.
4. `demand_profiles.parquet` - hourly USEFUL demand per cluster and package: space heat, hot water and non-heating
   electricity as three distinct vectors (kW, average power over the hour), on one shared time axis; plus a
   compact `demand_profile_library.parquet` (per archetype x eligibility class x package, per m2) with the cluster
   weights, from which the long table is an exact linear combination.

Boundaries, stated once here and in `README_pypsa_interface.md`:
* space heat = useful heat delivered to the zone by the ISO 52016 model with the calibrated physics (weather
  scaler 1.0, TMY Farnborough), positive only, no system efficiency;
* hot water = useful energy at the tap (BREDEM/SAP volume 46 + 26 N litres/day at 37 K rise, no cylinder or
  distribution losses, no efficiency), hourly shape from the CHAP hot-water draws of the household;
* non-heating electricity = BREDEM appliances + lighting + electric cooking, hourly shape from the CHAP activity
  model; it excludes any electricity for heat that PyPSA will model itself (heat pumps, resistive heating,
  electric showers and immersion heaters are NOT included - the hot-water vector carries that energy as heat).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import measures

R_PACKAGES = {
    "R0": {"name": "baseline", "measures": []},
    "R1": {"name": "light fabric retrofit", "measures": ["loft_topup", "draught_proofing", "cavity_fill"]},
    "R2": {"name": "deep fabric retrofit", "measures": ["cavity_fill", "solid_wall_iwi", "loft_topup", "flat_roof", "glazing_upgrade", "draught_proofing"]},
}
MEASURE_QUANTITY = {   # measure -> (quantity column of the envelope table, unit, source, cost scope)
    "cavity_fill": ("A_wall", "m2", "geometry_derived", "dwelling"),
    "solid_wall_iwi": ("A_wall", "m2", "geometry_derived", "dwelling"),
    "loft_topup": ("A_roof", "m2", "geometry_derived", "building"),
    "flat_roof": ("A_roof", "m2", "geometry_derived", "building"),
    "glazing_upgrade": ("A_window", "m2", "geometry_derived", "dwelling"),
    "draught_proofing": (None, "dwelling", "count", "dwelling"),
}
SHARED_WORKS_MEASURES = ["solid_wall_iwi", "loft_topup", "flat_roof", "cavity_fill"]   # scaffold / roof access on blocks of flats


def _slug(x) -> str:
    return re.sub(r"[^A-Za-z0-9_.:+-]+", "_", str(x)).strip("_")


# --------------------------------------------------------------------------- 1. clusters
def eligibility_class(inputs: pd.DataFrame, target_set: str = "planning") -> pd.Series:
    """Compact retrofit-eligibility signature per dwelling (wall measure, loft, flat roof, glazing)."""
    e = measures.eligibility(inputs, target_set)
    wall = np.where(e["cavity_fill"], "cav", np.where(e["solid_wall_iwi"], "sol", "none"))
    return pd.Series([f"W{w}-L{int(l)}-F{int(f)}-G{int(g)}" for w, l, f, g in zip(wall, e["loft_topup"], e["flat_roof"], e["glazing_upgrade"])],
                     index=inputs.index, name="eligibility_class")


def electricity_zones(inputs: pd.DataFrame, cfg) -> pd.DataFrame:
    """
    Electricity supply zone per dwelling. Study-area dwellings: the nearest-secondary-substation (Voronoi)
    assignment -> `network_assignment_method = "spatial_inference_nearest_substation"`.
    Dwellings without such an assignment: an assumed zone per LSOA (`"assumed_zone_lsoa"`).
    """
    qb_file = Path(cfg.NETWORK_BUILDINGS_CSV)          # building -> nearest secondary substation (Voronoi cells)
    sub_file = Path(cfg.SUBSTATIONS_CSV)
    z = pd.DataFrame(index=inputs.index)
    z["electricity_zone_id"] = "LSOA:" + inputs["LSOA"].astype(str)
    z["network_assignment_method"] = "assumed_zone_lsoa"
    z["zone_rating_kVA"] = np.nan; z["zone_x"] = np.nan; z["zone_y"] = np.nan
    if qb_file.exists():
        qb = pd.read_csv(qb_file, usecols=["egid", "id_transformer"]).dropna()
        qb["UPRN"] = qb["egid"].astype("Int64").astype(str)
        m = inputs[["UPRN"]].copy(); m["UPRN"] = m["UPRN"].astype("Int64").astype(str)
        m = m.merge(qb[["UPRN", "id_transformer"]].drop_duplicates("UPRN"), on="UPRN", how="left"); m.index = inputs.index
        has = m["id_transformer"].notna()
        z.loc[has, "electricity_zone_id"] = "SUB:" + m.loc[has, "id_transformer"].astype(str)
        z.loc[has, "network_assignment_method"] = "spatial_inference_nearest_substation"
        if sub_file.exists():
            subs = pd.read_csv(sub_file)[["functionallocation", "onanrating", "easting", "northing"]].drop_duplicates("functionallocation").set_index("functionallocation")
            z.loc[has, "zone_rating_kVA"] = m.loc[has, "id_transformer"].map(subs["onanrating"]).to_numpy()
            z.loc[has, "zone_x"] = m.loc[has, "id_transformer"].map(subs["easting"]).to_numpy()
            z.loc[has, "zone_y"] = m.loc[has, "id_transformer"].map(subs["northing"]).to_numpy()
    return z


def build_clusters(inputs: pd.DataFrame, zones: pd.DataFrame, elig: pd.Series, archetype_col: str = "construction_sa",
                   resolution: str = "planning", min_dwellings: int = 10) -> tuple[pd.Series, pd.DataFrame]:
    """
    cluster_id = archetype | eligibility class | electricity zone.

    resolution "fine": the full eligibility signature (wall, loft, flat roof, glazing) - exact but very fragmented.
    resolution "planning" (default): the eligibility class is the WALL measure only (Wcav / Wsol / Wnone - the
    measure that decides the package's cost and saving), loft, flat-roof and glazing eligibility stay as shares
    inside the cluster (recorded in the options and quantities); and, inside every zone, archetype-wall groups with
    fewer than `min_dwellings` dwellings are pooled into one MIX-<wall> cluster of that zone. Pooling loses no
    demand information (profiles, quantities and packages are always built bottom-up from the member dwellings);
    only the archetype label of a pooled cluster becomes "MIX" with its composition kept in the summary.
    """
    arche = inputs[archetype_col].astype(str).map(_slug)
    key = elig.astype(str) if resolution == "fine" else elig.astype(str).str.split("-").str[0]
    zone = zones["electricity_zone_id"].astype(str)
    cid = arche + "|" + key + "|" + zone
    if resolution != "fine" and min_dwellings > 1:
        n = cid.map(cid.value_counts())
        small = n < min_dwellings
        cid = cid.where(~small, "MIX|" + key + "|" + zone)
    cid.name = "cluster_id"
    d = pd.DataFrame({"cluster_id": cid, "archetype_id": inputs[archetype_col].astype(str), "eligibility_class": key,
                      "electricity_zone_id": zones["electricity_zone_id"], "network_assignment_method": zones["network_assignment_method"],
                      "toid": inputs["toid"], "area": inputs["floor_area_final"], "ward": inputs["ward"], "ward_code": inputs.get("ward_code"),
                      "systems_fam": inputs["systems_fam"], "in_study_area": inputs["in_study_area"]})
    summ = d.groupby("cluster_id").agg(archetype_id=("archetype_id", lambda x: x.iloc[0] if x.nunique() == 1 else "MIX"),
                                        n_archetypes=("archetype_id", "nunique"),
                                        archetype_mix=("archetype_id", lambda x: ";".join(f"{k}:{v}" for k, v in x.value_counts().head(6).items())),
                                        eligibility_class=("eligibility_class", "first"),
                                        electricity_zone_id=("electricity_zone_id", "first"), network_assignment_method=("network_assignment_method", "first"),
                                        building_count=("toid", "nunique"), dwelling_count=("toid", "size"), heated_floor_area_m2=("area", "sum"),
                                        ward=("ward", lambda s: s.mode().iloc[0] if s.notna().any() else ""), in_ten_wards=("in_study_area", "mean"))
    fam = d.groupby(["cluster_id", "systems_fam"]).size().unstack(fill_value=0)
    summ = summ.join(fam.add_prefix("n_"))
    summ["heated_floor_area_m2"] = summ["heated_floor_area_m2"].round(1)
    return cid, summ.reset_index()


def building_cluster_map(inputs: pd.DataFrame, cid: pd.Series, zones: pd.DataFrame, elig: pd.Series) -> pd.DataFrame:
    """One row per (building footprint, cluster): the building record PyPSA maps onto a bus."""
    flat = inputs["accommodation_type"].astype(str).str.lower().str.startswith(("flat", "maisonette"))
    d = pd.DataFrame({"building_id": inputs["toid"].astype(str), "cluster_id": cid, "ward_code": inputs.get("ward_code"), "ward": inputs["ward"],
                      "lsoa": inputs["LSOA"], "electricity_zone_id": zones["electricity_zone_id"], "archetype_id": inputs["construction_sa"].astype(str),
                      "eligibility_class": cid.str.split("|").str[1], "area": inputs["floor_area_final"], "block_id": np.where(flat, inputs["toid"].astype(str), ""),
                      "network_assignment_method": zones["network_assignment_method"], "lon": inputs["lon"], "lat": inputs["lat"], "dwelling_id": inputs.index})
    g = d.groupby(["building_id", "cluster_id"]).agg(ward_code=("ward_code", "first"), ward=("ward", "first"), lsoa=("lsoa", "first"),
                                                      electricity_zone_id=("electricity_zone_id", "first"), archetype_id=("archetype_id", "first"),
                                                      eligibility_class=("eligibility_class", "first"), dwelling_count=("dwelling_id", "size"),
                                                      heated_floor_area_m2=("area", "sum"), block_id=("block_id", "first"),
                                                      network_assignment_method=("network_assignment_method", "first"), lon=("lon", "mean"), lat=("lat", "mean"),
                                                      dwelling_ids=("dwelling_id", lambda s: ";".join(map(str, s))))
    g["heated_floor_area_m2"] = g["heated_floor_area_m2"].round(1)
    return g.reset_index()


# --------------------------------------------------------------------------- 2. retrofit catalogue
def retrofit_options(inputs: pd.DataFrame, cid: pd.Series, areas: pd.DataFrame, target_set: str = "planning") -> tuple[pd.DataFrame, dict, dict]:
    """
    Options per cluster x package with eligibility and its basis, the measure sets (what changes, before/after
    parameters by measure) and the thermal parameter sets (area-weighted cluster envelope before / after).
    """
    T = measures.ASSUMPTIONS["targets"][target_set]
    e = measures.eligibility(inputs, target_set)
    a = areas.reindex(inputs.index)
    from .geometry import UKGeometryAssumptions
    G = UKGeometryAssumptions()
    ach0 = inputs["age_band_resolved"].astype(str).map(G.ach_infiltration_by_age).fillna(G.default_ach_infiltration)
    rows, msets, tsets = [], {}, {}
    for pkg, spec in R_PACKAGES.items():
        inp_s, applied = measures.apply_package(inputs, spec["measures"], target_set)
        for c, idx in cid.groupby(cid).groups.items():
            ap = applied.loc[idx]; el = e.loc[idx]
            applicable = [m for m in spec["measures"] if ap[m].any()]
            eligible = True if pkg == "R0" else len([m for m in applicable if m != "draught_proofing"]) > 0
            basis = "baseline" if pkg == "R0" else ("construction eligibility: " + ", ".join(f"{m} {int(ap[m].sum())}/{len(idx)} dwellings" for m in applicable)
                                                      if applicable else "no measure of the package applies to the construction of this cluster")
            ms_id = f"MS_{pkg}_{cid.loc[idx].iloc[0].split('|')[1]}"
            tp_id = f"TP_{_slug(c)}_{pkg}"
            w = a.loc[idx]
            def wavg(col_before, col_after, area_col):
                aw = w[area_col].fillna(0).to_numpy(); aw = aw if aw.sum() > 0 else np.ones(len(idx))
                return float(np.average(inputs.loc[idx, col_before], weights=aw)), float(np.average(inp_s.loc[idx, col_after], weights=aw))
            wall_b, wall_a = wavg("wall_U", "wall_U", "A_wall"); roof_b, roof_a = wavg("roof_U", "roof_U", "A_roof"); win_b, win_a = wavg("window_U", "window_U", "A_window")
            shgc_b, shgc_a = float(inputs.loc[idx, "window_SHGC"].mean()), float(inp_s.loc[idx, "window_SHGC"].mean())
            ach_b = float(ach0.loc[idx].mean()); ach_a = float((ach0.loc[idx] * inp_s.loc[idx, "ach_factor"]).mean())
            tsets[tp_id] = {"cluster_id": c, "package_id": pkg, "n_dwellings": int(len(idx)),
                            "wall_U_W_m2K": {"before": round(wall_b, 3), "after": round(wall_a, 3)},
                            "roof_U_W_m2K": {"before": round(roof_b, 3), "after": round(roof_a, 3)},
                            "window_U_W_m2K": {"before": round(win_b, 3), "after": round(win_a, 3)},
                            "window_g": {"before": round(shgc_b, 3), "after": round(shgc_a, 3)},
                            "infiltration_ach_at_natural_conditions": {"before": round(ach_b, 3), "after": round(ach_a, 3),
                                                                        "definition": "age-band infiltration air changes per hour (RdSAP-style), scaled by the draught-proofing factor; engine adds 0.35 ach ventilation"},
                            "ventilation": {"ach": G.ach_ventilation, "heat_recovery": 0.0, "fan_electricity_W": 0.0, "note": "natural ventilation, unchanged by the packages"},
                            "floor_U_W_m2K": {"before": round(float(inputs.loc[idx, "age_band_resolved"].astype(str).map(G.u_floor_by_age).fillna(G.default_u_floor).mean()), 3), "after": None,
                                              "note": "floor insulation is not in the packages"},
                            "calibrated_physics_scalers_applied": "yes (notebook 06 theta, identical for every package)"}
            rows.append({"cluster_id": c, "package_id": pkg, "package_name": spec["name"], "baseline_state_id": f"BS_{_slug(c)}_2021",
                         "measure_set_id": ms_id, "eligible": bool(eligible), "eligibility_basis": basis, "thermal_parameter_set_id": tp_id,
                         "n_dwellings": int(len(idx)), **{f"applies_{m}": int(ap[m].sum()) for m in spec["measures"]}})
            if ms_id not in msets:
                msets[ms_id] = {"package_id": pkg, "package_name": spec["name"], "eligibility_class": cid.loc[idx].iloc[0].split("|")[1],
                                "measures": {m: measure_definition(m, T) for m in spec["measures"]},
                                "combination_rule": "the package is simulated as one combination (representatives re-run with all measures applied); savings are never summed from single measures"}
    return pd.DataFrame(rows), msets, tsets


def measure_definition(m: str, T: dict) -> dict:
    d = {"cavity_fill": {"element": "cavity or system-built external walls, uninsulated / partial / unknown", "parameter": "wall U-value", "after_W_m2K": T["cavity_fill_U"], "unit_cost_basis": "per m2 exposed opaque wall"},
         "solid_wall_iwi": {"element": "solid brick / stone external walls not insulated", "parameter": "wall U-value (internal wall insulation)", "after_W_m2K": T["solid_wall_iwi_U"], "unit_cost_basis": "per m2 exposed opaque wall"},
         "loft_topup": {"element": "exposed pitched roof with U above the trigger", "parameter": "roof U-value", "after_W_m2K": T["loft_topup_U"], "eligible_above_W_m2K": T["loft_eligible_above_U"], "unit_cost_basis": "per m2 roof, building scope for flats"},
         "flat_roof": {"element": "exposed flat roof with U above the trigger", "parameter": "roof U-value", "after_W_m2K": T["flat_roof_U"], "eligible_above_W_m2K": T["flat_roof_eligible_above_U"], "unit_cost_basis": "per m2 roof, building scope for flats"},
         "glazing_upgrade": {"element": "single or secondary glazing", "parameter": "window U-value and g-value", "after_W_m2K": T["glazing_U"], "after_g": T["glazing_SHGC"], "unit_cost_basis": "per m2 window"},
         "draught_proofing": {"element": "whole dwelling", "parameter": "infiltration air change rate", "after_factor": T["draught_ach_factor"], "definition": "multiplier on the age-band infiltration ACH", "unit_cost_basis": "per dwelling"}}
    return d[m]


# --------------------------------------------------------------------------- 3. quantities
def retrofit_quantities(inputs: pd.DataFrame, cid: pd.Series, areas: pd.DataFrame, target_set: str = "planning") -> pd.DataFrame:
    a = areas.reindex(inputs.index)
    flat = inputs["accommodation_type"].astype(str).str.lower().str.startswith(("flat", "maisonette"))
    rows = []
    for pkg, spec in R_PACKAGES.items():
        if not spec["measures"]:
            continue
        _, applied = measures.apply_package(inputs, spec["measures"], target_set)
        for c, idx in cid.groupby(cid).groups.items():
            ap = applied.loc[idx]
            for m in spec["measures"]:
                sel = idx[ap[m].to_numpy()]
                if len(sel) == 0:
                    continue
                col, unit, source, scope = MEASURE_QUANTITY[m]
                qty = float(len(sel)) if col is None else float(a.loc[sel, col].fillna(0).sum())
                rows.append({"cluster_id": c, "package_id": pkg, "measure_id": m, "quantity": round(qty, 1), "unit": unit,
                             "cost_scope_id": scope, "quantity_source": source, "n_dwellings_applied": int(len(sel)),
                             "n_buildings_applied": int(inputs.loc[sel, "toid"].nunique())})
            shared = idx[(ap[[m for m in SHARED_WORKS_MEASURES if m in ap]].any(axis=1) & flat.loc[idx]).to_numpy()]
            if len(shared):
                rows.append({"cluster_id": c, "package_id": pkg, "measure_id": "shared_enabling_works", "quantity": float(inputs.loc[shared, "toid"].nunique()),
                             "unit": "building", "cost_scope_id": "building", "quantity_source": "geometry_derived",
                             "n_dwellings_applied": int(len(shared)), "n_buildings_applied": int(inputs.loc[shared, "toid"].nunique())})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- 4. hourly profiles
def group_profiles(Q: np.ndarray, meta: pd.DataFrame) -> dict:
    """Mean hourly space-heat profile per m2 for every (construction_sa, size_class) representative group."""
    meta = meta.copy(); meta["A_floor"] = meta["A_floor"].astype(float)
    Qm2 = np.clip(Q, 0, None) / meta["A_floor"].to_numpy()[:, None]
    return {g: Qm2[ii].mean(axis=0) for g, ii in meta.groupby(["construction_sa", "size_class"]).indices.items()}


def demand_profiles(inputs: pd.DataFrame, cid: pd.Series, rep_profiles: dict, dhw_shapes: dict, elec_shapes: dict, usage: dict,
                    case_id: str, index: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    rep_profiles: {package_id: {(construction_sa, size_class): W per m2 [8760]}} (space heat, useful);
    dhw_shapes / elec_shapes: {(construction_sa, size_class): shape summing to 1 [8760]};
    usage: dict of arrays aligned with `inputs` (dhw_useful_kWh, elec_nonheat_kWh).
    Returns (long table, profile library per archetype x eligibility x package per m2, weights per cluster).
    """
    d = pd.DataFrame({"cluster_id": cid, "sa": inputs["construction_sa"].astype(str), "size": inputs["size_class"].astype(str), "area": inputs["floor_area_final"].astype(float),
                      "dhw": np.asarray(usage["dhw_useful_kWh"], float), "elec": np.asarray(usage["elec_nonheat_kWh"], float)}, index=inputs.index)
    d["elig"] = d["cluster_id"].str.split("|").str[1]
    keys = list(rep_profiles["R0"].keys()); pos = {k: i for i, k in enumerate(keys)}
    d["gi"] = [pos.get((s, z), -1) for s, z in zip(d["sa"], d["size"])]
    ok = d["gi"] >= 0
    H = np.stack([rep_profiles["R0"][k] for k in keys])                                 # groups x 8760 (W/m2)
    D = np.stack([dhw_shapes.get(k, np.full(8760, 1 / 8760)) for k in keys]); E = np.stack([elec_shapes.get(k, np.full(8760, 1 / 8760)) for k in keys])
    long, lib, weights = [], [], []
    for c, sub in d[ok].groupby("cluster_id"):
        w_area = np.bincount(sub["gi"], weights=sub["area"], minlength=len(keys))            # m2 per group
        w_dhw = np.bincount(sub["gi"], weights=sub["dhw"], minlength=len(keys)); w_el = np.bincount(sub["gi"], weights=sub["elec"], minlength=len(keys))
        dhw_kw = (w_dhw[:, None] * D).sum(axis=0)                                         # kWh per hour = kW average
        el_kw = (w_el[:, None] * E).sum(axis=0)
        for pkg in R_PACKAGES:
            Hp = np.stack([rep_profiles[pkg][k] for k in keys])
            sh_kw = (w_area[:, None] * Hp).sum(axis=0) / 1000.0
            long.append(pd.DataFrame({"case_id": case_id, "timestamp_utc": index, "cluster_id": c, "package_id": pkg,
                                      "space_heat_kw": sh_kw.astype(np.float32), "hot_water_heat_kw": dhw_kw.astype(np.float32),
                                      "non_heating_electricity_kw": el_kw.astype(np.float32)}))
        weights.append({"cluster_id": c, **{f"m2_{k[0]}_{k[1]}": float(v) for k, v in zip(keys, w_area) if v > 0}})
    for pkg in R_PACKAGES:
        for k in keys:
            lib.append(pd.DataFrame({"case_id": case_id, "timestamp_utc": index, "construction_sa": k[0], "size_class": k[1], "package_id": pkg,
                                     "space_heat_w_per_m2": rep_profiles[pkg][k].astype(np.float32), "dhw_shape": dhw_shapes.get(k, np.full(8760, 1 / 8760)).astype(np.float32),
                                     "elec_shape": elec_shapes.get(k, np.full(8760, 1 / 8760)).astype(np.float32)}))
    return pd.concat(long, ignore_index=True), pd.concat(lib, ignore_index=True), pd.DataFrame(weights)


def interface_readme(out_dir: Path, counts: dict, case: dict) -> Path:
    txt = f"""# EnerMap UBEM -> PyPSA-LAEP demand interface

Generated by notebook 11 of EnerMap UBEM. Scope: {counts.get('dwellings', 0):,} dwellings in {counts.get('clusters', 0):,} clusters
({counts.get('buildings', 0):,} building records, {counts.get('zones', 0):,} electricity zones).

| file | one row per | key columns |
|---|---|---|
| building_cluster_map.csv | building footprint x cluster | building_id (OS TOID), cluster_id, ward_code, electricity_zone_id, archetype_id, dwelling_count, heated_floor_area_m2, block_id, network_assignment_method, lon, lat |
| cluster_summary.csv | cluster | cluster_id, archetype_id, eligibility_class, electricity_zone_id, building_count, dwelling_count, heated_floor_area_m2, heating-system counts |
| retrofit_options.csv | cluster x package | package_id (R0/R1/R2), package_name, baseline_state_id, measure_set_id, eligible, eligibility_basis, thermal_parameter_set_id |
| measure_sets.json | measure set | the measures of each package with before/after parameters and the combination rule |
| thermal_parameter_sets.json | cluster x package | area-weighted wall / roof / window U, g-value, infiltration ACH before and after, ventilation |
| retrofit_quantities.csv | cluster x package x measure | quantity, unit (m2 / dwelling / building), cost_scope_id, quantity_source |
| demand_profiles.parquet | cluster x package x hour | space_heat_kw, hot_water_heat_kw, non_heating_electricity_kw (average kW over the hour) |
| demand_profile_library.parquet | archetype x size x package x hour | space heat per m2 and the hot-water / electricity shapes the long table is built from |
| cluster_profile_weights.csv | cluster | heated floor area per (archetype, size) group |
| cases.csv | case | weather, occupancy and calibration assumptions behind `case_id` |

Cluster id = `<thermal archetype>|<retrofit eligibility class>|<electricity zone>`. An archetype behind two
transformers is two clusters; a cluster whose dwellings differ in what can be retrofitted is split by the
eligibility class (W = wall measure cav/sol/none, L = loft top-up, F = flat roof, G = glazing).

Electricity zones: `SUB:<substation>` comes from the nearest-secondary-substation (Voronoi) assignment,
labelled `spatial_inference_nearest_substation` - it is NOT a verified connection; `LSOA:<code>` is an assumed
zone (`assumed_zone_lsoa`) for dwellings outside the ten urban wards.

The three demand vectors are useful energy, not fuel: space heat = ISO 52016 zone heat need under the calibrated
physics (weather scaler 1.0, no system efficiency); hot water = useful energy at the tap (BREDEM/SAP 46 + 26 N
litres/day, 37 K rise; add cylinder/distribution losses and generator efficiency in PyPSA); non-heating
electricity = BREDEM appliances + lighting + electric cooking (no heat-pump, resistive-heating, shower or
immersion electricity - those are PyPSA's technologies). R0/R1/R2 are three descriptions of the same
dwellings, so an optimiser mixing them must weight them to one: Q = sum_p x_p Q_p with sum_p x_p = 1.
Timestamps are the hours of {case.get('year', 2021)} labelled UTC; the profiles follow local clock time of the TMY
year (no daylight-saving shift is applied, as in the EPW/ISO 52016 calculation). Units: kW average over the hour;
PyPSA loads take MW (divide by 1000).

Case `{case.get('case_id', '')}`: {case.get('description', '')}
"""
    p = out_dir / "README_pypsa_interface.md"; p.write_text(txt, encoding="utf-8"); return p


def write_demand_profiles(path, inputs: pd.DataFrame, cid: pd.Series, rep_profiles: dict, dhw_shapes: dict, elec_shapes: dict, usage: dict,
                          case_id: str, index: pd.DatetimeIndex, chunk_clusters: int = 200) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """
    Stream the long demand table to parquet (pyarrow ParquetWriter) cluster by cluster, so that thousands
    of clusters x 3 packages x 8760 hours never sit in memory at once. Returns the profile library, the
    cluster weights and the per-cluster annual totals used for the consistency checks.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    d = pd.DataFrame({"cluster_id": cid, "sa": inputs["construction_sa"].astype(str), "size": inputs["size_class"].astype(str), "area": inputs["floor_area_final"].astype(float),
                      "dhw": np.asarray(usage["dhw_useful_kWh"], float), "elec": np.asarray(usage["elec_nonheat_kWh"], float)}, index=inputs.index)
    keys = list(rep_profiles["R0"].keys()); pos = {k: i for i, k in enumerate(keys)}
    d["gi"] = [pos.get((s_, z), -1) for s_, z in zip(d["sa"], d["size"])]
    d = d[d["gi"] >= 0]
    Hp = {pkg: np.stack([rep_profiles[pkg][k] for k in keys]) for pkg in R_PACKAGES}
    Dm = np.stack([dhw_shapes.get(k, np.full(8760, 1 / 8760)) for k in keys]); Em = np.stack([elec_shapes.get(k, np.full(8760, 1 / 8760)) for k in keys])
    schema = pa.schema([("case_id", pa.string()), ("timestamp_utc", pa.timestamp("ns", tz="UTC")), ("cluster_id", pa.string()), ("package_id", pa.string()),
                        ("space_heat_kw", pa.float32()), ("hot_water_heat_kw", pa.float32()), ("non_heating_electricity_kw", pa.float32())])
    writer = pq.ParquetWriter(str(path), schema, compression="snappy")
    buf, weights, totals = [], [], {}
    ts = pd.DatetimeIndex(index)
    for n_done, (c, sub) in enumerate(d.groupby("cluster_id"), start=1):
        w_area = np.bincount(sub["gi"], weights=sub["area"], minlength=len(keys))
        w_dhw = np.bincount(sub["gi"], weights=sub["dhw"], minlength=len(keys)); w_el = np.bincount(sub["gi"], weights=sub["elec"], minlength=len(keys))
        dhw_kw = (w_dhw[:, None] * Dm).sum(axis=0); el_kw = (w_el[:, None] * Em).sum(axis=0)
        for pkg in R_PACKAGES:
            sh_kw = (w_area[:, None] * Hp[pkg]).sum(axis=0) / 1000.0
            buf.append(pd.DataFrame({"case_id": case_id, "timestamp_utc": ts, "cluster_id": c, "package_id": pkg,
                                     "space_heat_kw": sh_kw.astype(np.float32), "hot_water_heat_kw": dhw_kw.astype(np.float32),
                                     "non_heating_electricity_kw": el_kw.astype(np.float32)}))
            totals[(c, pkg)] = {"space_heat_kWh": float(sh_kw.sum()), "hot_water_kWh": float(dhw_kw.sum()), "non_heating_elec_kWh": float(el_kw.sum()),
                                "space_heat_peak_kW": float(sh_kw.max()), "n_dwellings": int(len(sub)), "heated_floor_area_m2": float(sub["area"].sum())}
        weights.append({"cluster_id": c, **{f"m2|{k[0]}|{k[1]}": float(v) for k, v in zip(keys, w_area) if v > 0}})
        if n_done % chunk_clusters == 0:
            writer.write_table(pa.Table.from_pandas(pd.concat(buf, ignore_index=True), schema=schema, preserve_index=False)); buf = []
    if buf:
        writer.write_table(pa.Table.from_pandas(pd.concat(buf, ignore_index=True), schema=schema, preserve_index=False))
    writer.close()
    lib = []
    for pkg in R_PACKAGES:
        for k in keys:
            lib.append(pd.DataFrame({"case_id": case_id, "timestamp_utc": ts, "construction_sa": k[0], "size_class": k[1], "package_id": pkg,
                                     "space_heat_w_per_m2": rep_profiles[pkg][k].astype(np.float32), "dhw_shape": dhw_shapes.get(k, np.full(8760, 1 / 8760)).astype(np.float32),
                                     "elec_shape": elec_shapes.get(k, np.full(8760, 1 / 8760)).astype(np.float32)}))
    tot = pd.DataFrame.from_dict(totals, orient="index"); tot.index = pd.MultiIndex.from_tuples(tot.index, names=["cluster_id", "package_id"])
    return pd.concat(lib, ignore_index=True), pd.DataFrame(weights), tot.reset_index()
