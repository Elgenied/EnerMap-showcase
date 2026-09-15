"""
Calibration of the archetype-tier UBEM against DESNZ sub-national consumption.

Bilevel, derivative-free, following Dogan et al.
(2025) eq. (10)-(11):

* outer loop (Py-BOBYQA, bounded): the **physics** parameters that need a re-simulation of the
  representative dwellings — envelope scalers, infiltration scaler, internal-gains scaler and a
  thermostat offset;
* inner loop (scipy, bounded): the **post-processing** parameters that act on simulated needs —
  weather-year scaler on space heat, DHW scaler, appliance electricity target, boiler
  efficiency and heat-pump SPF — solved to optimality for every outer evaluation in seconds.

Objective: mean of the LSOA gas MAPE (gas-boiler dwellings vs DESNZ mains-gas meter means) and
the LSOA electricity MAPE (all dwellings), as EnerMap. Household draws are fixed by seed so the
objective is deterministic. Every evaluation is appended to a history CSV so a run can resume.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .geometry import UKGeometryAssumptions
from .runner import aggregate_lsoa, mape, simulate_rows
from .accounting import account, family_masks, usage_for
from . import metrics as M
from . import params

_CAL = params.load("demand_model", "calibration")
PHYS_BOUNDS = {k: (v["lower"], v["upper"], v["initial"]) for k, v in _CAL["physics_bounds"].items()}   # parameters/demand_model/calibration.json
POST_BOUNDS = {}                       # nothing is fitted in the inner loop any more (see accounting.py)
# Post-processing quantities fixed from evidence (annual meters cannot identify them against the
# space-heat level; stock-level calibrations show the same confounding of setpoint and boiler efficiency):
#  * weather_scale 1.0 - Farnborough TMYx 2007-2021, 2021 a near-normal UK heating year (Energy Trends);
#  * boiler_eff 0.82 - EST 2009 in-situ trial (condensing regular 85.3 %, combi 82.5 %) weighted by the
#    EHS 2021 boiler mix (SEDBUK-based registries use 0.85; the difference is stated, not fitted);
#  * hp_scop 2.8 - Electrification of Heat ASHP median SPF_H4 (2.8 space / 2.0 DHW);
#  * hot water, appliances, lighting, cooking - BREDEM/SAP usage model (ukubem.usage),
#    so `dhw_scale` stays 1.0 and there is no appliance electricity target.
POST_FIXED = dict(_CAL["fixed_post_processing"])   # nothing fitted in post-processing; sources in parameters/demand_model/calibration.json


def make_representatives(inputs: pd.DataFrame, n_size_classes: int = 3) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Archetype tier: terciles of floor area inside every construction sub-archetype; the
    representative of a class is the dwelling closest to its median floor area.
    Returns (inputs with a `size_class` column, representatives table).
    """
    labels_ = ["S", "M", "L"][:n_size_classes]
    inp = inputs.copy()
    inp["size_class"] = inp.groupby("construction_sa")["floor_area_final"].transform(
        lambda s: pd.qcut(s.rank(method="first"), n_size_classes, labels=labels_).astype(str) if len(s) >= 3 * n_size_classes else "M").astype(str)
    reps = []
    for (sa, sc), g in inp.groupby(["construction_sa", "size_class"], observed=True):
        med = g["floor_area_final"].median()
        reps.append({"construction_sa": sa, "size_class": sc, "n": len(g), "dwelling_id": int((g["floor_area_final"] - med).abs().idxmin())})
    return inp, pd.DataFrame(reps)


def _stock_delivered(inputs: pd.DataFrame, inten: pd.DataFrame, post: dict, heat_factor=None) -> pd.DataFrame:
    """
    Per-dwelling delivered energy from archetype space-heat intensities (archetype tier) with the
    same `ukubem.accounting.account` rule and BREDEM usage as the direct dwelling runs.
    `heat_factor`: optional per-dwelling multiplier of the space heat (e.g. the longer heating hours of
    heat-pump households in electrification scenarios, parameters/heat_pumps/operation.json).
    """
    m = inputs.join(inten.set_index(["construction_sa", "size_class"])[["Q_H_kWh_m2"]], on=["construction_sa", "size_class"])
    area = m["floor_area_final"].to_numpy(dtype=float)
    fam = m["systems_fam"].astype(str).to_numpy()
    gc = m["gas_connected"].fillna(False).to_numpy(dtype=bool) if "gas_connected" in m else None
    masks = family_masks(fam, gc)
    hf = np.ones(len(m)) if heat_factor is None else pd.Series(heat_factor).reindex(m.index).fillna(1.0).to_numpy(dtype=float)
    acc = account(m["Q_H_kWh_m2"].to_numpy(dtype=float) * area * hf, area, masks, post)
    out = pd.DataFrame({**acc, "fuel": masks["fuel"], "systems_fam": fam, "floor_area_final": area,
                        "Q_H_kWh_m2": acc["Q_H_kWh"] / area}, index=m.index)
    return out.reset_index()


