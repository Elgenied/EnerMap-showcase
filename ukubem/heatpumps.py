"""
Heat-pump households heat differently from gas-boiler households, so the scenarios that electrify heating do
not reuse the gas-era hourly shape. This module carries the two empirical pieces EnerMap uses for that:

* the **within-day shape** of heat-pump space heat per outdoor-temperature band from the GB field trials
  (Watson, Lomas & Buswell 2021, Energy and Buildings 238, 110777; RHPP trial 2012-15, half-hourly profiles
  normalised to 1 per day, eight 3 K bands of the mean daily outdoor temperature, separately for ASHP and
  GSHP) - `parameters/heat_pumps/watson2021_heat_pump_profiles.json`;
* the **hourly COP** as a quadratic in the source-sink temperature difference (Ruhnau, Hirth & Praktiknjo
  2019, Scientific Data 6:189), with radiator sink 40 - 1.0 T_out, minimum 15 K, as applied by Halloran
  (2024, DPhil thesis, University of Oxford, section 3.2.5) - `parameters/heat_pumps/cop_model.json`.

The *level* of heat stays EnerMap's own: each dwelling's calibrated space heat (its fabric, its weather, the
calibrated behaviour) sets the day totals; the trial profile only redistributes a day's heat within the day, and
the trials' finding that heat-pump households heat longer enters as an annual uplift (+8 %, RHPP mix vs EDRP
gas). Watson's daily regressions are kept as an optional within-year shape (`daily_source="watson"`).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import params

_W = params.load("heat_pumps", "watson2021_heat_pump_profiles")
_C = params.load("heat_pumps", "cop_model")
_O = params.load("heat_pumps", "operation")

BAND_LOWER = np.array([b["lower"] for b in _W["temperature_bands_C"]], dtype=float)     # -4.5, -1.5, ..., 16.5
BAND_LABELS = [b["label"] for b in _W["temperature_bands_C"]]
KINDS = tuple(_W["space_heat_profiles"].keys())


# ---------------------------------------------------------------- profiles
def halfhourly_shape(kind: str = "ASHP") -> np.ndarray:
    """(8 bands, 48 half-hours) normalised space-heat profiles of the trial; each row sums to 1."""
    return np.asarray(_W["space_heat_profiles"][kind], dtype=float)


def hourly_shape(kind: str = "ASHP") -> np.ndarray:
    """(8 bands, 24 hours): the two half-hours ending :30 and :00 of each clock hour summed."""
    hh = halfhourly_shape(kind)
    return hh.reshape(hh.shape[0], 24, 2).sum(axis=2)


def dhw_hourly_shape() -> np.ndarray:
    return np.asarray(_W["dhw_profile"], dtype=float).reshape(24, 2).sum(axis=1)


def band_index(t_daily_mean) -> np.ndarray:
    """Temperature band of each day (0 = coldest, below -1.5 C; 7 = above 16.5 C)."""
    t = np.asarray(t_daily_mean, dtype=float)
    return np.clip(np.searchsorted(BAND_LOWER, t, side="right") - 1, 0, len(BAND_LOWER) - 1)


def daily_mean_temperature(t_out_hourly) -> np.ndarray:
    t = np.asarray(t_out_hourly, dtype=float)
    return t.reshape(len(t) // 24, 24).mean(axis=1)


def effective_temperature(t_daily_mean, weight_today: float | None = None) -> np.ndarray:
    """Watson's effective temperature: today's mean weighted with yesterday's effective value (National Grid method)."""
    w = _O["effective_temperature_weight_today"] if weight_today is None else weight_today
    t = np.asarray(t_daily_mean, dtype=float); e = np.empty_like(t); e[0] = t[0]
    for i in range(1, len(t)):
        e[i] = w * t[i] + (1 - w) * e[i - 1]
    return e


def daily_regression_kWh(t_effective, group: str = "100% ASHP", quantity: str = "Space heat demand") -> np.ndarray:
    """Watson's piece-wise linear daily heat per dwelling (kWh/day) for a heating-pattern or heat-pump group."""
    rows = [r for r in _W["daily_regressions_kWh_per_dwelling"] if r["quantity"] == quantity and r["group"] == group]
    if not rows:
        raise KeyError(f"no regression for {quantity!r} / {group!r}")
    below = next(r for r in rows if r["side"] == "below"); above = next(r for r in rows if r["side"] == "above")
    t = np.asarray(t_effective, dtype=float)
    q = np.where(t < below["break_C"], below["gradient_kWh_per_K"] * t + below["intercept_kWh"],
                 above["gradient_kWh_per_K"] * t + above["intercept_kWh"])
    return np.clip(q, 0.0, None)


