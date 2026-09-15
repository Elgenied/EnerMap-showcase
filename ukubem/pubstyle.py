"""
Publication figure style for EnerMap UBEM: one call sets matplotlib for consistent, print-ready figures
(serif-free, 300 dpi, colour-blind-safe palette), and `save_fig` writes PNG + PDF side by side.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt

PALETTE = ["#1b4f72", "#c0392b", "#27ae60", "#f39c12", "#8e44ad", "#16a085", "#7f8c8d", "#d35400"]
PACKAGE_COLOURS = {"R0": "#7f8c8d", "R1": "#f39c12", "R2": "#1b4f72"}
VECTOR_COLOURS = {"space_heat_kw": "#c0392b", "hot_water_heat_kw": "#f39c12", "non_heating_electricity_kw": "#1b4f72"}


def apply(font_size: float = 9.5) -> None:
    mpl.rcParams.update({
        "figure.dpi": 110, "savefig.dpi": 300, "font.size": font_size, "font.family": "DejaVu Sans",
        "axes.titlesize": font_size + 1, "axes.labelsize": font_size, "legend.fontsize": font_size - 1.5,
        "xtick.labelsize": font_size - 1, "ytick.labelsize": font_size - 1, "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.alpha": 0.25, "grid.linewidth": 0.6, "legend.frameon": False,
        "axes.prop_cycle": mpl.cycler(color=PALETTE), "figure.constrained_layout.use": False,
    })


def save_fig(fig, path, formats=("png", "pdf"), dpi: int = 300) -> list[Path]:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for f in formats:
        p = path.with_suffix(f".{f}")
        fig.savefig(p, dpi=dpi, bbox_inches="tight", facecolor="white")
        out.append(p)
    return out


def panel_label(ax, text: str, x: float = -0.08, y: float = 1.04) -> None:
    ax.text(x, y, text, transform=ax.transAxes, fontsize=11, fontweight="bold", va="bottom", ha="left")
