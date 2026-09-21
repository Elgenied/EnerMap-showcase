"""
One energy-accounting path for both tiers, with the tool's usage model and gas-benchmark rules.

The archetype tier (intensities scaled to every dwelling) and the direct dwelling runs turn the
simulated space-heat need into delivered fuel with the *same* rule. Everything that is not the
space-heat simulation - hot water, appliances, lighting, cooking - comes from the BREDEM/SAP usage
model (`ukubem.usage`), so no post-processing parameter is fitted:

    weather_scale   TMY -> benchmark-year scalar on space heat (fixed 1.0: 2021 near-normal)
    boiler_eff      seasonal efficiency of combustion boilers, space heat and DHW (fixed 0.82)
    hp_scop         heat-pump SPF, space heat (fixed 2.8);  dhw_hp_scop DHW (2.0)
    dhw_scale       scalar on BREDEM hot water (1.0)
    communal_distribution_loss   share of heat lost in communal gas networks (0.15)

Gas-benchmark membership: gas-heated dwellings that are not
on communal heat, plus dwellings with a live mains-gas connection but non-gas heating, which cook
on gas (50 % share) and therefore have consuming gas meters. Communal-heat dwellings are outside
the gas benchmark; every dwelling is in the electricity benchmark.
"""
from __future__ import annotations

import numpy as np

from .systems import FAMILY_FUEL, SYSTEM_DEFAULTS
from .usage import bredem_usage

POST_DEFAULTS = {
    "weather_scale": 1.0,
    "dhw_scale": 1.0,
    "elec_target_kWh_m2": None,          # None -> BREDEM appliances + lighting + cooking (no fitted target)
    "boiler_eff": SYSTEM_DEFAULTS["boiler_eff"],
    "hp_scop": SYSTEM_DEFAULTS["hp_scop"],
    "dhw_hp_scop": SYSTEM_DEFAULTS["dhw_hp_scop"],
    "communal_distribution_loss": SYSTEM_DEFAULTS["communal_distribution_loss"],
}


def family_masks(fam, gas_connected=None) -> dict:
    """Boolean masks per generator/fuel class; `gas_connected` (mains_gas_flag == Y) adds the gas-benchmark membership."""
    fam = np.asarray(fam, dtype=object)
    gen = np.array([FAMILY_FUEL.get(str(f), ("gas", "boiler"))[1] for f in fam])
    fuel = np.array([FAMILY_FUEL.get(str(f), ("gas", "boiler"))[0] for f in fam])
    boiler_gas = (gen == "boiler") & (fuel == "gas")
    communal = fam.astype(str) == "S.GasCommunal"
    gc = np.zeros(len(fam), bool) if gas_connected is None else np.asarray(gas_connected, dtype=bool)
    gas_connected_nonheat = gc & ~boiler_gas & ~communal
    return {"boiler_gas": boiler_gas, "boiler_other": (gen == "boiler") & (fuel == "other"), "communal": communal,
            "hp": gen == "heat_pump", "direct": gen == "direct", "fuel": fuel,
            "gas_connected_nonheat": gas_connected_nonheat,
            "gas_eligible": (boiler_gas & ~communal) | gas_connected_nonheat,
            "in_gas_benchmark": (boiler_gas & ~communal) | gas_connected_nonheat}


def usage_for(area_m2, masks: dict, occupancy=None) -> dict:
    """BREDEM/SAP non-heating usage for an array of dwellings (see ukubem.usage)."""
    return bredem_usage(area_m2, masks["gas_eligible"], occupancy)


def account(q_sim_kWh, area_m2, masks: dict, post: dict | None = None, usage: dict | None = None, occupancy=None,
            hp_electric_kWh=None) -> dict:
    """
    Needs -> delivered fuel (vectorised). `q_sim_kWh`: simulated space heat (TMY weather).
    `usage`: BREDEM components (computed here when None). Returns arrays; Q_H_kWh / dhw_kWh /
    appliances_kWh are the post-processed values actually used for the fuel accounting.
    """
    p = dict(POST_DEFAULTS)
    for k, v in (post or {}).items():
        if k in p and (v is not None or k == "elec_target_kWh_m2"):
            p[k] = v
    area = np.asarray(area_m2, float)
    u = usage if usage is not None else usage_for(area, masks, occupancy)
    q = np.asarray(q_sim_kWh, float) * p["weather_scale"]
    dhw = np.asarray(u["dhw_system_kWh"], float) * p["dhw_scale"]
    app = np.asarray(u["elec_nonheat_kWh"], float) if p["elec_target_kWh_m2"] is None else p["elec_target_kWh_m2"] * area
    cook_gas = np.asarray(u["cooking_gas_kWh"], float)
    pou = np.asarray(u["dhw_pou_elec_kWh"], float) * p["dhw_scale"]
    boiler = masks["boiler_gas"] | masks["boiler_other"]
    fuel_in = np.where(boiler, (q + dhw) / p["boiler_eff"], 0.0)
    fuel_in = np.where(masks["communal"], fuel_in / (1.0 - p["communal_distribution_loss"]), fuel_in)
    if np.any(masks['hp']) and hp_electric_kWh is None:
        raise ValueError('Hourly heat-pump electricity is required. Sum hourly space/DHW heat divided by configured COP; fixed seasonal factors are retired.')
    hp_elec = np.zeros_like(q) if hp_electric_kWh is None else np.asarray(hp_electric_kWh, float)
    if hp_elec.shape != q.shape or not np.all(np.isfinite(hp_elec)) or np.any(hp_elec < 0):
        raise ValueError('hp_electric_kWh must be finite, nonnegative, and aligned with dwellings')
    elec_heat = np.where(masks['hp'], hp_elec, 0.) + np.where(masks['direct'], q + dhw, 0.)
    gas = np.where(masks["boiler_gas"], fuel_in, 0.0) + cook_gas
    return {"Q_H_kWh": q, "dhw_kWh": dhw, "appliances_kWh": app, "cooking_gas_kWh": cook_gas, "dhw_pou_elec_kWh": pou,
            "elec_heat_kWh": elec_heat, "occupancy_N": np.asarray(u["occupancy_N"], float),
            "delivered_gas_kWh": gas, "delivered_elec_kWh": app + elec_heat + pou,
            "delivered_other_kWh": np.where(masks["boiler_other"], fuel_in, 0.0),
            "in_gas_benchmark": masks["in_gas_benchmark"], "gas_connected_nonheat": masks["gas_connected_nonheat"]}
