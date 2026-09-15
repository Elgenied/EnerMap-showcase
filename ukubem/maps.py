"""
Council-facing maps for EnerMap UBEM (EPSG:27700).

Every map is drawn on the same base: ALL building footprints of the area - residential and
non-residential - so there are no empty spots, the ward (or LSOA) boundaries, ward labels and a
1-km scale bar. Results are painted either per building (dwelling results averaged onto their
`toid` footprint), per LSOA (choropleth) or per ward.

    layers = load_layers(CFG)                     # buildings (all), wards, town, lsoas, pv, substations
    fig, ax = plt.subplots(figsize=(9, 9))
    base_map(ax, layers, extent="wards")
    building_map(ax, layers, values_by_toid, cmap="YlOrRd", label="kWh/m2")
    finish(ax, layers, title="...")
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# palette shared by every figure of the tool
INK, INK2, MUTED = "#222222", "#555555", "#9a9a9a"
BG_NONRES, BG_RES, BG_EDGE = "#d9d9d9", "#efefef", "#ffffff"
WARD_EDGE, LSOA_EDGE, TOWN_EDGE = "#333333", "#777777", "#000000"
SEQ = "YlOrRd"; SEQ_BLUE = "Blues"; DIV = "RdBu_r"


def load_layers(cfg, town_only: bool = True) -> dict:
    """GeoDataFrames in EPSG:27700. `town_only` clips the authority footprints to the ten-ward area (+300 m)."""
    import geopandas as gpd
    L = {}
    L["wards"] = gpd.read_file(cfg.WARDS_GPKG).to_crs(27700)
    L["town"] = gpd.read_file(cfg.TOWN_GPKG).to_crs(27700) if Path(cfg.TOWN_GPKG).exists() else None
    L["lsoas"] = gpd.read_file(cfg.LSOA_SHP).to_crs(27700)
    b = gpd.read_file(cfg.BUILDINGS_GPKG).to_crs(27700)
    if town_only:
        minx, miny, maxx, maxy = L["wards"].total_bounds
        b = b.cx[minx - 300: maxx + 300, miny - 300: maxy + 300]
    L["buildings"] = b
    L["pv"] = gpd.read_file(cfg.PV_BUILDINGS_GPKG).to_crs(27700) if Path(cfg.PV_BUILDINGS_GPKG).exists() else None
    sub = Path(cfg.SUBSTATIONS_GEOJSON)
    L["substations"] = gpd.read_file(sub).to_crs(27700) if sub.exists() else None
    return L


def points_27700(df: pd.DataFrame, lon: str = "lon", lat: str = "lat"):
    import geopandas as gpd
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[lon], df[lat], crs=4326)).to_crs(27700)


def base_map(ax, layers: dict, extent: str = "wards", show_nonres: bool = True, res_color: str = BG_RES):
    """Footprints (non-residential in darker grey) + boundaries; nothing painted yet."""
    b = layers["buildings"]
    if show_nonres and "use_class_final" in b:
        nr = b[b["use_class_final"].astype(str).str.lower().str.startswith("non")]
        rs = b[~b.index.isin(nr.index)]
        nr.plot(ax=ax, color=BG_NONRES, edgecolor=BG_NONRES, linewidth=0.1, zorder=1)
        rs.plot(ax=ax, color=res_color, edgecolor=res_color, linewidth=0.1, zorder=1)
    else:
        b.plot(ax=ax, color=res_color, edgecolor=res_color, linewidth=0.1, zorder=1)
    if extent == "wards":
        layers["wards"].boundary.plot(ax=ax, color=WARD_EDGE, linewidth=0.9, zorder=5)
        minx, miny, maxx, maxy = layers["wards"].total_bounds
    else:
        layers["lsoas"].boundary.plot(ax=ax, color=LSOA_EDGE, linewidth=0.4, zorder=4)
        layers["wards"].boundary.plot(ax=ax, color=WARD_EDGE, linewidth=0.9, zorder=5)
        minx, miny, maxx, maxy = layers["lsoas"].total_bounds
    pad = 0.02 * max(maxx - minx, maxy - miny)
    ax.set_xlim(minx - pad, maxx + pad); ax.set_ylim(miny - pad, maxy + pad)
    ax.set_aspect("equal"); ax.set_axis_off()
    return ax


def building_map(ax, layers: dict, values: pd.Series, cmap: str = SEQ, label: str = "", vmin=None, vmax=None,
                 q: tuple = (0.02, 0.98), legend: bool = True, missing_color: str = BG_RES):
    """Paint residential footprints by a value indexed by `toid` (aggregate dwellings to buildings first)."""
    b = layers["buildings"]
    v = values.groupby(level=0).mean() if not values.index.is_unique else values
    g = b.join(v.rename("_v"), on="toid")
    g = g[g["_v"].notna()]
    lo = v.quantile(q[0]) if vmin is None else vmin; hi = v.quantile(q[1]) if vmax is None else vmax
    g.plot(ax=ax, column="_v", cmap=cmap, vmin=lo, vmax=hi, edgecolor="none", linewidth=0, zorder=3,
           legend=legend, legend_kwds={"label": label, "shrink": 0.55, "pad": 0.01}, missing_kwds={"color": missing_color})
    return g


def lsoa_map(ax, layers: dict, values: pd.Series, cmap: str = SEQ, label: str = "", vmin=None, vmax=None,
             legend: bool = True, alpha: float = 0.9, edge: str = LSOA_EDGE):
    """Choropleth of a value indexed by LSOA code."""
    g = layers["lsoas"].join(values.rename("_v"), on="LSOA")
    g.plot(ax=ax, column="_v", cmap=cmap, vmin=vmin, vmax=vmax, edgecolor=edge, linewidth=0.4, alpha=alpha, zorder=2,
           legend=legend, legend_kwds={"label": label, "shrink": 0.55, "pad": 0.01}, missing_kwds={"color": BG_RES, "label": "no data"})
    return g


def ward_map(ax, layers: dict, values: pd.Series, cmap: str = SEQ, label: str = "", vmin=None, vmax=None, legend: bool = True, alpha: float = 0.85):
    """Choropleth of a value indexed by ward name (WD25NM)."""
    g = layers["wards"].join(values.rename("_v"), on="WD25NM")
    g.plot(ax=ax, column="_v", cmap=cmap, vmin=vmin, vmax=vmax, edgecolor=WARD_EDGE, linewidth=0.9, alpha=alpha, zorder=2,
           legend=legend, legend_kwds={"label": label, "shrink": 0.55, "pad": 0.01}, missing_kwds={"color": BG_RES})
    return g


def ward_labels(ax, layers: dict, fontsize: int = 8, color: str = INK):
    for _, r in layers["wards"].iterrows():
        c = r.geometry.representative_point()
        ax.annotate(r["WD25NM"], (c.x, c.y), ha="center", va="center", fontsize=fontsize, color=color, zorder=9,
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.7))


def scale_bar(ax, length_m: int = 1000, loc: tuple = (0.05, 0.04)):
    x0, x1 = ax.get_xlim(); y0, y1 = ax.get_ylim()
    x = x0 + loc[0] * (x1 - x0); y = y0 + loc[1] * (y1 - y0)
    ax.plot([x, x + length_m], [y, y], color=INK, linewidth=2.5, zorder=10, solid_capstyle="butt")
    ax.annotate(f"{length_m/1000:g} km", (x + length_m / 2, y), xytext=(0, 4), textcoords="offset points", ha="center", fontsize=8, color=INK, zorder=10)


def finish(ax, layers: dict, title: str = "", labels: bool = True, bar: bool = True):
    if labels:
        ward_labels(ax, layers)
    if bar:
        scale_bar(ax)
    if title:
        ax.set_title(title, fontsize=11, loc="left")
    return ax


def save(fig, path, dpi: int = 220):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white")
    return path


def per_building(values_by_dwelling: pd.Series, inputs: pd.DataFrame, how: str = "mean") -> pd.Series:
    """Aggregate a dwelling-indexed series onto building `toid`s (mean per building by default; 'sum' for totals)."""
    t = inputs["toid"].reindex(values_by_dwelling.index)
    g = values_by_dwelling.groupby(t)
    return (g.sum() if how == "sum" else g.mean())