def objective_from_stock(stock: pd.DataFrame, labels: pd.DataFrame, bench_lsoa: pd.DataFrame) -> tuple[float, dict]:
    t = aggregate_lsoa(stock, labels, bench_lsoa)
    m = mape(t)
    return 0.5 * (m["gas_MAPE"] + m["elec_MAPE"]), m


class _FastObjective:
    """
    The calibration objective as a pure-numpy function of the post-processing parameters for one
    fixed set of archetype intensities: J = J_gas + lambda * J_elec with
    J_fuel = sum_L w_L ((sim_L - obs_L) / obs_L)^2, w_L = meters_L / sum meters.
    Population: dwellings with `in_calibration_population` (the benchmark-year population) and, when
    `lsoas` is given, only those LSOAs (spatial cross-validation folds). Gas means are taken over
    the gas-benchmark members (gas-heated non-communal + gas-connected non-gas-heated dwellings).
    """

    def __init__(self, inputs: pd.DataFrame, inten: pd.DataFrame, labels: pd.DataFrame, bench_lsoa: pd.DataFrame,
                 lsoas=None, lam: float = 1.0):
        m = inputs.join(inten.set_index(["construction_sa", "size_class"])[["Q_H_kWh_m2"]], on=["construction_sa", "size_class"])
        if "in_calibration_population" in m:
            m = m[m["in_calibration_population"].fillna(True).astype(bool)]
        lab = labels.reindex(m.index)
        lsoa = lab["LSOA"].astype(str)
        if lsoas is not None:
            keep = lsoa.isin(set(map(str, lsoas))).to_numpy(); m = m[keep]; lab = lab[keep]; lsoa = lsoa[keep]
        self.lam = lam
        self.area = m["floor_area_final"].to_numpy(float)
        self.q0 = m["Q_H_kWh_m2"].to_numpy(float) * self.area
        gc = m["gas_connected"].fillna(False).to_numpy(dtype=bool) if "gas_connected" in m else None
        self.masks = family_masks(m["systems_fam"].astype(str).to_numpy(), gc)
        self.usage = usage_for(self.area, self.masks)
        self.gas_dw = self.masks["in_gas_benchmark"]
        codes, uniq = pd.factorize(lsoa)
        self.codes = codes; self.n_lsoa = len(uniq); self.lsoas = np.asarray(uniq)
        b = bench_lsoa.set_index("LSOA").reindex(uniq)
        self.gas_obs = b["gas_mean_kWh"].to_numpy(float); self.elec_obs = b["elec_mean_kWh"].to_numpy(float)
        self.gas_w = b["gas_meters"].to_numpy(float); self.elec_w = b["elec_meters"].to_numpy(float)
        self.n_all = np.bincount(codes, minlength=self.n_lsoa).astype(float)
        self.n_gas = np.bincount(codes[self.gas_dw], minlength=self.n_lsoa).astype(float)

    def delivered(self, post: dict) -> tuple[np.ndarray, np.ndarray]:
        acc = account(self.q0, self.area, self.masks, post, usage=self.usage)
        return acc["delivered_gas_kWh"], acc["delivered_elec_kWh"]

    def lsoa_means(self, post: dict) -> tuple[np.ndarray, np.ndarray]:
        gas, elec = self.delivered(post)
        g = np.bincount(self.codes[self.gas_dw], weights=gas[self.gas_dw], minlength=self.n_lsoa) / np.maximum(self.n_gas, 1)
        e = np.bincount(self.codes, weights=elec, minlength=self.n_lsoa) / np.maximum(self.n_all, 1)
        return g, e

    def metrics(self, post: dict) -> dict:
        g, e = self.lsoa_means(post)
        okg = np.isfinite(self.gas_obs) & (self.n_gas > 0); oke = np.isfinite(self.elec_obs) & (self.n_all > 0)
        J_gas = M.weighted_sq_rel_error(self.gas_obs[okg], g[okg], self.gas_w[okg])
        J_elec = M.weighted_sq_rel_error(self.elec_obs[oke], e[oke], self.elec_w[oke])
        # WAPE / NMBE on LSOA totals (mean x meters) = meter-weighted errors
        gt_o, gt_s = self.gas_obs[okg] * self.gas_w[okg], g[okg] * self.gas_w[okg]
        et_o, et_s = self.elec_obs[oke] * self.elec_w[oke], e[oke] * self.elec_w[oke]
        return {"J": J_gas + self.lam * J_elec, "J_gas": J_gas, "J_elec": J_elec,
                "gas_WAPE_pct": M.wape_pct(gt_o, gt_s), "elec_WAPE_pct": M.wape_pct(et_o, et_s),
                "gas_NMBE_pct": M.nmbe_pct(gt_o, gt_s), "elec_NMBE_pct": M.nmbe_pct(et_o, et_s),
                "gas_R2_predictive": M.predictive_r2(self.gas_obs[okg], g[okg]), "elec_R2_predictive": M.predictive_r2(self.elec_obs[oke], e[oke]),
                "gas_MAPE": float(np.mean(np.abs(g[okg] - self.gas_obs[okg]) / self.gas_obs[okg])),
                "elec_MAPE": float(np.mean(np.abs(e[oke] - self.elec_obs[oke]) / self.elec_obs[oke])),
                "gas_bias": float((g[okg] / self.gas_obs[okg]).mean() - 1), "elec_bias": float((e[oke] / self.elec_obs[oke]).mean() - 1),
                "n_lsoa": int(okg.sum())}

    def __call__(self, post: dict) -> float:
        return self.metrics(post)["J"]


