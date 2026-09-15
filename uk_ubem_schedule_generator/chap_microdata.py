"""
CHAP microdata extractor.

Reads the CREST Heat and Power (CHAP) model workbook (McKenna, Higginson, Grunewald & Darby, 2018,
Energy and Buildings, doi:10.1016/j.enbuild.2018.02.051; `CHAP_model_1.0.xlsm`) and stores the
behavioural microdata the schedule generator samples from:

* `SH_HeatingPattern_Data` - for every EFUS heating-regime category (1, 2, 3, 5, 6, 8; 0 = occupancy
  driven) the empirical distributions of timer START times (96 x 15-min bins) and DURATIONS
  (15-min units) per heating period and weekday / weekend, the THERMOSTAT temperature distribution
  per category (integer degC bins), the category counts and shares (EFUS 2011, n = 2,142) and the
  heating-season start-month and length marginals;
* `tpmN_wd` / `tpmN_we` - the CREST four-state occupancy model: 144 ten-minute transition
  probability matrices per household size N = 1..6 over the combined states "<at home><active>";
* `starting_states` - the initial combined-state distributions per N, weekday and weekend;
* `prob_mean_values` - mean active occupancy per household size (validation reference).

Run once:  python chap_microdata.py "C:/path/CHAP_model_1.0.xlsm"
It writes `chap_microdata.npz` (arrays) and `chap_microdata.json` (labels, shares, descriptions)
next to this file; `load()` reads them back (the workbook itself is not needed at run time).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
NPZ_PATH = HERE / "chap_microdata.npz"
JSON_PATH = HERE / "chap_microdata.json"

# EFUS category number -> generator pattern id (categories 4 and 7 have no timing data in CHAP)
CATEGORY_TO_PATTERN = {1: "P1_ALL_DAY_FROM_WAKE", 2: "P2_ALL_DAY_AND_NIGHT", 3: "P3_EVENING_SUSTAINED",
                       5: "P5_TWO_SHORT", 6: "P6_MORNING_SHORT_EVENING_SUSTAINED", 8: "P8_THREE_PERIODS",
                       0: "P9_ACTIVE_OCCUPANCY"}
PERIODS = {1: 1, 2: 0, 3: 1, 5: 2, 6: 2, 8: 3}          # timer periods per category (2 = continuous)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


def extract(xlsm_path) -> tuple[dict, dict]:
    import openpyxl
    wb = openpyxl.load_workbook(str(xlsm_path), read_only=True, data_only=True, keep_vba=False)
    arrays, meta = {}, {"source": str(xlsm_path), "categories": {}, "notes": {}}

    # ---- heating patterns ---------------------------------------------------------------------
    rows = list(wb["SH_HeatingPattern_Data"].iter_rows(values_only=True))
    by_label = {}
    section = None
    for r in rows[1:]:
        lab = r[0]
        if lab is None:
            continue
        lab = str(lab).strip()
        if lab in ("Heating duration", "Thermostat temperature", "Heating season start", "Heating season length"):
            section = lab; continue
        key = lab if lab != "All" else f"All|{section}"
        by_label[key] = [_num(v) for v in r[1:97]]
    bins15 = np.arange(96) * 0.25                                          # start of each 15-minute bin, hours
    for cat, pid in CATEGORY_TO_PATTERN.items():
        info = {"pattern_id": pid, "periods": PERIODS.get(cat, 0)}
        for p in range(1, PERIODS.get(cat, 0) + 1):
            for day in ("WD", "WE"):
                s = np.nan_to_num(np.asarray(by_label[f"{cat}_P{p}_{day}_start"], float))
                d = np.nan_to_num(np.asarray(by_label[f"{cat}_P{p}_{day}_dur"], float))
                if s.sum() > 0:
                    arrays[f"start|{cat}|{p}|{day}"] = s / s.sum()
                if d.sum() > 0:
                    arrays[f"dur|{cat}|{p}|{day}"] = d / d.sum()
        t = np.nan_to_num(np.asarray(by_label[f"{cat}_temp"], float))
        arrays[f"temp|{cat}"] = t / t.sum()                                 # index = degC
        meta["categories"][str(cat)] = info
    counts = [_num(v) for v in by_label["Number"]]; total = [_num(v) for v in by_label["Total share"]]
    rel = [_num(v) for v in by_label["Relative share"]]
    desc_row = [r for r in rows if r[0] == "Description"][0]
    for i, c in enumerate(counts[:9]):
        if np.isfinite(c):
            meta["categories"].setdefault(str(int(c)), {})
            meta["categories"][str(int(c))].update({"efus_count": int(total[i]), "relative_share": float(rel[i]) if np.isfinite(rel[i]) else 0.0,
                                                    "description": str(desc_row[i + 1])})
    arrays["bins15_hours"] = bins15
    arrays["season_start_month"] = np.asarray(by_label["All|Heating season start"][:12], float)
    arrays["season_length_months"] = np.asarray(by_label["All|Heating season length"][:12], float)

    # ---- occupancy: transition matrices and starting states ---------------------------------
    for n in range(1, 7):
        for kind in ("wd", "we"):
            rr = list(wb[f"tpm{n}_{kind}"].iter_rows(min_row=11, values_only=True))
            data = [r for r in rr if r[0] is not None and str(r[0]).replace(".", "").isdigit()]
            states = []
            for r in data:
                s = str(r[1]).strip()
                if s not in states:
                    states.append(s)
                if len(states) == (n + 1) ** 2:
                    break
            S = len(states); idx = {s: i for i, s in enumerate(states)}
            tpm = np.zeros((144, S, S), float)
            for r in data:
                t = int(float(r[0])) - 1; i = idx[str(r[1]).strip()]
                probs = np.nan_to_num(np.asarray([_num(v) for v in r[2:2 + S]], float))
                tpm[t, i, :] = probs
            row_sums = tpm.sum(axis=2, keepdims=True)
            tpm = np.where(row_sums > 0, tpm / np.maximum(row_sums, 1e-12), 0.0)
            arrays[f"tpm|{n}|{kind}"] = tpm
            meta.setdefault("states", {})[str(n)] = states
    ss = list(wb["starting_states"].iter_rows(values_only=True))
    block = None
    start = {"wd": {}, "we": {}}
    for r in ss:
        lab = str(r[0]).strip() if r[0] is not None else ""
        if lab.startswith("Weekday"):
            block = "wd"; continue
        if lab.startswith("Weekend"):
            block = "we"; continue
        if block and lab.isdigit() and len(lab) == 2:
            for n in range(1, 7):
                start[block].setdefault(str(n), {})[lab] = _num(r[n])
    for kind in ("wd", "we"):
        for n in range(1, 7):
            states = meta["states"][str(n)]
            v = np.asarray([start[kind].get(str(n), {}).get(s, 0.0) for s in states], float)
            v = np.nan_to_num(v); arrays[f"start_state|{n}|{kind}"] = v / v.sum() if v.sum() > 0 else np.ones(len(states)) / len(states)
    pm = list(wb["prob_mean_values"].iter_rows(min_row=3, max_row=3, values_only=True))[0]
    arrays["mean_active_occupancy_by_size"] = np.asarray([_num(v) for v in pm[1:6]], float)
    meta["notes"]["occupancy"] = ("CREST four-state Markov chain: combined state '<residents at home><residents active>', 144 ten-minute "
                                  "periods, weekday/weekend matrices per household size 1-6 (McKenna et al. 2018, Richardson et al. 2008)")
    meta["notes"]["heating"] = ("EFUS 2011 heating-regime categories as implemented in CHAP: timer start and duration distributions in "
                                "15-minute bins per period and day type; thermostat distributions per category in 1 degC bins; category "
                                "0 ('question not applicable') is heated on active occupancy")
    return arrays, meta


def save(xlsm_path) -> None:
    arrays, meta = extract(xlsm_path)
    np.savez_compressed(NPZ_PATH, **arrays)
    JSON_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"wrote {NPZ_PATH.name} ({NPZ_PATH.stat().st_size/1e6:.2f} MB, {len(arrays)} arrays) and {JSON_PATH.name}")


_CACHE = None


def load() -> tuple[dict, dict]:
    """Arrays and metadata (cached). Raises FileNotFoundError if the extraction has not been run."""
    global _CACHE
    if _CACHE is None:
        if not NPZ_PATH.exists():
            raise FileNotFoundError(f"{NPZ_PATH} not found: run `python chap_microdata.py <CHAP_model_1.0.xlsm>` once")
        z = np.load(NPZ_PATH)
        _CACHE = ({k: z[k] for k in z.files}, json.loads(JSON_PATH.read_text(encoding="utf-8")))
    return _CACHE


def available() -> bool:
    return NPZ_PATH.exists() and JSON_PATH.exists()


if __name__ == "__main__":
    save(sys.argv[1] if len(sys.argv) > 1 else r"LOCAL_PATH/CHAP_model_1.0.xlsm")
