"""
Stock runs on all cores, with checkpointing.

`simulate_rows` takes a slice of the dwelling-input table, builds a BUI per row (optionally
with a stochastic schedule), runs pyBuildingEnergy with the per-process weather cache and
returns one summary row per dwelling. `simulate_stock` shards a table into chunks, runs them
with joblib (loky processes) and appends each finished chunk to a parquet checkpoint so an
overnight run can be resumed.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .engine import PBE, annual_summary, run_building
from .geometry import UKGeometryAssumptions, build_bui
from .schedules import deterministic_schedule, dwelling_schedule, sample_household
from .accounting import account, family_masks, usage_for


def schedule_for_row(r: pd.Series, did, assumptions: UKGeometryAssumptions, stochastic: bool = True, seed: int = 42,
                     year: int = 2021, timestep_minutes: int = 60, stratum: int | None = None, n_strata: int = 1) -> tuple[str, dict]:
    """
    The household draw and schedule of one dwelling row, exactly as the stock runs use it (same seeds).

    Stock runs (stratum None): one random household per dwelling - the population model.
    Representatives (stratum k of n_strata): stratified household draws. Realisation k covers stratum k of a
    Latin-hypercube design over the two behavioural dimensions that dominate the heat need - the CHAP
    heating-regime share CDF and the thermostat CDF of the drawn regime - so the mean over the n_strata
    realisations estimates the archetype's expected demand without the lottery of a few random households
    (the between-draw CV of the annual heat need is about 0.5). Household composition, timer settings, season
    and occupancy stay random inside the stratum.
    """
    if stratum is None:
        rng = np.random.default_rng([seed, int(did)]); q = {}
    else:
        design = np.random.default_rng([seed, int(did), 999_983])            # the dwelling's design, shared by its strata
        perm_p, perm_s = design.permutation(n_strata), design.permutation(n_strata)
        rng = np.random.default_rng([seed, int(did), 1_000_003 + int(stratum)])
        q = {"heating_pattern_quantile": float((perm_p[int(stratum)] + rng.random()) / n_strata),
             "setpoint_quantile": float((perm_s[int(stratum)] + rng.random()) / n_strata)}
    if stochastic:
        hh = r["household_archetype"] if "household_archetype" in r and pd.notna(r["household_archetype"]) else \
            sample_household(r["accommodation_type"], r["floor_area_final"], r.get("tenure"), rng)
        sch = dwelling_schedule(str(did), hh, float(r["floor_area_final"]), year=year, seed=int(rng.integers(2**31)),
                                timestep_minutes=timestep_minutes, setback_c=float(assumptions.stochastic_setback_c), **q)
    else:
        hh = "deterministic"
        sch = deterministic_schedule(float(r["floor_area_final"]), assumptions.default_setpoint_c, assumptions.default_setback_c,
                                     assumptions.default_gains_w_m2 * assumptions.gains_scale, year)
    return hh, sch


def simulate_rows(rows: pd.DataFrame, epw: str, pybui_src: str, assumptions: UKGeometryAssumptions,
                  stochastic: bool = True, seed: int = 42, year: int = 2021, post: dict | None = None,
                  keep_hourly: bool = False, timestep_minutes: int = 60, stratum: int | None = None, n_strata: int = 1) -> list[dict]:
    """
    Simulate every row of `rows` (a DataFrame indexed by dwelling_id). Runs inside a worker.
    `stratum` / `n_strata`: stratified household draw for representatives (see `schedule_for_row`).
    `post` are the calibrated post-processing parameters (weather_scale, dhw_scale,
    elec_target_kWh_m2, boiler_eff, hp_scop): the delivered-fuel columns use exactly the same
    accounting as the archetype tier (`ukubem.accounting.account`). Raw simulated values are
    kept as Q_H_sim_kWh / dhw_generator_kWh / appliances_generator_kWh.
    """
    PBE.load(pybui_src)
    out = []
    for did, r in rows.iterrows():
        t0 = time.time()
        hh, sch = schedule_for_row(r, did, assumptions, stochastic, seed, year, timestep_minutes, stratum, n_strata)
        # BREDEM/SAP usage for this dwelling; the generator's hourly shapes are kept but their
        # annual appliance + lighting electricity is brought to the BREDEM level inside the gains series
        fam = str(r.get("systems_fam", "S.GasBoiler"))
        gc = bool(r.get("gas_connected", False)) if pd.notna(r.get("gas_connected", np.nan)) else False
        masks = family_masks([fam], [gc])
        A_floor_eng = float(np.clip(float(r["floor_area_final"]), 15.0, 1000.0))
        usage = usage_for([A_floor_eng], masks)
        gains = np.asarray(sch["gains_w"], dtype=float).copy()
        gen_elec = float(sch["elec_kWh"]) if np.isfinite(sch.get("elec_kWh", np.nan)) else np.nan
        if stochastic and np.isfinite(gen_elec):
            shape = np.asarray(sch["appliances"], dtype=float); shape = shape / shape.mean() if shape.mean() > 0 else np.ones(8760)
            delta_kWh = float(usage["elec_nonheat_kWh"][0]) - gen_elec          # BREDEM level minus generator level
            gains = gains + 0.95 * delta_kWh * 1000.0 / 8760.0 * shape         # 0.95: BREDEM S6 share of electricity that is a gain
        bui, derived = build_bui(r, assumptions, schedule=sch if stochastic else None, name=str(did))
        gains = gains * (assumptions.gains_scale if stochastic else 1.0)
        try:
            hourly, annual, secs = run_building(bui, epw, pybui_src, external_internal_gains_w=gains if stochastic else None)
            res = annual_summary(hourly, annual, derived["A_floor"])
            status = "ok"
        except Exception as exc:  # keep the stock run alive; report the failure
            res, status, secs = {"Q_H_kWh": np.nan, "Q_H_kWh_m2": np.nan}, f"error: {exc}"[:200], time.time() - t0
            hourly = None
        dhw_gen = float(sch["dhw_kWh_year"]) if np.isfinite(sch.get("dhw_kWh_year", np.nan)) else np.nan
        q_sim = float(res.get("Q_H_kWh", np.nan))
        acc = account([q_sim], [derived["A_floor"]], masks, post, usage=usage)
        deliv = {k: (float(v[0]) if k not in ("in_gas_benchmark", "gas_connected_nonheat") else bool(v[0])) for k, v in acc.items()}
        deliv["Q_H_kWh_m2"] = deliv["Q_H_kWh"] / derived["A_floor"]
        row = {"dwelling_id": did, "status": status, "seconds": secs, "household_archetype": hh,
               "stratum": -1 if stratum is None else int(stratum),
               "heating_pattern": sch.get("heating_pattern"), "setpoint_c": sch.get("setpoint_c"),
               "heating_share_of_hours": sch.get("heating_share_of_hours"), "people": sch.get("people"),
               "dhw_generator_kWh": dhw_gen, "appliances_generator_kWh": gen_elec, "systems_fam": fam,
               **{k: derived[k] for k in ("A_floor", "A_wall", "A_window", "A_roof", "A_ground", "HTC_fabric_W_K", "H_ve_W_K", "HTC_per_m2")},
               **res, "Q_H_sim_kWh": q_sim, "Q_H_sim_kWh_m2": res.get("Q_H_kWh_m2", np.nan), **deliv}
        if keep_hourly and hourly is not None:
            row["hourly_Q_HC_W"] = hourly["Q_HC"].astype(np.float32).to_numpy()
            row["hourly_T_op_C"] = hourly["T_op"].astype(np.float32).to_numpy()
        out.append(row)
    return out


def simulate_stock(inputs: pd.DataFrame, epw: str, pybui_src: str, assumptions: UKGeometryAssumptions,
                   checkpoint: str | os.PathLike, n_jobs: int = -1, chunk_size: int = 40, stochastic: bool = True,
                   seed: int = 42, year: int = 2021, post: dict | None = None, resume: bool = True,
                   timestep_minutes: int = 60, verbose: int = 5, keep_hourly: bool = False, hourly_dir=None) -> pd.DataFrame:
    """
    Run `inputs` (indexed by dwelling_id) on all cores; append finished chunks to `checkpoint`
    (parquet). With `resume=True`, dwellings already in the checkpoint are skipped. With
    `keep_hourly=True` the hourly heat output (W, float32) of every dwelling is written to
    `hourly_dir/part_XXXX.npz` (ids + Q_HC_W) in rounds, for peak and demand-curve analysis.
    """
    from joblib import Parallel, delayed
    checkpoint = Path(checkpoint)
    done = pd.read_parquet(checkpoint) if (resume and checkpoint.exists()) else pd.DataFrame()
    todo = inputs.drop(index=[i for i in done["dwelling_id"]] if len(done) else [], errors="ignore")
    if len(todo) == 0:
        return done
    chunks = [todo.iloc[i:i + chunk_size] for i in range(0, len(todo), chunk_size)]
    workers = os.cpu_count() if n_jobs in (-1, None) else n_jobs
    t0 = time.time()
    results = list(done.to_dict("records")) if len(done) else []
    # loky workers keep their weather cache between chunks; batch_size=1 keeps the checkpoint fresh
    par = Parallel(n_jobs=workers, verbose=verbose, batch_size=1, return_as="generator")
    n_done = 0
    hourly_dir = Path(hourly_dir) if hourly_dir else (checkpoint.parent / (checkpoint.stem + "_hourly"))
    if keep_hourly:
        hourly_dir.mkdir(parents=True, exist_ok=True)
    pending_ids, pending_arr, part = [], [], len(list(hourly_dir.glob("part_*.npz"))) if keep_hourly else 0

    def flush_hourly():
        nonlocal pending_ids, pending_arr, part
        if pending_arr:
            np.savez(hourly_dir / f"part_{part:04d}.npz", ids=np.array(pending_ids), Q_HC_W=np.stack(pending_arr).astype(np.float32))
            part += 1; pending_ids, pending_arr = [], []

    for res in par(delayed(simulate_rows)(c, epw, pybui_src, assumptions, stochastic, seed, year, post, keep_hourly, timestep_minutes)
                   for c in chunks):
        for row in res:
            if keep_hourly:
                arr = row.pop("hourly_Q_HC_W", None); row.pop("hourly_T_op_C", None)
                if arr is not None and len(arr) == 8760:
                    pending_ids.append(row["dwelling_id"]); pending_arr.append(np.asarray(arr, np.float32))
        results.extend(res)
        n_done += len(res)
        if n_done % (chunk_size * max(1, workers)) < chunk_size:      # every full round of workers
            pd.DataFrame(results).to_parquet(checkpoint, index=False)
            flush_hourly()
    out = pd.DataFrame(results)
    out.to_parquet(checkpoint, index=False)
    flush_hourly()
    print(f"simulated {len(todo):,} dwellings in {(time.time() - t0) / 60:.1f} min on {workers} workers "
          f"({(time.time() - t0) / max(1, len(todo)) * workers:.1f} s CPU per dwelling)")
    return out


def aggregate_lsoa(results: pd.DataFrame, labels: pd.DataFrame, bench_lsoa: pd.DataFrame) -> pd.DataFrame:
    """
    LSOA means in EnerMap's convention: gas over gas-boiler dwellings, electricity over all,
    joined to DESNZ 2021 meter means. `labels` must carry LSOA and heat_system per dwelling_id.
    """
    r = results.set_index("dwelling_id").join(labels[["LSOA", "heat_system"] + [c for c in ("in_calibration_population",) if c in labels.columns]])
    if "in_calibration_population" in r:
        r = r[r["in_calibration_population"].fillna(True).astype(bool)]           # the benchmark-year population
    member = r["in_gas_benchmark"].astype(bool) if "in_gas_benchmark" in r else r["heat_system"].eq("Gas boiler")
    gas = r.loc[member].groupby("LSOA")["delivered_gas_kWh"].mean().rename("sim_gas_mean_kWh")
    ele = r.groupby("LSOA")["delivered_elec_kWh"].mean().rename("sim_elec_mean_kWh")
    n = r.groupby("LSOA").size().rename("n_sim")
    t = pd.concat([n, gas, ele], axis=1).join(bench_lsoa.set_index("LSOA")[["gas_mean_kWh", "elec_mean_kWh", "gas_meters", "elec_meters"]])
    t["gas_ape"] = (t["sim_gas_mean_kWh"] - t["gas_mean_kWh"]).abs() / t["gas_mean_kWh"]
    t["elec_ape"] = (t["sim_elec_mean_kWh"] - t["elec_mean_kWh"]).abs() / t["elec_mean_kWh"]
    return t


def mape(t: pd.DataFrame) -> dict:
    """LSOA-level metrics (WAPE / NMBE / predictive R2 / J) plus the per-row MAPE and bias kept for continuity."""
    from . import metrics as M
    g = t.dropna(subset=["sim_gas_mean_kWh", "gas_mean_kWh"]); e = t.dropna(subset=["sim_elec_mean_kWh", "elec_mean_kWh"])
    gt_o, gt_s = g["gas_mean_kWh"] * g["gas_meters"], g["sim_gas_mean_kWh"] * g["gas_meters"]
    et_o, et_s = e["elec_mean_kWh"] * e["elec_meters"], e["sim_elec_mean_kWh"] * e["elec_meters"]
    return {"gas_MAPE": float(t["gas_ape"].mean()), "elec_MAPE": float(t["elec_ape"].mean()),
            "gas_bias": float((t["sim_gas_mean_kWh"] / t["gas_mean_kWh"]).mean() - 1),
            "elec_bias": float((t["sim_elec_mean_kWh"] / t["elec_mean_kWh"]).mean() - 1),
            "gas_WAPE_pct": M.wape_pct(gt_o, gt_s), "elec_WAPE_pct": M.wape_pct(et_o, et_s),
            "gas_NMBE_pct": M.nmbe_pct(gt_o, gt_s), "elec_NMBE_pct": M.nmbe_pct(et_o, et_s),
            "gas_R2_predictive": M.predictive_r2(g["gas_mean_kWh"], g["sim_gas_mean_kWh"]),
            "elec_R2_predictive": M.predictive_r2(e["elec_mean_kWh"], e["sim_elec_mean_kWh"]),
            "J_gas": M.weighted_sq_rel_error(g["gas_mean_kWh"], g["sim_gas_mean_kWh"], g["gas_meters"]),
            "J_elec": M.weighted_sq_rel_error(e["elec_mean_kWh"], e["sim_elec_mean_kWh"], e["elec_meters"])}