def fit_post_params(inputs, inten, labels, bench_lsoa, x0: dict | None = None, lsoas=None) -> tuple[dict, float, dict]:
    """Inner optimisation of the post-processing parameters (bounded Powell on the fast objective)."""
    from scipy.optimize import minimize
    names = list(POST_BOUNDS)
    fobj = _FastObjective(inputs, inten, labels, bench_lsoa, lsoas=lsoas)
    fixed = dict(POST_FIXED)
    if not names:                                   # nothing to fit: evaluate the fixed post-processing
        metrics = fobj.metrics(fixed)
        return dict(fixed), metrics["J"], metrics
    lo = np.array([POST_BOUNDS[k][0] for k in names]); hi = np.array([POST_BOUNDS[k][1] for k in names])
    start = np.array([(x0 or {}).get(k, POST_BOUNDS[k][2]) for k in names])

    def f(x):
        return fobj({**fixed, **dict(zip(names, np.clip(x, lo, hi)))})

    res = minimize(f, start, method="Powell", bounds=list(zip(lo, hi)), options={"xtol": 1e-3, "ftol": 1e-5, "maxfev": 2000})
    post = {**fixed, **{k: float(v) for k, v in zip(names, np.clip(res.x, lo, hi))}}
    metrics = fobj.metrics(post)
    return post, metrics["J"], metrics


