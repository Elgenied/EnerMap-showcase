"""
pyBuildingEnergy adapter for stock-scale runs.

pyBuildingEnergy simulates one building at a time and, for every call, re-computes the
ISO 52010 solar irradiance and window-shading factors from the EPW file - about half of a
16 s run. For a UBEM every dwelling shares the same site and the same surface layout
(N/E/S/W walls and windows, horizontal roof and floor), so that frame is identical across
dwellings and is computed once per worker process (`PBE.weather()`), then handed to the
engine through the `weather_sim_df` keyword added to pyBuildingEnergy for this purpose.
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd


class PBE:
    """Lazy handle on the pyBuildingEnergy package plus a per-process weather cache."""

    _pybui = None
    _weather: dict[tuple, pd.DataFrame] = {}

    @classmethod
    def load(cls, src_dir: str | os.PathLike):
        if cls._pybui is None:
            src_dir = str(src_dir)
            if src_dir not in sys.path:
                sys.path.insert(0, src_dir)
            import pybuildingenergy as pybui  # noqa: WPS433
            cls._pybui = pybui
        return cls._pybui

    @classmethod
    def weather(cls, epw: str | os.PathLike, template_bui: dict) -> pd.DataFrame:
        """ISO 52010 weather + solar frame for `template_bui`'s surface layout, cached per process."""
        key = (str(epw), _layout_key(template_bui))
        if key not in cls._weather:
            pybui = cls._pybui
            with _quiet():
                sim = pybui.ISO52016().Weather_data_bui(template_bui, str(epw), weather_source="epw").simulation_df
            cls._weather[key] = sim
        return cls._weather[key]


def _layout_key(bui: dict) -> tuple:
    """
    What the ISO 52010 frame depends on: site, building azimuth, the set of surface names /
    types / orientations, and the geometry of the transparent surfaces (window shading factors).
    Areas and wall heights do not enter the frame, so dwellings of different size share it.
    """
    parts = []
    for s in bui.get("building_surface", []):
        o = s.get("orientation", {})
        item = (s.get("name"), str(s.get("type")).lower(), o.get("azimuth"), o.get("tilt"))
        if str(s.get("type")).lower() == "transparent":
            item += (s.get("height"), s.get("width"), s.get("parapet"), bool(s.get("shading", False)))
        parts.append(item)
    b = bui.get("building", {})
    return (round(float(b.get("latitude", 0)), 3), round(float(b.get("longitude", 0)), 3),
            float(b.get("azimuth_relative_to_true_north", 0)), tuple(parts))


@contextlib.contextmanager
def _quiet():
    """Silence pyBuildingEnergy's tqdm bars and debug prints."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


def run_building(bui: dict, epw: str | os.PathLike, pybui_src: str | os.PathLike,
                 use_weather_cache: bool = True, quiet: bool = True,
                 external_internal_gains_w: np.ndarray | None = None, **kwargs):
    """
    Run EN ISO 52016-1 for one dwelling. Returns (hourly DataFrame, annual DataFrame, seconds).

    `external_internal_gains_w` is a calendar-year hourly series (8760) of sensible internal
    gains in W, e.g. from the stochastic schedule generator; pyBuildingEnergy aligns it to its
    simulation horizon (which carries a December warm-up month).
    """
    pybui = PBE.load(pybui_src)
    from pybuildingenergy.source.check_input import sanitize_and_validate_BUI
    checked, issues = sanitize_and_validate_BUI(bui, fix=True)
    errors = [i for i in issues if i["level"] == "ERROR"]
    if errors:
        raise ValueError(f"invalid BUI for {bui['building'].get('name')}: {errors}")
    kw = dict(weather_source="epw", path_weather_file=str(epw))
    if use_weather_cache:
        kw["weather_sim_df"] = PBE.weather(epw, checked)
    if external_internal_gains_w is not None:
        kw["external_internal_gains_series"] = np.asarray(external_internal_gains_w, dtype=float)
    kw.update(kwargs)
    t0 = time.time()
    ctx = _quiet() if quiet else contextlib.nullcontext()
    with ctx:
        out = pybui.ISO52016.Temperature_and_Energy_needs_calculation(checked, **kw)
    hourly, annual = out[0], out[1]
    return hourly, annual, time.time() - t0


def annual_summary(hourly: pd.DataFrame, annual: pd.DataFrame, floor_area: float) -> dict:
    """Compact per-dwelling result: needs, temperatures, balance terms (kWh/yr)."""
    q = hourly["Q_HC"].astype(float)
    heat_w = q.clip(lower=0.0)
    res = {
        "Q_H_kWh": float(heat_w.sum() / 1000.0),
        "Q_C_kWh": float(-q.clip(upper=0.0).sum() / 1000.0),
        "T_op_mean_C": float(hourly["T_op"].mean()),
        "T_op_heating_season_C": float(hourly.loc[hourly.index.month.isin([11, 12, 1, 2, 3]), "T_op"].mean()),
        "heating_hours": int((heat_w > 0).sum()),
        "peak_heat_W": float(heat_w.max()),
    }
    res["Q_H_kWh_m2"] = res["Q_H_kWh"] / floor_area if floor_area else np.nan
    for col in ("Q_solar_gains_kWh", "Q_internal_gains_kWh", "Q_tr_total_loss_kWh", "Q_ve_loss_kWh",
                "Q_ground_loss_kWh", "Q_tb_loss_kWh", "Q_tr_window_loss_kWh", "Q_tr_opaque_loss_kWh"):
        if col in annual.columns:
            res[col] = float(annual[col].iloc[0])
    return res
