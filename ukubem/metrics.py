"""
Evaluation metrics (WAPE, NMBE, predictive R2) so that every notebook reports the same
quantities: WAPE and NMBE (never per-row MAPE as the headline), the PREDICTIVE R2
(1 - SSE/SST about the 1:1 line) and, reported only alongside it, the squared correlation.
"""
from __future__ import annotations

import numpy as np


def _clean(obs, pred):
    o = np.asarray(obs, dtype=float); p = np.asarray(pred, dtype=float)
    m = np.isfinite(o) & np.isfinite(p)
    return o[m], p[m]


def predictive_r2(obs, pred) -> float:
    o, p = _clean(obs, pred)
    sst = ((o - o.mean()) ** 2).sum()
    return float(1.0 - ((o - p) ** 2).sum() / sst) if sst > 0 else np.nan


def squared_correlation(obs, pred) -> float:
    o, p = _clean(obs, pred)
    return float(np.corrcoef(o, p)[0, 1] ** 2) if len(o) > 2 else np.nan


def nmbe_pct(obs, pred) -> float:
    o, p = _clean(obs, pred)
    return float(100.0 * (p - o).sum() / o.sum()) if o.sum() else np.nan


def wape_pct(obs, pred) -> float:
    o, p = _clean(obs, pred)
    return float(100.0 * np.abs(p - o).sum() / o.sum()) if o.sum() else np.nan


def mape_pct(obs, pred) -> float:
    """Per-row MAPE, kept for continuity with the earlier runs of this tool (not the headline)."""
    o, p = _clean(obs, pred)
    return float(100.0 * (np.abs(p - o) / o).mean()) if len(o) else np.nan


def weighted_sq_rel_error(obs, pred, weights) -> float:
    """The calibration objective term: sum_L w_L ((pred_L - obs_L) / obs_L)^2, w normalised."""
    o = np.asarray(obs, float); p = np.asarray(pred, float); w = np.asarray(weights, float)
    m = np.isfinite(o) & np.isfinite(p) & np.isfinite(w) & (o > 0)
    w = w[m] / w[m].sum()
    return float((w * ((p[m] - o[m]) / o[m]) ** 2).sum())


def summary(obs, pred, weights=None, label: str = "") -> dict:
    out = {"label": label, "n": int(len(_clean(obs, pred)[0])), "WAPE_pct": wape_pct(obs, pred),
           "NMBE_pct": nmbe_pct(obs, pred), "MAPE_pct": mape_pct(obs, pred),
           "R2_predictive": predictive_r2(obs, pred), "r2_correlation": squared_correlation(obs, pred)}
    if weights is not None:
        out["J"] = weighted_sq_rel_error(obs, pred, weights)
    return out