@dataclass
class ArchetypeCalibrator:
    inputs: pd.DataFrame                   # dwelling_inputs with construction_sa, size_class, systems_fam, floor_area_final
    labels: pd.DataFrame                   # LSOA, heat_system per dwelling_id
    bench_lsoa: pd.DataFrame
    reps: pd.DataFrame                     # representative rows: dwelling_id, construction_sa, size_class, n
    epw: str
    pybui_src: str
    base: UKGeometryAssumptions = field(default_factory=UKGeometryAssumptions)
    n_real: int = 2
    seed: int = 42
    n_jobs: int = -1
    history_path: Path | None = None
    stochastic: bool = True
    history: list = field(default_factory=list)
    post_last: dict | None = None
    lsoas: object = None                   # restrict the objective to these LSOAs (cross-validation training folds)
    stratified: bool = True                # representatives: stratified household draws (runner.schedule_for_row), one stratum per realisation
    sim_cache: dict | None = None          # optional {phys key: intensities}: the simulation does not depend on the objective's LSOAs, so CV folds share it

    def assumptions(self, phys: dict) -> UKGeometryAssumptions:
        # the calibrated scalers replace the base values: scenario changes to any of them must be
        # passed through `phys` (e.g. phys["infil_scale"] * airtightness_factor), never via `base`
        for k in ("wall_u_scale", "roof_u_scale", "window_u_scale", "infil_scale", "gains_scale", "setpoint_offset_c"):
            if abs(float(getattr(self.base, k)) - float(UKGeometryAssumptions.__dataclass_fields__[k].default)) > 1e-12:
                raise ValueError(f"base.{k} would be overwritten by phys[{k!r}]; apply scenario scalers through phys")
        return self.base.with_(wall_u_scale=phys["wall_u_scale"], roof_u_scale=phys["roof_u_scale"],
                               window_u_scale=phys["window_u_scale"], infil_scale=phys["infil_scale"],
                               gains_scale=phys["gains_scale"], setpoint_offset_c=phys["setpoint_offset_c"],
                               stochastic_setback_c=phys.get("setback_c", self.base.stochastic_setback_c))

    def simulate(self, phys: dict) -> pd.DataFrame:
        """Intensities of the representatives under `phys`: mean over n_real (stratified) household draws, fixed seeds -> deterministic."""
        from joblib import Parallel, delayed
        A = self.assumptions(phys)
        rows = self.inputs.loc[self.reps["dwelling_id"].to_numpy()]
        n_real = self.n_real if self.stochastic else 1
        key_c = tuple((k, round(float(phys[k]), 6)) for k in sorted(phys)) + (n_real, self.seed, self.stratified)
        if self.sim_cache is not None and key_c in self.sim_cache:
            return self.sim_cache[key_c].copy()
        # one task per (dwelling, realisation) so the 32 workers stay balanced to the last simulation; stratified
        # realisations share the base seed and differ by stratum, plain ones use the seed + 1000 k convention
        tasks = [(rows.iloc[i:i + 1], self.seed if self.stratified else self.seed + 1000 * k, k if self.stratified else None)
                 for k in range(n_real) for i in range(len(rows))]
        res = Parallel(n_jobs=self.n_jobs, verbose=0, batch_size=1)(
            delayed(simulate_rows)(c, self.epw, self.pybui_src, A, self.stochastic, s, 2021, None, False, 60, st, n_real) for c, s, st in tasks)
        sto = pd.DataFrame(sum(res, []))
        key = self.reps.set_index("dwelling_id")[["construction_sa", "size_class"]]
        inten = (sto.groupby("dwelling_id").agg(Q_H_kWh_m2=("Q_H_sim_kWh_m2", "mean"), dhw_kWh=("dhw_kWh", "mean"),
                                                appliances_kWh=("appliances_kWh", "mean"), T_op_winter=("T_op_heating_season_C", "mean"),
                                                heating_share=("heating_share_of_hours", "mean"))
                    .join(key))
        if self.sim_cache is not None:
            self.sim_cache[key_c] = inten.copy()
        return inten

    def evaluate(self, x: np.ndarray) -> float:
        names = list(PHYS_BOUNDS)
        phys = dict(zip(names, [float(v) for v in x]))
        t0 = time.time()
        inten = self.simulate(phys)
        post, obj, metrics = fit_post_params(self.inputs, inten, self.labels, self.bench_lsoa, self.post_last, lsoas=self.lsoas)
        self.post_last = post
        rec = {"eval": len(self.history) + 1, "objective": obj, **metrics, **phys, **post,
               "T_op_winter_mean": float(inten["T_op_winter"].mean()), "seconds": time.time() - t0}
        self.history.append(rec)
        if self.history_path:
            pd.DataFrame(self.history).to_csv(self.history_path, index=False)
        print(f"eval {rec['eval']:3d}  J {obj:.4f}  gas WAPE {metrics['gas_WAPE_pct']:.1f}% NMBE {metrics['gas_NMBE_pct']:+.1f}%  "
              f"elec WAPE {metrics['elec_WAPE_pct']:.1f}% NMBE {metrics['elec_NMBE_pct']:+.1f}%  "
              f"phys {' '.join(f'{k[:6]}={v:.2f}' for k, v in phys.items())}  post {' '.join(f'{k[:6]}={v:.2f}' for k, v in post.items())}  "
              f"[{rec['seconds']:.0f} s]", flush=True)
        return obj

    def run(self, maxfun: int = 40, rhobeg: float = 0.1, x0: dict | None = None):
        import pybobyqa
        names = list(PHYS_BOUNDS)
        lo = np.array([PHYS_BOUNDS[k][0] for k in names]); hi = np.array([PHYS_BOUNDS[k][1] for k in names])
        start = np.array([(x0 or {}).get(k, PHYS_BOUNDS[k][2]) for k in names])
        # BOBYQA works in the parameter's own units: scale so a step of rhobeg is comparable across parameters
        scale = hi - lo
        f = lambda z: self.evaluate(lo + z * scale)  # noqa: E731
        z0 = (start - lo) / scale
        sol = pybobyqa.solve(f, z0, bounds=(np.zeros(len(names)), np.ones(len(names))), maxfun=maxfun,
                             rhobeg=rhobeg, rhoend=1e-3, seek_global_minimum=False, objfun_has_noise=False, print_progress=False)
        best = dict(zip(names, lo + sol.x * scale))
        return best, sol

    def best(self) -> dict:
        h = pd.DataFrame(self.history)
        return h.loc[h["objective"].idxmin()].to_dict()


def evaluate_on(inputs, inten, labels, bench_lsoa, post, lsoas=None) -> dict:
    """Metrics of one set of intensities on a subset of LSOAs (cross-validation test folds)."""
    return _FastObjective(inputs, inten, labels, bench_lsoa, lsoas=lsoas).metrics(post)
