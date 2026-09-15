"""
Building-fabric heating flexibility, after Halloran (2024, DPhil thesis, University of Oxford, chapters 5-6):
a dwelling's thermal capacity C (kWh/K) and heat-loss rate H (kW/K) give a thermal time constant tau = C/H;
Newton cooling from a comfort start temperature to a comfort minimum gives the *comfortable heat-free hours*
t_c = -tau ln((T_min - T_out)/(T_start - T_out)) (eq. 5.6); a flexibility window dT gives the thermal energy
storage e = C dT (eq. 5.3 / 6.15) with hourly standing loss 1 - exp(-1/tau) (eq. 6.16) and a charging link
bounded by the heat-pump capacity (eqs 6.9-6.10). EnerMap evaluates these per dwelling from the ISO 52016
geometry (floor area -> C with the SAP medium thermal mass; envelope U-values, thermal bridging and ventilation
-> H) and aggregates them household-weighted per cluster (eqs 6.1-6.2) for the PyPSA-LAEP interface.
Parameters: parameters/heat_pumps/operation.json ["flexibility"].
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import params

_F = params.load("heat_pumps", "operation")["flexibility"]


def thermal_capacity_kWh_per_K(floor_area_m2, specific_kJ_per_m2K: float | None = None) -> np.ndarray:
    c = _F["specific_heat_capacity_kJ_per_m2K"] if specific_kJ_per_m2K is None else specific_kJ_per_m2K
    return np.asarray(floor_area_m2, dtype=float) * c / 3600.0


def heat_loss_kW_per_K(htc_W_per_K) -> np.ndarray:
    return np.asarray(htc_W_per_K, dtype=float) / 1000.0


def time_constant_h(C_kWh_per_K, H_kW_per_K) -> np.ndarray:
    return np.asarray(C_kWh_per_K, dtype=float) / np.maximum(np.asarray(H_kW_per_K, dtype=float), 1e-9)


def heat_free_hours(tau_h, t_out_C, t_start: float | None = None, t_min: float | None = None) -> np.ndarray:
    """Comfortable heat-free hours (Halloran eq. 5.6); infinite when the outdoor air is at or above the comfort minimum."""
    t_start = _F["comfort_start_C"] if t_start is None else t_start; t_min = _F["comfort_min_C"] if t_min is None else t_min
    tau = np.asarray(tau_h, dtype=float); t = np.asarray(t_out_C, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        hrs = -tau * np.log((t_min - t) / (t_start - t))
    return np.where(t < t_min, hrs, np.inf)


def storage_energy_kWh(C_kWh_per_K, delta_T_K: float | None = None) -> np.ndarray:
    return np.asarray(C_kWh_per_K, dtype=float) * (_F["delta_T_window_K"] if delta_T_K is None else delta_T_K)


def standing_loss_per_hour(tau_h) -> np.ndarray:
    return 1.0 - np.exp(-1.0 / np.maximum(np.asarray(tau_h, dtype=float), 1e-9))


def winter_temperature_percentiles(t_out_hourly, index: pd.DatetimeIndex, pcts=None, months=None) -> dict:
    """Percentiles of the daily mean outdoor temperature over the winter months of the weather year."""
    pcts = _F["winter_temperature_percentiles"] if pcts is None else pcts; months = _F["winter_months"] if months is None else months
    daily = pd.Series(np.asarray(t_out_hourly, dtype=float), index=index).resample("1D").mean()
    winter = daily[daily.index.month.isin(months)]
    return {f"p{p}": float(np.percentile(winter, p)) for p in pcts} | {"lowest": float(winter.min())}


def dwelling_flexibility(df: pd.DataFrame, floor_col: str = "floor_area_final", htc_col: str = "HTC_W_K",
                         t_ref: float | None = None, winter_temps: dict | None = None) -> pd.DataFrame:
    """Per-dwelling C, H, tau, storage energy and heat-free hours at the reference and winter-percentile temperatures."""
    C = thermal_capacity_kWh_per_K(df[floor_col]); H = heat_loss_kW_per_K(df[htc_col]); tau = time_constant_h(C, H)
    t_ref = _F["reference_outdoor_C"] if t_ref is None else t_ref
    out = pd.DataFrame({"thermal_capacity_kWh_per_K": C, "heat_loss_kW_per_K": H, "time_constant_h": tau,
                        "tes_energy_kWh": storage_energy_kWh(C), "standing_loss_per_h": standing_loss_per_hour(tau),
                        f"heat_free_hours_at_{t_ref:g}C": heat_free_hours(tau, t_ref)}, index=df.index)
    for k, t in (winter_temps or {}).items():
        out[f"heat_free_hours_{k}"] = heat_free_hours(tau, t)
    return out


def aggregate(flex: pd.DataFrame, by, n_col: str | None = None, hp_capacity_kW=None) -> pd.DataFrame:
    """Household-weighted means (tau, C, heat-free hours) and sums (storage energy, heat-pump capacity) per group (Halloran eqs 6.1-6.2)."""
    g = flex.groupby(by)
    hf_cols = [c for c in flex.columns if c.startswith("heat_free_hours")]
    agg = pd.DataFrame({
        "dwellings": g.size(),
        "time_constant_h_mean": g["time_constant_h"].mean(),
        "time_constant_h_p10": g["time_constant_h"].quantile(0.1), "time_constant_h_p90": g["time_constant_h"].quantile(0.9),
        "thermal_capacity_kWh_per_K_mean": g["thermal_capacity_kWh_per_K"].mean(),
        "heat_loss_kW_per_K_mean": g["heat_loss_kW_per_K"].mean(),
        "tes_energy_kWh": g["tes_energy_kWh"].sum(),
    })
    agg["standing_loss_per_h"] = standing_loss_per_hour(agg["time_constant_h_mean"])
    for c in hf_cols:
        agg[c + "_mean"] = g[c].apply(lambda s: float(np.mean(np.clip(s, 0, 48))))
    kappa = _F["heat_pump_thermal_capacity_kW_per_dwelling"]
    agg["hp_capacity_kW_halloran"] = agg["dwellings"] * kappa
    if hp_capacity_kW is not None:
        agg["hp_capacity_kW_sized"] = pd.Series(np.asarray(hp_capacity_kW, float), index=flex.index).groupby(flex[by] if isinstance(by, str) else by).sum()
    return agg