def heat_pump_hourly(q_hourly_W, t_out_hourly, kind: str | None = None, daily_source: str | None = None,
                     uplift: float | None = None) -> np.ndarray:
    """
    Space heat of a heat-pump dwelling, hourly W (8760), from the calibrated hourly space heat of the same dwelling.

    daily_source "ubem": every day keeps its calibrated day total; "watson": day totals follow the trial's daily
    regression against the effective temperature, scaled to the calibrated annual. The within-day shape is the
    trial profile of the day's temperature band (ASHP or GSHP); `uplift` multiplies the annual heat (heat-pump
    households heat longer; None = parameters/heat_pumps/operation.json).
    """
    kind = kind or _O["heat_pump_kind_default"]; daily_source = daily_source or _O["daily_total_source"]
    uplift = _O["annual_heat_uplift_heat_pump_households"] if uplift is None else uplift
    q = np.asarray(q_hourly_W, dtype=float); n_days = len(q) // 24
    daily_Wh = q.reshape(n_days, 24).sum(axis=1)
    t_daily = daily_mean_temperature(t_out_hourly)
    if daily_source == "watson":
        reg = daily_regression_kWh(effective_temperature(t_daily), group="100% " + kind)
        daily_Wh = reg / max(reg.sum(), 1e-9) * daily_Wh.sum()
    shape = hourly_shape(kind)[band_index(t_daily)]                    # (days, 24)
    return (daily_Wh[:, None] * shape * uplift).reshape(-1)


def heat_pump_group_profiles(profiles: dict, t_out_hourly, **kw) -> dict:
    """Apply `heat_pump_hourly` to every group profile (dict of 8760 W/m2 arrays), e.g. the representatives' groups."""
    return {g: heat_pump_hourly(v, t_out_hourly, **kw) for g, v in profiles.items()}


# ---------------------------------------------------------------- COP
def sink_temperature(t_out_hourly, emitter: str | None = None) -> np.ndarray:
    e = _C["sink_temperature_C"][emitter or _C["default_emitter"]]
    return e["intercept"] + e["slope_per_K_outdoor"] * np.asarray(t_out_hourly, dtype=float)


def ground_temperature(t_out_hourly, damping: float = 0.15, lag_days: int = 30) -> np.ndarray:
    """Soil temperature at 1-3 m from the air temperature: annual mean plus a damped, lagged seasonal swing (approximation)."""
    t = pd.Series(np.asarray(t_out_hourly, dtype=float))
    seasonal = t.rolling(24 * 30, center=True, min_periods=1).mean().shift(24 * lag_days).bfill()
    return (t.mean() + damping * (seasonal - t.mean())).to_numpy()


def cop(t_source, t_sink, kind: str | None = None) -> np.ndarray:
    """Ruhnau et al. (2019) quadratic COP in dT = T_sink - T_source (dT >= 15 K), as in Halloran (2024) eq. 3.7."""
    a, b, c = _C["cop_quadratic_in_delta_T"][kind or _C["default_kind"]]
    dt = np.maximum(np.asarray(t_sink, dtype=float) - np.asarray(t_source, dtype=float), _C["min_delta_T_K"])
    return np.maximum(a + b * dt + c * dt ** 2, _C["cop_floor"])


def cop_hourly(t_out_hourly, kind: str | None = None, emitter: str | None = None, t_ground=None) -> np.ndarray:
    kind = kind or _C["default_kind"]
    source = np.asarray(t_out_hourly, dtype=float) if kind == "ASHP" else (ground_temperature(t_out_hourly) if t_ground is None else np.asarray(t_ground, float))
    return cop(source, sink_temperature(t_out_hourly, emitter), kind)


def hp_electric_w(heat_W, t_out_hourly, kind: str | None = None, emitter: str | None = None, t_ground=None) -> np.ndarray:
    """Hourly heat-pump electricity (W) for an hourly heat demand (W): heat / COP(T_out)."""
    return np.asarray(heat_W, dtype=float) / cop_hourly(t_out_hourly, kind, emitter, t_ground)


def seasonal_cop(heat_W, elec_W) -> float:
    """Heat-weighted annual COP implied by the hourly model (to compare with the evidence-based SPF)."""
    h, e = float(np.sum(heat_W)), float(np.sum(elec_W))
    return h / e if e > 0 else np.nan


def annual_dhw_shape(n_hours=8760):
    """Configured daily DHW shape, normalized to one over a non-leap model year."""
    if n_hours != 8760:
        raise ValueError('The current demand framework requires 8760 hourly steps')
    shape = np.tile(dhw_hourly_shape(), 365).astype(float)
    if not np.isfinite(shape).all() or np.any(shape < 0) or shape.sum() <= 0:
        raise ValueError('Invalid configured DHW shape')
    return shape / shape.sum()


def hourly_electricity(space_heat_kWh, dhw_system_kWh, t_out_hourly):
    """Single authoritative ASHP accounting path; return hourly space and DHW electricity.

    Space heat follows supplied hourly useful heat. System-side DHW retains its annual
    requirement (including configured losses, excluding electric showers), distributed
    with the configured normalized daily shape. Radiator sink 40-Tout; DHW sink 50 C.
    The unresolved existing HP family uses the ASHP curve. No seasonal-factor rescaling.
    """
    q = np.asarray(space_heat_kWh, float)
    t = np.asarray(t_out_hourly, float)
    if q.shape != (8760,) or t.shape != q.shape or not np.isfinite(q).all() or np.any(q < 0):
        raise ValueError('Expected finite nonnegative hourly heat and 8760 aligned temperatures')
    return q / cop_hourly(t, kind='ASHP', emitter='radiator'), float(dhw_system_kWh) * annual_dhw_shape() / cop_hourly(t, kind='ASHP', emitter='dhw')


def profile_summary(t_out_hourly, kind: str = "ASHP") -> pd.DataFrame:
    """The banded hourly shapes as a table (for figures and the parameters documentation)."""
    return pd.DataFrame(hourly_shape(kind).T, columns=BAND_LABELS, index=pd.Index(range(24), name="hour"))
