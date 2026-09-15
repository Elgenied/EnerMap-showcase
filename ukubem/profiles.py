"""
Hourly profiles, peaks and heat-pump electricity - the rules made explicit.

* Group peaks are the maximum of the TIME-ALIGNED SUM of the dwelling profiles; no external
  diversity factor is ever applied . The coincidence comes from the
  stochastic households of the CREST/CHAP generator, one draw per dwelling.
* Heat-pump electricity uses the earlier Carnot-fraction COP(T_out) (eta 0.35, floor 1, cap 5)
  at 45 C flow for space heat and 55 C for hot water; the annual accounting keeps the static SPFs.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def epw_hourly(epw_path) -> pd.DataFrame:
    """Dry-bulb temperature and global horizontal irradiance for the 8760 hours of the EPW."""
    rows = []
    with open(epw_path, "r", encoding="latin-1") as f:
        for line in f:
            p = line.split(",")
            if len(p) > 30 and p[0].isdigit():
                rows.append((int(p[0]), int(p[1]), int(p[2]), int(p[3]), float(p[6]), float(p[13])))
    d = pd.DataFrame(rows[:8760], columns=["year", "month", "day", "hour", "T_out_C", "GHI_W_m2"])
    d.index = pd.date_range("2021-01-01", periods=len(d), freq="h")
    return d


def cop_ashp(t_out, t_flow: float = 45.0) -> np.ndarray:
    """Carnot-fraction COP, kept for comparison with ukubem.heatpumps (Ruhnau / Halloran), eta = 0.35, dT floored at 5 K, COP in [1, 5]."""
    dt = np.maximum(t_flow - np.asarray(t_out, float), 5.0)
    return np.clip(0.35 * (t_flow + 273.15) / dt, 1.0, 5.0)


def hp_electric_w(q_heat_w, t_out, t_flow: float = 45.0) -> np.ndarray:
    return np.asarray(q_heat_w, float) / cop_ashp(t_out, t_flow)


def peak_stats(hourly_kW: np.ndarray, index: pd.DatetimeIndex | None = None) -> dict:
    """Peak of a summed profile: value, timestamp, hour of day, p95/p99, mean, load factor, hour-of-day of the 50 highest hours."""
    x = np.asarray(hourly_kW, float)
    idx = index if index is not None else pd.date_range("2021-01-01", periods=len(x), freq="h")
    i = int(np.nanargmax(x)); top = np.argsort(x)[-50:]
    return {"peak_kW": float(x[i]), "peak_time": str(idx[i]), "peak_hour": int(idx[i].hour), "peak_month": int(idx[i].month),
            "p99_kW": float(np.nanpercentile(x, 99)), "p95_kW": float(np.nanpercentile(x, 95)), "mean_kW": float(np.nanmean(x)),
            "load_factor": float(np.nanmean(x) / x[i]) if x[i] > 0 else np.nan,
            "top50_hour_mode": int(pd.Series(idx[top].hour).mode().iloc[0])}


def load_duration(hourly) -> np.ndarray:
    return np.sort(np.asarray(hourly, float))[::-1]


def average_day(hourly, index: pd.DatetimeIndex | None = None, months=(12, 1, 2), weekday_only: bool = False) -> np.ndarray:
    x = pd.Series(np.asarray(hourly, float), index=index if index is not None else pd.date_range("2021-01-01", periods=len(hourly), freq="h"))
    m = x.index.month.isin(months)
    if weekday_only:
        m &= x.index.dayofweek < 5
    return x[m].groupby(x.index[m].hour).mean().to_numpy()


def stack_hourly(parts_dir) -> tuple[np.ndarray, np.ndarray]:
    """Load the per-chunk hourly arrays written by `runner.simulate_stock(keep_hourly=True)` -> (ids, Q_HC_W [n, 8760])."""
    ids, arrs = [], []
    for p in sorted(Path(parts_dir).glob("part_*.npz")):
        z = np.load(p, allow_pickle=True); ids.append(z["ids"]); arrs.append(z["Q_HC_W"])
    if not arrs:
        return np.array([]), np.zeros((0, 8760), np.float32)
    return np.concatenate(ids), np.concatenate(arrs)


def scenario_electric_profiles(q_w: np.ndarray, fam: np.ndarray, t_out: np.ndarray, hp_mask: np.ndarray | None = None,
                               dhw_w: np.ndarray | None = None, t_flow_space: float = 45.0, boiler_eff: float = 0.82) -> dict:
    """
    From dwelling heat-output profiles (W, n x 8760) and system families, the aligned group profiles:
    gas boiler heat -> gas (W) for boiler dwellings; heat-pump electricity for the dwellings in `hp_mask`
    (existing heat pumps plus converted ones); direct electric heat as is.
    """
    fam = np.asarray(fam).astype(str)
    hp = np.zeros(len(fam), bool) if hp_mask is None else np.asarray(hp_mask, bool)
    boiler = np.isin(fam, ["S.GasBoiler", "S.GasCommunal", "S.OilBoiler", "S.LPGBoiler", "S.SolidFuel"]) & ~hp
    direct = np.isin(fam, ["S.ElecResistive", "S.ElecStorage"]) & ~hp
    cop = cop_ashp(t_out, t_flow_space)[None, :]
    q = np.nan_to_num(np.asarray(q_w, np.float32), nan=0.0)
    out = {"heat_W": q.sum(axis=0), "gas_W": (q[boiler].sum(axis=0) / boiler_eff) if boiler.any() else np.zeros(q.shape[1]),
           "hp_elec_W": (q[hp] / cop).sum(axis=0) if hp.any() else np.zeros(q.shape[1]),
           "direct_elec_W": q[direct].sum(axis=0) if direct.any() else np.zeros(q.shape[1]),
           "n": int(len(fam)), "n_hp": int(hp.sum()), "n_boiler": int(boiler.sum()), "n_direct": int(direct.sum())}
    out["heat_elec_W"] = out["hp_elec_W"] + out["direct_elec_W"]
    return out
