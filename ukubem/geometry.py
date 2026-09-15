"""
Dwelling record -> pyBuildingEnergy `BUI` dictionary.

Conventions of the geometry derivation (parameters/demand_model/geometry_defaults.json)
so that both engines see the same building:

* one rectangular zone per dwelling, plan aspect 1.5 with party walls on the long sides;
* exposed-perimeter fraction by built form (detached 1.0, semi / end-terrace 0.70,
  mid-terrace 0.40); flats additionally lose wall to neighbours on the same floor plate;
* a flat is one storey of its block and owns 1/storeys of the block's roof and ground floor
  (area conservation); party floors/ceilings and party walls are adiabatic;
* window-to-wall ratio 0.25 on the exposed wall, glazing spread evenly over N/E/S/W because
  the orientation of a dwelling is unknown at stock scale;
* ground-floor U and infiltration by RdSAP age band, thermal mass by wall construction
  (ISO 13790 classes, J/m2K of floor area), SAP y-value thermal bridging (0.15 W/m2K of
  envelope) expressed as a linear psi on the exposed perimeter as pyBuildingEnergy expects.

Everything is a field of `UKGeometryAssumptions`, so calibration can scale U-values,
infiltration or gains without touching this code.
"""
from __future__ import annotations

from dataclasses import fields, dataclass, field, replace
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class UKGeometryAssumptions:
    plan_aspect_ratio: float = 1.5
    window_area_method: str = "rdsap_s4"                   # "rdsap_s4" (default window area by age band) | "wwr"
    window_to_wall_ratio: float = 0.25                      # used when window_area_method == "wwr"
    max_window_fraction_of_wall: float = 0.60               # cap so glazing never exceeds most of the exposed wall
    default_storey_height: float = 2.5
    storey_height_bounds: tuple[float, float] = (2.3, 3.5)
    max_storeys: int = 12
    exposed_perimeter_fraction: Mapping[str, float] = field(default_factory=lambda: {
        "detached": 1.00, "semi-detached": 0.70, "end-terrace": 0.70, "mid-terrace": 0.40})
    default_exposed_fraction: float = 0.70
    roof_area_factor: Mapping[str, float] = field(default_factory=lambda: {"roof room": 1.20})
    u_floor_by_age: Mapping[str, float] = field(default_factory=lambda: {
        "A": 0.70, "B": 0.70, "C": 0.70, "D": 0.70, "E": 0.60, "F": 0.60, "G": 0.60,
        "H": 0.45, "I": 0.45, "J": 0.25, "K": 0.22, "L": 0.18})
    default_u_floor: float = 0.70
    ach_infiltration_by_age: Mapping[str, float] = field(default_factory=lambda: {
        "A": 0.55, "B": 0.55, "C": 0.45, "D": 0.40, "E": 0.35, "F": 0.30, "G": 0.28,
        "H": 0.25, "I": 0.22, "J": 0.18, "K": 0.18, "L": 0.18})
    default_ach_infiltration: float = 0.40
    ach_ventilation: float = 0.35
    thermal_bridging_y: float = 0.15                       # W/m2K of envelope (SAP 2012 Appendix K)
    thermal_capacitance_by_construction: Mapping[str, float] = field(default_factory=lambda: {
        "solid brick": 260_000, "stone": 260_000, "cavity": 165_000, "system built": 165_000,
        "timber frame": 110_000, "park home": 80_000})
    default_thermal_capacitance: float = 165_000            # J/m2K of floor area (ISO 13790 medium)
    internal_capacity_per_area: float = 10_000              # furniture, J/m2K (pyBuildingEnergy c_int_per_A_us)
    solar_absorptance_wall: float = 0.60
    solar_absorptance_roof: float = 0.60
    window_height: float = 1.2
    window_g_default: float = 0.72
    max_heating_w_per_m2: float = 150.0
    # Cooling is disabled by giving it zero capacity; the setpoint itself stays realistic because
    # pyBuildingEnergy's ISO 13370 ground model takes the mean of the heating and cooling setpoints
    # as the annual mean internal temperature.
    cooling_setpoint: float = 26.0
    cooling_capacity_w: float = 0.0
    # calibration scalers (applied on top of the dwelling's own values)
    wall_u_scale: float = 1.0
    roof_u_scale: float = 1.0
    window_u_scale: float = 1.0
    floor_u_scale: float = 1.0
    infil_scale: float = 1.0
    gains_scale: float = 1.0
    setpoint_offset_c: float = 0.0
    # default (deterministic) regime when no stochastic schedule is supplied
    default_setpoint_c: float = 19.5
    default_setback_c: float = 16.0
    default_gains_w_m2: float = 4.0
    # background temperature outside the EFUS heating periods of the stochastic regime (CHAP: none; frost 12 C)
    stochastic_setback_c: float = 12.0

    def with_(self, **kw) -> "UKGeometryAssumptions":
        return replace(self, **kw)


