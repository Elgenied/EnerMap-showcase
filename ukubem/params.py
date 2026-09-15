"""
Every modelling assumption of EnerMap lives in `parameters/<group>/<name>.json` (see parameters/README.md).
This module is the single reader: `load("heat_pumps", "cop_model")` returns the parsed file (cached).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PARAMS_DIR = Path(__file__).resolve().parents[1] / "parameters"


@lru_cache(maxsize=None)
def load(group: str, name: str) -> dict:
    p = PARAMS_DIR / group / f"{name}.json"
    if not p.exists():
        raise FileNotFoundError(f"parameter file missing: {p}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def path(group: str, name: str, ext: str = "json") -> Path:
    return PARAMS_DIR / group / f"{name}.{ext}"
