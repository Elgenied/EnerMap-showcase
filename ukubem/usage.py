"""
Non-heating usage model (BREDEM 2012 / SAP 10.2, parameters/demand_model/usage_bredem.json)
accounting for hot water, appliances, lighting and cooking with one,
evidence-based rules (BREDEM 2012 / SAP 10.2) instead of fitted scalers.

Choices, stated:
* A per-dwelling seeded draw of the gas-cooking (50 %) and electric-shower (37 %) memberships per dwelling with a seeded
  Bernoulli; here the EXPECTED values are used (deterministic), which leaves every LSOA mean unchanged
  and keeps the calibration objective noise-free.
* the dwelling-level tier keeps the CREST/CHAP generator's hourly SHAPES for appliances, lighting and
  hot water, rescaled so that their annual totals equal these BREDEM values (see `runner.py`).
"""
from __future__ import annotations

import numpy as np

from . import params

USAGE_ASSUMPTIONS = {k: v for k, v in params.load("demand_model", "usage_bredem").items() if k != "source"}   # parameters/demand_model/usage_bredem.json
_KWH_YR_TO_W = 1000.0 / 8760.0


def sap_occupancy(floor_area_m2) -> np.ndarray:
    """SAP 10.2 Table 1b assumed occupancy N from total floor area ."""
    tfa = np.asarray(floor_area_m2, dtype=float)
    return np.where(tfa > 13.9, 1 + 1.76 * (1 - np.exp(-0.000349 * (tfa - 13.9) ** 2)) + 0.0013 * (tfa - 13.9), 1.0)


def bredem_usage(floor_area_m2, gas_eligible, occupancy=None) -> dict:
    """
    Annual non-heating energy per dwelling (kWh/yr) as arrays:
      appliances_kWh, lighting_kWh, cooking_elec_kWh, cooking_gas_kWh (expected value),
      dhw_useful_kWh (no losses), dhw_pou_elec_kWh (electric showers, expected value),
      dhw_system_kWh (system hot water incl. 15 % losses), gains_W (BREDEM S6 average gains),
      occupancy_N.
    `gas_eligible`: dwellings that may cook on gas (gas-heated, or gas-connected non-gas-heated).
    """
    a = USAGE_ASSUMPTIONS
    tfa = np.asarray(floor_area_m2, dtype=float)
    N = sap_occupancy(tfa) if occupancy is None else np.asarray(occupancy, dtype=float)
    gas_el = np.asarray(gas_eligible, dtype=bool)
    an = (tfa * N) ** 0.4714
    appliances = 184.8 * an
    lighting = 59.73 * an * (1 - 0.5 * 0.75)
    p_gas_cook = np.where(gas_el, a["gas_cooking_share_in_gas_homes"], 0.0)
    cooking_gas = p_gas_cook * (481.0 + 96.0 * N)
    cooking_elec = (1.0 - p_gas_cook) * (275.0 + 55.0 * N)
    litres = 46.0 + 26.0 * N
    useful = litres * 4.18 * a["dhw_delta_t_K"] / 3600.0 * 365.0
    shower_frac = np.clip(a["shower_events_per_person_day"] * N * a["shower_litres"] / litres, 0.0, 0.75)
    pou = a["electric_shower_share"] * useful * shower_frac
    system = (useful - pou) * a["dhw_loss_factor_system"]
    gains_W = (20.0 * N + 1.00 * appliances * _KWH_YR_TO_W + 0.85 * lighting * _KWH_YR_TO_W
               + 0.90 * cooking_elec * _KWH_YR_TO_W + 0.75 * cooking_gas * _KWH_YR_TO_W
               + 0.25 * 0.85 * system * _KWH_YR_TO_W)
    return {"occupancy_N": N, "appliances_kWh": appliances, "lighting_kWh": lighting,
            "cooking_elec_kWh": cooking_elec, "cooking_gas_kWh": cooking_gas,
            "dhw_useful_kWh": useful, "dhw_pou_elec_kWh": pou, "dhw_system_kWh": system,
            "elec_nonheat_kWh": appliances + lighting + cooking_elec, "gains_W": gains_W}