def _norm(x) -> str:
    return str(x).strip().lower().replace("_", " ")


# RdSAP 2012 Appendix S, Table S4: default window area A_w = a * TFA + b  [m2] by age band
# (England & Wales bands A..L). Bands H onward share the coefficients of band H.
_RDSAP_S4_HOUSE = {"A": (0.1220, 6.875), "B": (0.1294, 5.515), "C": (0.1239, 7.332), "D": (0.1252, 5.520),
                   "E": (0.1356, 5.242), "F": (0.0948, 6.534), "G": (0.1382, -0.027), "H": (0.1435, -0.403)}
_RDSAP_S4_FLAT = {"A": (0.0801, 5.580), "B": (0.0341, 8.562), "C": (0.0717, 6.560), "D": (0.1199, 1.975),
                  "E": (0.0510, 4.554), "F": (0.0813, 3.744), "G": (0.1148, 0.392), "H": (0.1148, 0.392)}


def rdsap_default_window_area(floor_area: float, age_band: str, is_flat: bool) -> float:
    """RdSAP default total window area for a dwelling of `floor_area` m2 and age band."""
    table = _RDSAP_S4_FLAT if is_flat else _RDSAP_S4_HOUSE
    band = age_band if age_band in table else ("H" if age_band > "H" else "D")
    a_, b_ = table[band]
    return max(a_ * floor_area + b_, 1.0)


def _capacity_class(wall_construction: str, wall_insulation: str) -> str:
    """ISO 52016 mass-distribution class: heavy uninsulated masonry -> mass in the middle,
    everything else -> mass on the inside (inner leaf / plasterboard)."""
    w = _norm(wall_construction)
    if w in ("solid brick", "stone") and _norm(wall_insulation) != "insulated":
        return "class_m"
    return "class_i"


