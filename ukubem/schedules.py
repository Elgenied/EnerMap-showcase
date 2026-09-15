"""
Occupancy, heating-regime, internal-gain and DHW schedules for UK dwellings.

Wraps `uk_ubem_schedule_generator/uk_ubem_schedules.py` (the user's transparent
implementation of the CREST Heat and Power (CHAP) logic on the bundled CHAP microdata: CREST
four-state occupancy chain, EFUS heating-regime categories with empirical timer programmes and
thermostat distributions, sampled heating seasons, lighting/appliance fractions and DHW draws) and turns one dwelling's result
into the calendar-year hourly arrays pyBuildingEnergy consumes:

    heating   0/1 availability  -> building_parameters["hourly_profiles"]["heating"]
    gains     W (people + lighting + appliances) -> external_internal_gains_series
    dhw       litres and kWh per hour (energy at 44.7 C delivery, 10 C inlet, CHAP)
    elec      appliance + lighting electricity, kWh

Household composition is not in EPC data. `sample_household` draws a household archetype
from a transparent prior conditioned on accommodation type, floor area and tenure
(Census 2021 England household-size marginals, adjusted for dwelling size); replace it
with LSOA-level Census TS017/TS003 shares when those tables are attached.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_GEN_DIR = Path(__file__).resolve().parent.parent / "uk_ubem_schedule_generator"
if str(_GEN_DIR) not in sys.path:
    sys.path.insert(0, str(_GEN_DIR))
import uk_ubem_schedules as ukg  # noqa: E402

HOUSEHOLD_SIZE = {a: len(m) for a, m in ukg.HOUSEHOLD_ARCHETYPES.items()}

# Prior probabilities of the seven household archetypes, by dwelling context.
# Rows: (accommodation type group, floor-area band). Values sum to 1 in each row.
# Anchored on Census 2021 (England) household size marginals: 1 person 30.1 %, 2 persons 34.4 %,
# 3 persons 15.5 %, 4 persons 12.9 %, 5+ persons 7.1 %, one-person households 65+ ~ 13 %,
# and the strong size gradient of flats vs houses.
_ARCH = ["single_working", "single_retired", "working_couple", "retired_couple",
         "family_children", "single_parent", "shared_adults"]
HOUSEHOLD_PRIOR = {
    ("flat", "small"):   [0.34, 0.16, 0.26, 0.07, 0.05, 0.06, 0.06],
    ("flat", "medium"):  [0.28, 0.14, 0.28, 0.08, 0.09, 0.07, 0.06],
    ("flat", "large"):   [0.22, 0.12, 0.30, 0.10, 0.14, 0.07, 0.05],
    ("house", "small"):  [0.20, 0.16, 0.24, 0.14, 0.14, 0.08, 0.04],
    ("house", "medium"): [0.12, 0.12, 0.24, 0.16, 0.24, 0.08, 0.04],
    ("house", "large"):  [0.07, 0.08, 0.22, 0.20, 0.33, 0.06, 0.04],
}
TENURE_TILT = {   # multiplicative tilt on the prior, renormalised: social/private renters younger, fewer retired
    "rented (social)": {"single_retired": 0.9, "retired_couple": 0.7, "single_parent": 1.8, "family_children": 1.1},
    "rented (private)": {"single_retired": 0.5, "retired_couple": 0.4, "single_working": 1.4, "shared_adults": 2.0, "working_couple": 1.2},
}


def _band(area: float) -> str:
    return "small" if area < 60 else ("medium" if area < 110 else "large")


def sample_household(accommodation_type: str, floor_area: float, tenure: str | None, rng: np.random.Generator) -> str:
    grp = "flat" if str(accommodation_type).lower().startswith(("flat", "maisonette", "apartment")) else "house"
    p = np.asarray(HOUSEHOLD_PRIOR[(grp, _band(float(floor_area)))], dtype=float)
    tilt = TENURE_TILT.get(str(tenure).lower(), {})
    if tilt:
        p = p * np.array([tilt.get(a, 1.0) for a in _ARCH])
    p = p / p.sum()
    return _ARCH[int(rng.choice(len(_ARCH), p=p))]


def dwelling_schedule(dwelling_id: str, household_archetype: str, floor_area: float, year: int = 2021,
                      seed: int = 42, heating_pattern: str | None = None, comfort_setpoint_c: float | None = None,
                      timestep_minutes: int = 60, control_mode: str = "efus_mixed",
                      season_mode: str = "efus_sampled", setback_c: float = 12.0, heating_pattern_quantile: float | None = None,
                      setpoint_quantile: float | None = None, **config_kw) -> dict:
    """
    One dwelling's stochastic year, aggregated to hourly arrays. `heating_pattern_quantile` / `setpoint_quantile`
    (stratified draws, see `ukubem.runner.schedule_for_row`) fix the regime and thermostat quantiles.

    Returns dict(heating_on[8760] 0/1, heating_fraction[8760], setpoint_c, setback_c, gains_w[8760],
                 occupancy[8760] (present / household size), lighting[8760], appliances[8760],
                 dhw_litre[8760], dhw_kWh[8760], elec_kWh (annual), dhw_kWh_year, people,
                 heating_pattern, season_start_month, season_length_months)
    """
    cfg = ukg.SimulationConfig.for_year(year, timestep_minutes=timestep_minutes, master_seed=seed,
                                        heating_control_mode=control_mode, heating_season_mode=season_mode,
                                        setback_setpoint_c=setback_c, **config_kw)
    spec = ukg.DwellingSpec(dwelling_id=str(dwelling_id), household_archetype=household_archetype,
                            floor_area_m2=float(floor_area), heating_pattern=heating_pattern,
                            comfort_setpoint_c=comfort_setpoint_c, heating_pattern_quantile=heating_pattern_quantile,
                            setpoint_quantile=setpoint_quantile)
    res = ukg.generate_dwelling(spec, cfg)
    f = res.frame
    # to local hours of the calendar year (drop the tz so the 8760 grid is the civil year)
    local = f.index.tz_convert(cfg.timezone).tz_localize(None)
    f = f.set_index(local)
    f = f[(f.index.year == year)]
    h = f.resample("1h").agg({"heating_enabled": "mean", "internal_gain_w": "mean", "occupants_present": "mean",
                              "lighting_fraction": "mean", "appliance_fraction": "mean",
                              "dhw_litre_per_step": "sum", "dhw_thermal_w": "mean", "electric_load_w": "mean"})
    h = h.reindex(pd.date_range(f"{year}-01-01", periods=8760, freq="h")).ffill().bfill()
    size = HOUSEHOLD_SIZE[household_archetype]
    step_h = timestep_minutes / 60.0
    return {
        "heating_on": (h["heating_enabled"].to_numpy() > 0).astype(float),
        "heating_fraction": h["heating_enabled"].to_numpy(),
        "setpoint_c": float(res.metadata["sampled_comfort_setpoint_c"]),
        "setback_c": float(setback_c),
        "gains_w": h["internal_gain_w"].to_numpy(),
        "occupancy": (h["occupants_present"] / size).to_numpy(),
        "lighting": h["lighting_fraction"].to_numpy(),
        "appliances": h["appliance_fraction"].to_numpy(),
        "dhw_litre": h["dhw_litre_per_step"].to_numpy(),
        "dhw_kWh": (h["dhw_thermal_w"] / 1000.0).to_numpy(),                      # W over 1 h -> kWh
        "dhw_kWh_year": float(h["dhw_thermal_w"].sum() / 1000.0),
        "elec_w": h["electric_load_w"].to_numpy(),
        "elec_kWh": float(h["electric_load_w"].sum() / 1000.0),
        "people": size,
        "heating_pattern": res.metadata["sampled_heating_pattern"],
        "season_start_month": res.metadata["sampled_heating_season_start_month"],
        "season_length_months": res.metadata["sampled_heating_season_length_months"],
        "heating_share_of_hours": float((h["heating_enabled"] > 0).mean()),
    }


def deterministic_schedule(floor_area: float, setpoint_c: float = 19.5, setback_c: float = 16.0,
                           gains_w_m2: float = 4.0, year: int = 2021) -> dict:
    """EnerMap's baseline regime (Huebner/Calderon): weekday 06-08 & 16-21, weekend 06-21, flat gains."""
    idx = pd.date_range(f"{year}-01-01", periods=8760, freq="h")
    wd = np.zeros(24); wd[6:9] = 1; wd[16:22] = 1
    we = np.zeros(24); we[6:22] = 1
    on = np.where(idx.dayofweek < 5, wd[idx.hour], we[idx.hour]).astype(float)
    return {"heating_on": on, "heating_fraction": on, "setpoint_c": setpoint_c, "setback_c": setback_c,
            "gains_w": np.full(8760, gains_w_m2 * floor_area), "occupancy": np.ones(8760), "lighting": np.zeros(8760),
            "appliances": np.zeros(8760), "dhw_litre": np.zeros(8760), "dhw_kWh": np.zeros(8760),
            "dhw_kWh_year": np.nan, "elec_kWh": np.nan, "people": np.nan, "heating_pattern": "EnerMap_2period",
            "season_start_month": None, "season_length_months": None, "heating_share_of_hours": float(on.mean())}
