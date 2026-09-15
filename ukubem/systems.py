"""
Heating-system families -> delivered energy by fuel.

The archetype stage (notebook 01) reduced the systems theme to eight deterministic families
of the EPC heating system. Here each family gets a generator model: seasonal efficiency for
combustion boilers, a seasonal performance factor for heat pumps, unity for direct electric.
Initial guesses and bounds come from parameters/heating_systems/efficiencies.json and
can be overridden per run (calibration) or per scenario (heat-pump conversion).
"""
from __future__ import annotations

import numpy as np

from . import params
import pandas as pd

# family -> (fuel, generator type)
_EFF = params.load("heating_systems", "efficiencies")                       # parameters/heating_systems/efficiencies.json
FAMILY_FUEL = {k: (v["fuel"], v["kind"]) for k, v in _EFF["family_fuel"].items()}

SYSTEM_DEFAULTS = {k: _EFF[k] for k in ("boiler_eff", "hp_scop", "direct_eff", "dhw_boiler_eff", "dhw_hp_scop", "dhw_electric_eff", "communal_distribution_loss")}


def delivered_energy(space_heat_kWh: float, dhw_kWh: float, appliances_kWh: float,
                     family: str, params: dict | None = None) -> dict:
    """
    Convert useful energy (space heat, DHW, appliance electricity) into delivered fuel.

    Returns a dict with delivered_gas_kWh, delivered_elec_kWh, delivered_other_kWh and the
    generator efficiency actually applied.
    """
    p = {**SYSTEM_DEFAULTS, **(params or {})}
    fuel, gen = FAMILY_FUEL.get(family, ("gas", "boiler"))
    if gen == "boiler":
        eff = p["boiler_eff"]
        heat_in = space_heat_kWh / eff
        dhw_in = dhw_kWh / p["dhw_boiler_eff"]
        if family == "S.GasCommunal":
            heat_in /= (1.0 - p["communal_distribution_loss"])
            dhw_in /= (1.0 - p["communal_distribution_loss"])
        fuel_in, elec_extra = heat_in + dhw_in, 0.0
    elif gen == "heat_pump":
        eff = p["hp_scop"]
        fuel_in, elec_extra = 0.0, space_heat_kWh / eff + dhw_kWh / p["dhw_hp_scop"]
    else:  # direct electric
        eff = p["direct_eff"]
        fuel_in, elec_extra = 0.0, space_heat_kWh / eff + dhw_kWh / p["dhw_electric_eff"]
    out = {"delivered_gas_kWh": 0.0, "delivered_elec_kWh": appliances_kWh + elec_extra, "delivered_other_kWh": 0.0,
           "generator_efficiency": eff, "fuel": fuel}
    if fuel == "gas":
        out["delivered_gas_kWh"] = fuel_in
    elif fuel == "other":
        out["delivered_other_kWh"] = fuel_in
    return out


def delivered_energy_frame(df: pd.DataFrame, family_col: str = "systems_fam", params: dict | None = None,
                           heat_col: str = "Q_H_kWh", dhw_col: str = "dhw_kWh", app_col: str = "appliances_kWh") -> pd.DataFrame:
    """Vectorised `delivered_energy` over a results table."""
    rows = [delivered_energy(r[heat_col], r[dhw_col], r[app_col], r[family_col], params) for _, r in df.iterrows()]
    return pd.DataFrame(rows, index=df.index)