def build_bui(row: Mapping, a: UKGeometryAssumptions | None = None, schedule: dict | None = None,
              name: str | None = None) -> tuple[dict, dict]:
    """
    Build the pyBuildingEnergy input for one dwelling.

    `row` is a record of `export_stratified/dwelling_inputs.parquet` (accommodation_type,
    built_form, floor_area_final, storeys_est, height_eaves_m, area_m2, uprn_count, roof_type,
    roof_exposed, wall_U, roof_U, window_U, window_SHGC, age_band_resolved,
    wall_construction_resolved, wall_insulation, lat, lon, ...).
    `schedule` is the dict returned by `ukubem.schedules.dwelling_schedule` (or None for the
    deterministic EnerMap regime).

    Returns (BUI, derived) where `derived` records every geometric quantity used.
    """
    a = a or UKGeometryAssumptions()
    g = lambda k, d=np.nan: row[k] if k in row and pd.notna(row[k]) else d  # noqa: E731

    acc = _norm(g("accommodation_type", ""))
    is_flat = acc.startswith(("flat", "maisonette", "apartment"))
    A_floor = float(np.clip(g("floor_area_final", 90.0), 15.0, 1000.0))
    storeys_bldg = int(np.clip(np.round(g("storeys_est", 2.0)), 1, a.max_storeys))
    storeys_dw = 1 if is_flat else storeys_bldg
    eaves = g("height_eaves_m", np.nan)
    h_storey = eaves / storeys_bldg if np.isfinite(eaves) and eaves > 0 else a.default_storey_height
    h_storey = float(np.clip(h_storey, *a.storey_height_bounds))

    footprint = A_floor / storeys_dw
    short = np.sqrt(footprint / a.plan_aspect_ratio)
    perimeter = 2.0 * short * (1.0 + a.plan_aspect_ratio)
    form = _norm(g("built_form", ""))
    exposed_frac = a.exposed_perimeter_fraction.get(form, a.default_exposed_fraction)
    if is_flat:
        units = max(1.0, float(g("uprn_count", 1.0)))
        per_floor = max(1.0, round(units / storeys_bldg))
        one, two = a.exposed_perimeter_fraction["semi-detached"], a.exposed_perimeter_fraction["mid-terrace"]
        plate = 1.0 if per_floor <= 1 else (one if per_floor == 2 else (2.0 / per_floor) * one + ((per_floor - 2.0) / per_floor) * two)
        exposed_frac *= plate
    height_dw = h_storey * storeys_dw
    gross_wall = perimeter * exposed_frac * height_dw
    age = str(g("age_band_resolved", "")).strip().upper()
    if a.window_area_method == "rdsap_s4":
        A_window = rdsap_default_window_area(A_floor, age or "D", is_flat)
    else:
        A_window = a.window_to_wall_ratio * gross_wall
    A_window = float(min(A_window, a.max_window_fraction_of_wall * gross_wall))
    A_wall = gross_wall - A_window
    A_party = perimeter * (1.0 - exposed_frac) * height_dw

    # `roof_exposed` already says whether THIS dwelling is under the roof (top-floor flat or a
    # house), so the exposed roof is the whole footprint - never divided by the storeys again
    # (three stacked 60 m2 flats must keep 60 m2 of roof, not 20 m2).
    has_roof = bool(g("roof_exposed", 1)) and np.isfinite(g("roof_U", np.nan))
    roof_factor = a.roof_area_factor.get(_norm(g("roof_type", "")), 1.0)
    roof_share = 1.0 if has_roof else 0.0
    A_roof = footprint * roof_factor * roof_share
    # ground floor: an explicit `ground_exposed` flag (0/1, or a share) wins; otherwise a house is
    # on the ground, a single-storey flat is on the ground, a top-floor flat of a taller block is
    # not, and a flat of unknown level gets the expected share 1 / (storeys - 1). Summed over a
    # block this conserves one ground floor per plate, a 1/storeys average.
    ge = g("ground_exposed", np.nan)
    if np.isfinite(ge):
        ground_share, ground_source = float(np.clip(ge, 0.0, 1.0)), "ground_exposed"
    elif not is_flat or storeys_bldg <= 1:
        ground_share, ground_source = 1.0, "house_or_single_storey"
    elif has_roof:
        ground_share, ground_source = 0.0, "top_floor_flat"
    else:
        ground_share, ground_source = 1.0 / max(storeys_bldg - 1, 1), "expected_share_unknown_level"
    A_ground = footprint * ground_share
    A_ceiling_adiabatic = footprint * (1.0 - roof_share) if is_flat else 0.0         # party ceiling to the flat above
    A_floor_adiabatic = footprint * (1.0 - ground_share) if is_flat else 0.0         # party floor to the flat below

    age = str(g("age_band_resolved", "")).strip().upper()
    u_floor = a.u_floor_by_age.get(age, a.default_u_floor) * a.floor_u_scale
    ach_infil = a.ach_infiltration_by_age.get(age, a.default_ach_infiltration) * a.infil_scale
    u_wall = float(g("wall_U", 1.5)) * a.wall_u_scale
    u_roof = (float(g("roof_U", 0.4)) if has_roof else 0.0) * a.roof_u_scale
    u_window = float(g("window_U", 3.02)) * a.window_u_scale
    g_win = float(g("window_SHGC", a.window_g_default))
    kappa = a.thermal_capacitance_by_construction.get(_norm(g("wall_construction_resolved", "")), a.default_thermal_capacitance)
    C_total = kappa * A_floor                                          # J/K for the whole zone fabric

    volume = A_floor * h_storey
    H_ve = 1200.0 * volume * (ach_infil + a.ach_ventilation) / 3600.0  # W/K, rho*c = 1200 J/m3K
    A_envelope = A_wall + A_window + A_roof + A_ground
    exposed_perimeter = max(perimeter * exposed_frac, 1.0)
    psi_tb = a.thermal_bridging_y * A_envelope / exposed_perimeter    # W/mK so that psi*perimeter = y*A_env

    # --- surfaces -------------------------------------------------------------------------
    opaque_areas = {"wall": A_wall, "party": A_party, "roof": A_roof, "ground": A_ground,
                    "ceil_ad": A_ceiling_adiabatic, "floor_ad": A_floor_adiabatic}
    tot_opaque = sum(v for v in opaque_areas.values() if v > 0) or 1.0
    cap = lambda area: C_total * area / tot_opaque  # noqa: E731

    surfaces = []
    for az, lab in ((0, "N"), (90, "E"), (180, "S"), (270, "W")):
        surfaces.append({"name": f"Wall {lab}", "type": "opaque", "area": A_wall / 4.0, "sky_view_factor": 0.5,
                         "u_value": u_wall, "solar_absorptance": a.solar_absorptance_wall, "thermal_capacity": cap(A_wall / 4.0),
                         "orientation": {"azimuth": az, "tilt": 90}, "name_adj_zone": None,
                         "height": height_dw, "length": (A_wall / 4.0) / height_dw})
    for az, lab in ((0, "N"), (90, "E"), (180, "S"), (270, "W")):
        surfaces.append({"name": f"Win {lab}", "type": "transparent", "area": A_window / 4.0, "sky_view_factor": 0.5,
                         "u_value": u_window, "solar_absorptance": 0.5, "thermal_capacity": 0.0,
                         "orientation": {"azimuth": az, "tilt": 90}, "name_adj_zone": None,
                         "height": a.window_height, "width": 1.0, "parapet": 0.9, "g_value": g_win, "shading": False})
    if A_roof > 0:
        surfaces.append({"name": "Roof", "type": "opaque", "area": A_roof, "sky_view_factor": 1.0, "u_value": u_roof,
                         "solar_absorptance": a.solar_absorptance_roof, "thermal_capacity": cap(A_roof),
                         "orientation": {"azimuth": 0, "tilt": 0}, "name_adj_zone": None,
                         "height": np.sqrt(A_roof), "length": np.sqrt(A_roof)})
    surfaces.append({"name": "Ground", "type": "opaque", "boundary": "ground", "area": A_ground, "sky_view_factor": 0.0,
                     "u_value": u_floor, "solar_absorptance": 0.0, "thermal_capacity": cap(A_ground),
                     "orientation": {"azimuth": 0, "tilt": 0}, "name_adj_zone": None,
                     "height": np.sqrt(A_ground), "length": np.sqrt(A_ground)})
    if A_party > 0:
        surfaces.append({"name": "Party walls", "type": "adiabatic", "area": A_party, "sky_view_factor": 0.0,
                         "u_value": 0.0, "solar_absorptance": 0.0, "thermal_capacity": cap(A_party),
                         "orientation": {"azimuth": 0, "tilt": 90}, "name_adj_zone": None, "height": height_dw, "length": A_party / height_dw})
    if A_ceiling_adiabatic > 0:
        surfaces.append({"name": "Party ceiling", "type": "adiabatic", "area": A_ceiling_adiabatic, "sky_view_factor": 0.0,
                         "u_value": 0.0, "solar_absorptance": 0.0, "thermal_capacity": cap(A_ceiling_adiabatic),
                         "orientation": {"azimuth": 0, "tilt": 0}, "name_adj_zone": None,
                         "height": np.sqrt(A_ceiling_adiabatic), "length": np.sqrt(A_ceiling_adiabatic)})
    if A_floor_adiabatic > 0:
        surfaces.append({"name": "Party floor", "type": "adiabatic", "area": A_floor_adiabatic, "sky_view_factor": 0.0,
                         "u_value": 0.0, "solar_absorptance": 0.0, "thermal_capacity": cap(A_floor_adiabatic),
                         "orientation": {"azimuth": 0, "tilt": 0}, "name_adj_zone": None,
                         "height": np.sqrt(A_floor_adiabatic), "length": np.sqrt(A_floor_adiabatic)})

    # --- schedules --------------------------------------------------------------------------
    setpoint = (schedule["setpoint_c"] if schedule else a.default_setpoint_c) + a.setpoint_offset_c
    setback = (schedule["setback_c"] if schedule else a.default_setback_c) + a.setpoint_offset_c
    wd = [0] * 6 + [1] * 3 + [0] * 7 + [1] * 6 + [0] * 2          # EnerMap weekday 06-08, 16-21
    we = [0] * 6 + [1] * 16 + [0] * 2                              # weekend 06-21
    hourly = None
    if schedule is not None:
        hourly = {"heating": np.asarray(schedule["heating_on"], dtype=float),
                  "occupancy": np.asarray(schedule["occupancy"], dtype=float),
                  "lighting": np.asarray(schedule["lighting"], dtype=float),
                  "appliances": np.asarray(schedule["appliances"], dtype=float),
                  "ventilation": np.ones(8760)}
        full_load_occ = 0.0                                        # gains come as an external W series
    else:
        full_load_occ = a.default_gains_w_m2 * a.gains_scale

    btc = "Residential_apartment" if is_flat else "Residential_detached_house"
    bui = {
        "building": {
            "name": str(name or g("UPRN", g("dwelling_id", "dwelling"))),
            "azimuth_relative_to_true_north": 0, "latitude": float(g("lat", 51.24)), "longitude": float(g("lon", -0.57)),
            "exposed_perimeter": float(exposed_perimeter), "height": float(height_dw), "wall_thickness": 0.3,
            "n_floors": int(storeys_dw), "building_type_class": btc, "adj_zones_present": False, "number_adj_zone": 0,
            "net_floor_area": float(A_floor), "construction_class": _capacity_class(g("wall_construction_resolved", ""), g("wall_insulation", "")),
            "construction_year": age, "country": "UK", "country_code": "GB",
        },
        "adjacent_zones": [],
        "building_surface": surfaces,
        "units": {"area": "m2", "u_value": "W/m2K", "thermal_capacity": "J/K", "azimuth": "deg (0=N,90=E,180=S,270=W)",
                  "tilt": "deg (0=horizontal, 90=vertical)", "internal_gain": "W/m2", "internal_gain_profile": "0-1", "HVAC_profile": "0/1"},
        "building_parameters": {
            "temperature_setpoints": {"heating_setpoint": float(setpoint), "heating_setback": float(setback),
                                      "cooling_setpoint": a.cooling_setpoint, "cooling_setback": a.cooling_setpoint, "units": "C"},
            "system_capacities": {"heating_capacity": float(a.max_heating_w_per_m2 * A_floor),
                                  "cooling_capacity": float(a.cooling_capacity_w), "units": "W"},
            "ventilation": {"ventilation_type": "custom", "flow_rate_per_person": 0.0, "units": "l/(s m2)",
                            "custom_heat_transfer_coefficient_ventilation": float(H_ve)},
            "internal_gains": [
                {"name": "occupants", "full_load": float(full_load_occ), "weekday": [1.0] * 24, "weekend": [1.0] * 24},
                {"name": "appliances", "full_load": 0.0, "weekday": [0.0] * 24, "weekend": [0.0] * 24},
                {"name": "lighting", "full_load": 0.0, "weekday": [0.0] * 24, "weekend": [0.0] * 24}],
            "construction": {"wall_thickness": 0.3, "thermal_bridges": float(psi_tb), "units": "m, W/mK (psi on exposed perimeter)"},
            "climate_parameters": {"coldest_month": 1, "units": "1-12"},
            "heating_profile": {"weekday": wd, "weekend": we},
            "cooling_profile": {"weekday": [0] * 24, "weekend": [0] * 24},
            "ventilation_profile": {"weekday": [1] * 24, "weekend": [1] * 24},
        },
    }
    if hourly is not None:
        bui["building_parameters"]["hourly_profiles"] = hourly

    derived = {"A_floor": A_floor, "storeys_bldg": storeys_bldg, "storeys_dw": storeys_dw, "h_storey": h_storey,
               "footprint": footprint, "perimeter": perimeter, "exposed_frac": exposed_frac, "A_wall": A_wall,
               "A_window": A_window, "A_party": A_party, "A_roof": A_roof, "A_ground": A_ground, "volume": volume,
               "u_wall": u_wall, "u_roof": u_roof, "u_window": u_window, "u_floor": u_floor, "g_window": g_win,
               "ach_infil": ach_infil, "H_ve_W_K": H_ve, "psi_tb": psi_tb, "H_tb_W_K": psi_tb * exposed_perimeter,
               "HTC_fabric_W_K": u_wall * A_wall + u_window * A_window + u_roof * A_roof + u_floor * A_ground + psi_tb * exposed_perimeter,
               "C_total_J_K": C_total, "setpoint_c": setpoint, "setback_c": setback, "is_flat": is_flat, "roof_share": roof_share, "ground_share": ground_share, "ground_source": ground_source}
    derived["HTC_total_W_K"] = derived["HTC_fabric_W_K"] + H_ve
    derived["HTC_per_m2"] = derived["HTC_total_W_K"] / A_floor
    return bui, derived

# ---------------------------------------------------------------- defaults from parameters/demand_model/geometry_defaults.json
from . import params as _params  # noqa: E402
_GEOMETRY_PARAMS = {k: v for k, v in _params.load("demand_model", "geometry_defaults").items() if k != "source"}
_FIELD_NAMES = {f.name for f in fields(UKGeometryAssumptions)}
_unknown = set(_GEOMETRY_PARAMS) - _FIELD_NAMES
if _unknown:
    raise KeyError(f"geometry_defaults.json has unknown fields {sorted(_unknown)}")
_orig_init = UKGeometryAssumptions.__init__


def _coerce(name, v):
    d = UKGeometryAssumptions.__dataclass_fields__[name].default
    if isinstance(v, list) and (isinstance(d, tuple) or name.endswith("bounds")):
        return tuple(v)
    return dict(v) if isinstance(v, dict) else v


def _init_from_params(self, **kw):
    merged = {k: _coerce(k, v) for k, v in _GEOMETRY_PARAMS.items()}
    merged.update(kw)
    _orig_init(self, **merged)


UKGeometryAssumptions.__init__ = _init_from_params
