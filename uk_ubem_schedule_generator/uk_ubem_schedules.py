"""UK residential stochastic schedules for urban building energy models.

This module generates behavioural boundary conditions for an existing thermal
demand engine.  It does *not* calculate space-heating demand.  The intended
consumer is a 5R1C/ISO-style model that already owns weather, fabric, solar,
ventilation and HVAC-system calculations.

The heating-regime categories, their population shares, thermostat distributions,
timer start/duration distributions and heating-season marginals are the EFUS
microdata as implemented in the CREST Heat and Power (CHAP) model (McKenna et al.,
2018, doi:10.1016/j.enbuild.2018.02.051), extracted from `CHAP_model_1.0.xlsm` by
`chap_microdata.py` into `chap_microdata.npz` / `.json`. Occupancy follows the CREST
four-state Markov chain with CHAP's ten-minute transition matrices per household
size (`occupancy_mode="crest_tpm"`). The earlier configurable assumptions remain
available for comparison (`timing_source="assumed"`, `setpoint_source="normal"`,
`occupancy_mode="person_templates"`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

try:  # bundled CHAP microdata (see chap_microdata.py)
    import chap_microdata as _chap
except ImportError:  # pragma: no cover
    try:
        from . import chap_microdata as _chap  # type: ignore
    except ImportError:
        _chap = None


SCHEMA_VERSION = "1.2.0"

AWAY = 0
HOME_ACTIVE = 1
HOME_ASLEEP = 2


@dataclass(frozen=True)
class PersonTemplate:
    """Stochastic daily timing assumptions for one household-member type."""

    wake_weekday_h: float
    wake_weekend_h: float
    bedtime_weekday_h: float
    bedtime_weekend_h: float
    timing_sd_h: float
    weekday_leave_h: float | None
    weekday_return_h: float | None
    weekday_away_probability: float
    weekend_outing_probability: float
    weekend_outing_start_h: float = 11.5
    weekend_outing_duration_h: float = 3.0


PERSON_TEMPLATES: Mapping[str, PersonTemplate] = {
    "full_time": PersonTemplate(6.7, 8.2, 23.0, 23.5, 0.55, 8.1, 17.6, 0.90, 0.65),
    "part_time": PersonTemplate(7.0, 8.2, 22.8, 23.3, 0.65, 9.0, 14.5, 0.72, 0.65),
    "unemployed": PersonTemplate(7.8, 8.5, 23.0, 23.4, 0.75, 11.0, 14.0, 0.45, 0.65),
    "retired": PersonTemplate(7.6, 8.0, 22.5, 22.7, 0.65, 10.8, 13.5, 0.48, 0.55),
    "student": PersonTemplate(7.3, 8.7, 23.5, 0.2 + 24.0, 0.70, 9.2, 16.5, 0.78, 0.72),
    "school_child": PersonTemplate(7.0, 8.0, 21.2, 21.8, 0.45, 8.2, 15.5, 0.94, 0.70),
    "preschool_child": PersonTemplate(7.0, 7.5, 20.2, 20.5, 0.40, None, None, 0.0, 0.65),
}


HOUSEHOLD_ARCHETYPES: Mapping[str, tuple[str, ...]] = {
    "single_working": ("full_time",),
    "working_couple": ("full_time", "full_time"),
    "family_children": ("full_time", "part_time", "school_child", "school_child"),
    "single_parent": ("part_time", "school_child"),
    "retired_couple": ("retired", "retired"),
    "single_retired": ("retired",),
    "shared_adults": ("full_time", "full_time", "full_time"),
}


@dataclass(frozen=True)
class HeatingPattern:
    """EFUS/CHAP heating category: id, EFUS 2011 count, CHAP category number and the normal-approximation setpoint."""

    pattern_id: str
    label: str
    efus_count: int
    setpoint_mean_c: float
    setpoint_sd_c: float
    efus_category: int = -1          # CHAP category number (0 = occupancy driven); -1 = not in CHAP
    timer_periods: int = 0           # timer periods per day (0 for continuous / occupancy driven)


# EFUS 2011 categories as implemented by CHAP (categories 4 and 7 have no timing data and are
# excluded; category 0 "question not applicable" is heated on active occupancy). The counts are
# the EFUS sample (n = 2,142); with `timing_source="chap_microdata"` the draw uses the workbook's
# relative shares. Category 2 is "on once daily, on in the evening for all day and night", which
# CHAP implements as continuous heating (timer start 00:00, no duration) - the earlier reading
# "evening through night" is kept as an alias for old inputs.
HEATING_PATTERNS: Mapping[str, HeatingPattern] = {
    "P1_ALL_DAY_FROM_WAKE": HeatingPattern(
        "P1_ALL_DAY_FROM_WAKE", "Once daily: on at wake-up for all day", 165, 20.0, 1.0, 1, 1
    ),
    "P2_ALL_DAY_AND_NIGHT": HeatingPattern(
        "P2_ALL_DAY_AND_NIGHT", "Once daily: on in the evening for all day and night (continuous)", 112, 20.7, 0.8, 2, 0
    ),
    "P3_EVENING_SUSTAINED": HeatingPattern(
        "P3_EVENING_SUSTAINED", "Once daily: on at home-time for a sustained interval", 61, 20.4, 0.7, 3, 1
    ),
    "P5_TWO_SHORT": HeatingPattern(
        "P5_TWO_SHORT", "Twice daily: morning short burst and evening short burst", 240, 19.7, 0.7, 5, 2
    ),
    "P6_MORNING_SHORT_EVENING_SUSTAINED": HeatingPattern(
        "P6_MORNING_SHORT_EVENING_SUSTAINED",
        "Twice daily: morning short burst and evening sustained",
        643,
        20.5,
        0.7,
        6,
        2,
    ),
    "P8_THREE_PERIODS": HeatingPattern(
        "P8_THREE_PERIODS", "Three heating periods", 246, 20.2, 0.7, 8, 3
    ),
    "P9_ACTIVE_OCCUPANCY": HeatingPattern(
        "P9_ACTIVE_OCCUPANCY", "Heating during active occupancy (EFUS: question not applicable)", 675, 20.2, 3.36, 0, 0
    ),
}
PATTERN_ALIASES = {"P2_EVENING_THROUGH_NIGHT": "P2_ALL_DAY_AND_NIGHT"}
PATTERN_BY_CATEGORY = {p.efus_category: p for p in HEATING_PATTERNS.values()}


def resolve_pattern_id(pattern_id: str) -> str:
    return PATTERN_ALIASES.get(pattern_id, pattern_id)


def chap_microdata_available() -> bool:
    return _chap is not None and _chap.available()


def _microdata():
    if not chap_microdata_available():
        raise FileNotFoundError(
            "CHAP microdata not found: run `python chap_microdata.py <CHAP_model_1.0.xlsm>` in the generator folder, "
            "or set timing_source='assumed', setpoint_source='normal', occupancy_mode='person_templates'"
        )
    return _chap.load()


# Source-workbook EFUS marginals. Months are January..December and lengths are
# 1..12 calendar months. They are sampled independently, matching CHAP.
EFUS_SEASON_START_PROBABILITIES = np.asarray(
    [
        0.0320747,
        0.0004060,
        0.0012180,
        0.0004060,
        0.0004060,
        0.0004060,
        0.0008120,
        0.0052781,
        0.1343890,
        0.5274056,
        0.2553796,
        0.0418189,
    ],
    dtype=float,
)
EFUS_SEASON_START_PROBABILITIES /= EFUS_SEASON_START_PROBABILITIES.sum()
EFUS_SEASON_LENGTH_PROBABILITIES = np.asarray(
    [
        0.0073082,
        0.0223305,
        0.0418189,
        0.1555014,
        0.2634998,
        0.2793341,
        0.1368250,
        0.0495331,
        0.0097442,
        0.0020300,
        0.0032481,
        0.0288266,
    ],
    dtype=float,
)
EFUS_SEASON_LENGTH_PROBABILITIES /= EFUS_SEASON_LENGTH_PROBABILITIES.sum()


@dataclass(frozen=True)
class DwellingSpec:
    """Behavioural attributes linked to one EPC/building-stock record.

    Physical fabric and HVAC attributes deliberately remain in the 5R1C model.
    ``member_types`` overrides the selected household archetype when supplied.
    """

    dwelling_id: str
    household_archetype: str
    floor_area_m2: float
    physical_archetype_id: str | None = None
    member_types: tuple[str, ...] | None = None
    stock_weight: float = 1.0
    heating_pattern: str | None = None
    comfort_setpoint_c: float | None = None
    heating_pattern_quantile: float | None = None   # stratified sampling: invert the regime-share CDF at this quantile
    setpoint_quantile: float | None = None          # stratified sampling: invert the regime's thermostat CDF at this quantile

    def validate(self) -> None:
        if not self.dwelling_id or not self.dwelling_id.strip():
            raise ValueError("dwelling_id must be non-empty")
        if self.household_archetype not in HOUSEHOLD_ARCHETYPES and self.member_types is None:
            raise ValueError(
                f"Unknown household_archetype {self.household_archetype!r}; "
                f"choose one of {sorted(HOUSEHOLD_ARCHETYPES)} or supply member_types."
            )
        if not math.isfinite(self.floor_area_m2) or self.floor_area_m2 <= 0:
            raise ValueError("floor_area_m2 must be a positive finite number")
        members = self.resolved_members()
        if not members:
            raise ValueError("At least one household member is required")
        unknown = sorted(set(members) - set(PERSON_TEMPLATES))
        if unknown:
            raise ValueError(f"Unknown member type(s): {unknown}; choose from {sorted(PERSON_TEMPLATES)}")
        if self.heating_pattern is not None and resolve_pattern_id(self.heating_pattern) not in HEATING_PATTERNS:
            raise ValueError(f"Unknown heating_pattern {self.heating_pattern!r}")
        if self.comfort_setpoint_c is not None and not 16.0 <= self.comfort_setpoint_c <= 24.0:
            raise ValueError("comfort_setpoint_c must be between 16 and 24 C")
        for name in ("heating_pattern_quantile", "setpoint_quantile"):
            q = getattr(self, name)
            if q is not None and not (math.isfinite(q) and 0.0 <= q < 1.0):
                raise ValueError(f"{name} must lie in [0, 1)")
        if not math.isfinite(self.stock_weight) or self.stock_weight <= 0:
            raise ValueError("stock_weight must be positive and finite")

    def resolved_members(self) -> tuple[str, ...]:
        if self.member_types is not None:
            return tuple(self.member_types)
        return HOUSEHOLD_ARCHETYPES[self.household_archetype]


@dataclass(frozen=True)
class SimulationConfig:
    """Calendar, randomisation and calibratable behavioural assumptions."""

    start: str
    end: str
    timestep_minutes: int = 10
    timezone: str = "Europe/London"
    master_seed: int = 20260902
    realization: int = 0
    heating_control_mode: str = "efus_mixed"
    heating_season_mode: str = "efus_sampled"
    heating_season_start: tuple[int, int] = (10, 1)
    heating_season_end: tuple[int, int] = (5, 15)
    setback_setpoint_c: float = 12.0
    lighting_peak_w_per_m2: float = 3.0
    appliance_peak_w_per_m2: float = 6.0
    dhw_litre_per_person_day: float = 40.1
    dhw_hot_temperature_c: float = 44.71
    dhw_cold_temperature_c: float = 10.0

    # Behavioural evidence sources (CHAP microdata by default; the earlier assumptions for comparison)
    timing_source: str = "chap_microdata"      # "chap_microdata" | "assumed"
    timer_sampling: str = "per_dwelling"        # "per_dwelling" (CHAP: one timer programme per dwelling) | "per_day"
    setpoint_source: str = "chap_microdata"    # "chap_microdata" (empirical per category) | "normal"
    occupancy_mode: str = "crest_tpm"          # "crest_tpm" (CHAP transition matrices) | "person_templates"
    setpoint_clip_c: tuple[float, float] = (12.0, 27.0)

    # Timing assumptions of the "assumed" source (kept for comparison with the microdata).
    morning_start_mean_h: float = 6.75
    morning_start_sd_h: float = 0.60
    evening_start_mean_h: float = 17.0
    evening_start_sd_h: float = 0.85
    morning_short_duration_h: float = 2.0
    evening_short_duration_h: float = 4.0
    evening_sustained_duration_h: float = 6.5
    midday_duration_h: float = 1.5

    def validate(self) -> None:
        if self.timestep_minutes <= 0 or 60 % self.timestep_minutes != 0:
            raise ValueError("timestep_minutes must be a positive divisor of 60")
        if self.heating_control_mode not in {"efus_mixed", "timer_only", "active_occupancy"}:
            raise ValueError(
                "heating_control_mode must be efus_mixed, timer_only, or active_occupancy"
            )
        if self.heating_season_mode not in {"efus_sampled", "fixed_dates"}:
            raise ValueError("heating_season_mode must be efus_sampled or fixed_dates")
        if self.timing_source not in {"chap_microdata", "assumed"}:
            raise ValueError("timing_source must be chap_microdata or assumed")
        if self.timer_sampling not in {"per_dwelling", "per_day"}:
            raise ValueError("timer_sampling must be per_dwelling or per_day")
        if self.setpoint_source not in {"chap_microdata", "normal"}:
            raise ValueError("setpoint_source must be chap_microdata or normal")
        if self.occupancy_mode not in {"crest_tpm", "person_templates"}:
            raise ValueError("occupancy_mode must be crest_tpm or person_templates")
        if "chap_microdata" in (self.timing_source, self.setpoint_source) or self.occupancy_mode == "crest_tpm":
            _microdata()
        for label, month_day in (
            ("heating_season_start", self.heating_season_start),
            ("heating_season_end", self.heating_season_end),
        ):
            try:
                dt.date(2001, int(month_day[0]), int(month_day[1]))
            except (TypeError, ValueError, IndexError) as exc:
                raise ValueError(f"{label} must be a valid (month, day) tuple") from exc
        if not 5.0 <= self.setback_setpoint_c <= 18.0:
            raise ValueError("setback_setpoint_c must be between 5 and 18 C")
        if self.dhw_hot_temperature_c <= self.dhw_cold_temperature_c:
            raise ValueError("DHW hot temperature must exceed cold temperature")
        non_negative = {
            "lighting_peak_w_per_m2": self.lighting_peak_w_per_m2,
            "appliance_peak_w_per_m2": self.appliance_peak_w_per_m2,
            "dhw_litre_per_person_day": self.dhw_litre_per_person_day,
            "morning_start_sd_h": self.morning_start_sd_h,
            "evening_start_sd_h": self.evening_start_sd_h,
        }
        if any(not math.isfinite(v) or v < 0 for v in non_negative.values()):
            raise ValueError(f"These values must be finite and non-negative: {sorted(non_negative)}")
        positive = {
            "morning_short_duration_h": self.morning_short_duration_h,
            "evening_short_duration_h": self.evening_short_duration_h,
            "evening_sustained_duration_h": self.evening_sustained_duration_h,
            "midday_duration_h": self.midday_duration_h,
        }
        if any(not math.isfinite(v) or v <= 0 for v in positive.values()):
            raise ValueError(f"These durations must be finite and positive: {sorted(positive)}")
        index = _make_time_index(self)
        if len(index) == 0:
            raise ValueError("Simulation horizon contains no intervals")
        local_start = _localise_timestamp(self.start, self.timezone)
        if (
            local_start.second != 0
            or local_start.microsecond != 0
            or local_start.minute % self.timestep_minutes != 0
        ):
            raise ValueError("Simulation start must align to the local timestep grid")

    @classmethod
    def for_year(cls, year: int, **kwargs: object) -> "SimulationConfig":
        return cls(start=f"{year}-01-01", end=f"{year + 1}-01-01", **kwargs)


@dataclass
class ScheduleResult:
    frame: pd.DataFrame
    metadata: dict[str, object] = field(default_factory=dict)

    def to_5r1c(self) -> dict[str, np.ndarray]:
        """Return the minimum direct behavioural inputs for one 5R1C run."""

        enabled = self.frame["heating_enabled"].to_numpy(dtype=bool, copy=True)
        return {
            "theta_set_heating_c": self.frame["heating_setpoint_c"].to_numpy(copy=True),
            "heating_enabled": enabled,
            # Multiply the 5R1C heating-power limit by this series. This is the
            # strict CHAP switch that prevents off-period setback heating.
            "heating_power_available_fraction": enabled.astype(float),
            "phi_internal_w": self.frame["internal_gain_w"].to_numpy(copy=True),
        }


def _localise_timestamp(value: str, timezone: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize(timezone)
    return ts.tz_convert(timezone)


def _make_time_index(config: SimulationConfig) -> pd.DatetimeIndex:
    start_local = _localise_timestamp(config.start, config.timezone)
    end_local = _localise_timestamp(config.end, config.timezone)
    if end_local <= start_local:
        raise ValueError("Simulation end must be after start")
    return pd.date_range(
        start=start_local.tz_convert("UTC"),
        end=end_local.tz_convert("UTC"),
        freq=pd.Timedelta(minutes=config.timestep_minutes),
        inclusive="left",
    )


def _complete_local_day_index(
    requested_utc_index: pd.DatetimeIndex,
    timezone: str,
    timestep_minutes: int,
) -> pd.DatetimeIndex:
    """Cover every touched local civil day, including UK DST transitions."""

    local = requested_utc_index.tz_convert(timezone)
    start_date = local[0].date()
    end_date = local[-1].date() + dt.timedelta(days=1)
    start = pd.Timestamp(start_date).tz_localize(timezone).tz_convert("UTC")
    end = pd.Timestamp(end_date).tz_localize(timezone).tz_convert("UTC")
    return pd.date_range(
        start=start,
        end=end,
        freq=pd.Timedelta(minutes=timestep_minutes),
        inclusive="left",
    )


def _stable_rng(config: SimulationConfig, dwelling_id: str, process: str) -> np.random.Generator:
    token = f"{config.master_seed}|{dwelling_id}|{config.realization}|{process}"
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], byteorder="little", signed=False)
    return np.random.default_rng(seed)


def _normal_clipped(
    rng: np.random.Generator, mean: float, sd: float, lower: float, upper: float
) -> float:
    return float(np.clip(rng.normal(mean, sd), lower, upper))


def _daily_person_state(
    member_type: str,
    local_index: pd.DatetimeIndex,
    config: SimulationConfig,
    dwelling_id: str,
    member_index: int,
) -> np.ndarray:
    template = PERSON_TEMPLATES[member_type]
    result = np.full(len(local_index), AWAY, dtype=np.int8)
    dates = np.asarray(local_index.date)
    unique_dates = pd.unique(dates)

    for date in unique_dates:
        rng = _stable_rng(
            config,
            dwelling_id,
            f"occupancy|member={member_index}|type={member_type}|date={date.isoformat()}",
        )
        mask = dates == date
        hours = (
            local_index[mask].hour.to_numpy(dtype=float)
            + local_index[mask].minute.to_numpy(dtype=float) / 60.0
        )
        weekend = date.weekday() >= 5
        wake_mean = template.wake_weekend_h if weekend else template.wake_weekday_h
        bed_mean = template.bedtime_weekend_h if weekend else template.bedtime_weekday_h
        wake = _normal_clipped(rng, wake_mean, template.timing_sd_h, 4.5, 11.5)
        bed = _normal_clipped(rng, bed_mean, template.timing_sd_h, 19.0, 25.0)
        if bed >= 24.0:
            bed = 23.99

        daily = np.full(mask.sum(), HOME_ASLEEP, dtype=np.int8)
        daily[(hours >= wake) & (hours < bed)] = HOME_ACTIVE

        away_probability = (
            template.weekend_outing_probability if weekend else template.weekday_away_probability
        )
        if rng.random() < away_probability:
            if weekend or template.weekday_leave_h is None or template.weekday_return_h is None:
                leave = _normal_clipped(
                    rng, template.weekend_outing_start_h, 1.1, wake + 0.5, max(wake + 0.6, bed - 0.5)
                )
                duration = _normal_clipped(
                    rng, template.weekend_outing_duration_h, 1.0, 0.5, 8.0
                )
                returned = min(bed, leave + duration)
            else:
                leave = _normal_clipped(
                    rng, template.weekday_leave_h, template.timing_sd_h, wake + 0.3, 14.0
                )
                returned = _normal_clipped(
                    rng, template.weekday_return_h, template.timing_sd_h * 1.2, leave + 1.0, bed
                )
            daily[(hours >= leave) & (hours < returned)] = AWAY

        result[mask] = daily
    return result


def _crest_tpm_occupancy(
    n_residents: int,
    local_index: pd.DatetimeIndex,
    config: SimulationConfig,
    dwelling_id: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    CREST four-state occupancy (Richardson et al. 2008; CHAP, McKenna et al. 2018): every local day starts
    from CHAP's initial-state distribution and walks 144 ten-minute transitions of the weekday or weekend
    matrix of the household size (clipped to 6). Combined state "<at home><active>": present = at home,
    active = min(active, at home) (CHAP: inactive residents are assumed at home; a resident recorded active
    while away adds no gains here), asleep = present - active. The random numbers are drawn per local day
    (temporal chunks reproduce the annual run) and the walk is vectorised across days. Values are
    aggregated to the configured timestep as means over the ten-minute states.
    """
    arrays, meta = _microdata()
    n = int(min(max(n_residents, 1), 6))
    states = meta["states"][str(n)]; S = len(states)
    home = np.asarray([int(s[0]) for s in states]); act = np.asarray([int(s[1]) for s in states])
    cum = {k: np.cumsum(arrays[f"tpm|{n}|{k}"], axis=2) for k in ("wd", "we")}
    p0cum = {k: np.cumsum(arrays[f"start_state|{n}|{k}"]) for k in ("wd", "we")}
    all_dates = np.asarray(local_index.date)
    dates = pd.unique(all_dates)
    kinds = np.array(["we" if d.weekday() >= 5 else "wd" for d in dates])
    U = np.empty((len(dates), 144)); u0 = np.empty(len(dates))
    for i, d in enumerate(dates):
        rng = _stable_rng(config, dwelling_id, f"occupancy_tpm|date={d.isoformat()}")
        u0[i] = rng.random(); U[i] = rng.random(144)
    path = np.empty((len(dates), 144), dtype=np.int16)
    state = np.zeros(len(dates), dtype=int)
    groups = {k: np.where(kinds == k)[0] for k in ("wd", "we") if (kinds == k).any()}
    for k, sel in groups.items():
        state[sel] = np.minimum(np.searchsorted(p0cum[k], u0[sel], side="right"), S - 1)
    path[:, 0] = state
    for t in range(1, 144):
        for k, sel in groups.items():
            c = cum[k][t - 1][state[sel]]                         # cumulative rows of the current states
            nxt = np.minimum((c < U[sel, t][:, None]).sum(axis=1), S - 1)
            nxt = np.where(c[:, -1] <= 0, state[sel], nxt)        # a row without mass keeps its state
            state[sel] = nxt
        path[:, t] = state
    h10 = home[path]; a10 = np.minimum(act[path], h10)
    day_pos = {d: i for i, d in enumerate(dates)}
    day_idx = np.fromiter((day_pos[d] for d in all_dates), dtype=int, count=len(all_dates))
    minutes = local_index.hour.to_numpy() * 60 + local_index.minute.to_numpy()
    step = int(config.timestep_minutes)
    if step != 10 and step % 10 == 0 and 144 % (step // 10) == 0:
        k = step // 10
        hp = h10.reshape(len(dates), 144 // k, k).mean(axis=2); ap = a10.reshape(len(dates), 144 // k, k).mean(axis=2)
        idx = np.clip(minutes // step, 0, 144 // k - 1)
        present = hp[day_idx, idx]; active = ap[day_idx, idx]
    else:
        idx = np.clip(minutes // 10, 0, 143)
        present = h10[day_idx, idx].astype(float); active = a10[day_idx, idx].astype(float)
    return present, active, present - active


def _sample_heating_pattern(
    spec: DwellingSpec,
    config: SimulationConfig,
    rng: np.random.Generator,
) -> HeatingPattern:
    if spec.heating_pattern:
        return HEATING_PATTERNS[resolve_pattern_id(spec.heating_pattern)]
    if config.heating_control_mode == "active_occupancy":
        return HEATING_PATTERNS["P9_ACTIVE_OCCUPANCY"]

    candidates = list(HEATING_PATTERNS.values())
    if config.heating_control_mode == "timer_only":
        candidates = [p for p in candidates if p.pattern_id != "P9_ACTIVE_OCCUPANCY"]
    if config.timing_source == "chap_microdata":
        _, meta = _microdata()
        weights = np.asarray([meta["categories"][str(p.efus_category)]["relative_share"] for p in candidates], dtype=float)
    else:
        weights = np.asarray([p.efus_count for p in candidates], dtype=float)
    weights /= weights.sum()
    if spec.heating_pattern_quantile is not None:
        # stratified draw: invert the share CDF at the given quantile (Latin-hypercube sampling of a stock)
        cdf = np.cumsum(weights)
        return candidates[min(int(np.searchsorted(cdf, spec.heating_pattern_quantile, side="right")), len(candidates) - 1)]
    return candidates[int(rng.choice(len(candidates), p=weights))]


def _sample_setpoint(pattern: HeatingPattern, config: SimulationConfig, rng: np.random.Generator,
                     quantile: float | None = None) -> float:
    """
    Thermostat setting: CHAP's empirical distribution of the category (1 degC bins, jittered) or the normal
    approximation. With `quantile` (stratified sampling) the continuous inverse CDF is evaluated at that
    quantile instead of drawing from `rng`.
    """
    if config.setpoint_source == "chap_microdata":
        arrays, _ = _microdata()
        p = np.asarray(arrays[f"temp|{pattern.efus_category}"], dtype=float)
        if quantile is not None:
            cdf = np.cumsum(p)
            i = min(int(np.searchsorted(cdf, quantile, side="right")), len(p) - 1)
            frac = (quantile - (cdf[i] - p[i])) / p[i] if p[i] > 0 else 0.5       # position inside the 1 degC bin
            deg = i + (min(max(frac, 0.0), 1.0) - 0.5)
        else:
            deg = int(rng.choice(len(p), p=p)) + rng.uniform(-0.5, 0.5)
        lo, hi = config.setpoint_clip_c
        return float(np.clip(deg, lo, hi))
    if quantile is not None:
        from statistics import NormalDist
        q = min(max(quantile, 1e-6), 1.0 - 1e-6)
        return float(np.clip(NormalDist(pattern.setpoint_mean_c, pattern.setpoint_sd_c).inv_cdf(q), 16.0, 24.0))
    return _normal_clipped(rng, pattern.setpoint_mean_c, pattern.setpoint_sd_c, 16.0, 24.0)


def _sample_timer_programme(pattern: HeatingPattern, rng: np.random.Generator) -> dict[str, list[tuple[float, float]]]:
    """
    One CHAP timer programme: per day type ("WD" / "WE") the (start hour, duration hours) of every heating
    period, drawn from the EFUS microdata (15-minute bins, uniform jitter inside the bin). Continuous
    categories return a single 24-hour period.
    """
    arrays, _ = _microdata()
    cat = pattern.efus_category
    prog: dict[str, list[tuple[float, float]]] = {}
    for day in ("WD", "WE"):
        periods = []
        if pattern.timer_periods == 0 and cat == 2:
            periods.append((0.0, 24.0))
        for p in range(1, pattern.timer_periods + 1):
            ps = arrays.get(f"start|{cat}|{p}|{day}"); pd_ = arrays.get(f"dur|{cat}|{p}|{day}")
            if ps is None or pd_ is None:
                ps = arrays.get(f"start|{cat}|{p}|WD"); pd_ = arrays.get(f"dur|{cat}|{p}|WD")
            start = int(rng.choice(len(ps), p=ps)) * 0.25 + rng.uniform(0.0, 0.25)
            dur = int(rng.choice(len(pd_), p=pd_)) * 0.25 + rng.uniform(0.0, 0.25)
            periods.append((float(start), float(max(dur, 0.25))))
        prog[day] = periods
    return prog


def _in_heating_season(
    dates: Sequence[object], start: tuple[int, int], end: tuple[int, int]
) -> np.ndarray:
    result = np.zeros(len(dates), dtype=bool)
    wraps_year = start > end
    for i, date in enumerate(dates):
        month_day = (date.month, date.day)
        if wraps_year:
            result[i] = month_day >= start or month_day <= end
        else:
            result[i] = start <= month_day <= end
    return result


def _sample_heating_season(
    config: SimulationConfig,
    dates: Sequence[object],
    rng: np.random.Generator,
) -> tuple[np.ndarray, int | None, int | None]:
    if config.heating_season_mode == "fixed_dates":
        mask = _in_heating_season(
            dates, config.heating_season_start, config.heating_season_end
        )
        return mask, None, None

    start_month = int(rng.choice(np.arange(1, 13), p=EFUS_SEASON_START_PROBABILITIES))
    length_months = int(rng.choice(np.arange(1, 13), p=EFUS_SEASON_LENGTH_PROBABILITIES))
    months = np.asarray([date.month for date in dates], dtype=int)
    offsets = (months - start_month) % 12
    return offsets < length_months, start_month, length_months


def _interval_mask(hours: np.ndarray, start_h: float, duration_h: float) -> np.ndarray:
    end_h = start_h + duration_h
    if end_h <= 24.0:
        return (hours >= start_h) & (hours < end_h)
    return (hours >= start_h) | (hours < (end_h - 24.0))


def _heating_controls(
    spec: DwellingSpec,
    config: SimulationConfig,
    local_index: pd.DatetimeIndex,
    active_count: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, HeatingPattern, float, int | None, int | None]:
    pattern = _sample_heating_pattern(spec, config, rng)
    if spec.comfort_setpoint_c is None:
        comfort = _sample_setpoint(pattern, config, rng, spec.setpoint_quantile)
    else:
        comfort = float(spec.comfort_setpoint_c)
    programme = None
    if config.timing_source == "chap_microdata" and pattern.pattern_id != "P9_ACTIVE_OCCUPANCY" and config.timer_sampling == "per_dwelling":
        programme = _sample_timer_programme(pattern, rng)

    enabled = np.zeros(len(local_index), dtype=bool)
    dates = np.asarray(local_index.date)
    season, season_start_month, season_length_months = _sample_heating_season(
        config, dates, rng
    )

    if pattern.pattern_id == "P9_ACTIVE_OCCUPANCY":
        enabled = season & (active_count > 0)
    elif config.timing_source == "chap_microdata":
        for date in pd.unique(dates):
            mask = dates == date
            hours = (
                local_index[mask].hour.to_numpy(dtype=float)
                + local_index[mask].minute.to_numpy(dtype=float) / 60.0
            )
            day = "WE" if date.weekday() >= 5 else "WD"
            prog = programme
            if prog is None:      # per-day sampling: a fresh draw of the timer settings every day
                daily_rng = _stable_rng(config, spec.dwelling_id, f"heating_timing|date={date.isoformat()}")
                prog = _sample_timer_programme(pattern, daily_rng)
            daily = np.zeros(mask.sum(), dtype=bool)
            for start_h, dur_h in prog[day]:
                daily |= _interval_mask(hours, start_h, dur_h)
            enabled[mask] = daily
        enabled &= season
    else:
        for date in pd.unique(dates):
            daily_rng = _stable_rng(
                config, spec.dwelling_id, f"heating_timing|date={date.isoformat()}"
            )
            mask = dates == date
            hours = (
                local_index[mask].hour.to_numpy(dtype=float)
                + local_index[mask].minute.to_numpy(dtype=float) / 60.0
            )
            morning = _normal_clipped(
                daily_rng,
                config.morning_start_mean_h,
                config.morning_start_sd_h,
                4.5,
                10.0,
            )
            evening = _normal_clipped(
                daily_rng,
                config.evening_start_mean_h,
                config.evening_start_sd_h,
                14.0,
                21.0,
            )

            if pattern.pattern_id == "P1_ALL_DAY_FROM_WAKE":
                daily = _interval_mask(hours, morning, max(0.5, 23.0 - morning))
            elif pattern.pattern_id == "P2_ALL_DAY_AND_NIGHT":
                daily = np.ones(len(hours), dtype=bool)
            elif pattern.pattern_id == "P3_EVENING_SUSTAINED":
                daily = _interval_mask(hours, evening, config.evening_sustained_duration_h)
            elif pattern.pattern_id == "P5_TWO_SHORT":
                daily = _interval_mask(hours, morning, config.morning_short_duration_h)
                daily |= _interval_mask(hours, evening, config.evening_short_duration_h)
            elif pattern.pattern_id == "P6_MORNING_SHORT_EVENING_SUSTAINED":
                daily = _interval_mask(hours, morning, config.morning_short_duration_h)
                daily |= _interval_mask(hours, evening, config.evening_sustained_duration_h)
            elif pattern.pattern_id == "P8_THREE_PERIODS":
                midday = _normal_clipped(daily_rng, 12.5, 0.75, 10.5, 15.0)
                daily = _interval_mask(hours, morning, config.morning_short_duration_h)
                daily |= _interval_mask(hours, midday, config.midday_duration_h)
                daily |= _interval_mask(hours, evening, config.evening_short_duration_h)
            else:  # defensive: every registered pattern must be handled above
                raise RuntimeError(f"Unhandled heating pattern {pattern.pattern_id}")
            enabled[mask] = daily
        enabled &= season

    setpoint = np.where(enabled, comfort, config.setback_setpoint_c).astype(float)
    _LAST_PROGRAMME[spec.dwelling_id] = programme
    return (
        enabled,
        setpoint,
        pattern,
        comfort,
        season_start_month,
        season_length_months,
    )


_LAST_PROGRAMME: dict[str, object] = {}


def _lighting_fraction(
    local_index: pd.DatetimeIndex,
    active_count: np.ndarray,
    config: SimulationConfig,
    dwelling_id: str,
) -> np.ndarray:
    day_of_year = local_index.dayofyear.to_numpy(dtype=float)
    hours = local_index.hour.to_numpy(dtype=float) + local_index.minute.to_numpy(dtype=float) / 60.0
    # Transparent UK approximation: day length varies from about 8 to 16 h.
    day_length = 12.0 + 4.0 * np.sin(2.0 * np.pi * (day_of_year - 80.0) / 365.25)
    sunrise = 12.0 - day_length / 2.0
    sunset = 12.0 + day_length / 2.0
    dark = (hours < sunrise) | (hours >= sunset)
    dates = np.asarray(local_index.date)
    noise = np.zeros(len(local_index), dtype=float)
    for date in pd.unique(dates):
        mask = dates == date
        rng = _stable_rng(config, dwelling_id, f"lighting|date={date.isoformat()}")
        noise[mask] = rng.normal(0.0, 0.10, mask.sum())
    fraction = np.where(
        dark & (active_count > 0),
        0.30 + 0.18 * np.minimum(active_count, 3) + noise,
        0.0,
    )
    return np.clip(fraction, 0.0, 1.0)


def _appliance_fraction(
    local_index: pd.DatetimeIndex,
    active_count: np.ndarray,
    asleep_count: np.ndarray,
    config: SimulationConfig,
    dwelling_id: str,
) -> np.ndarray:
    hours = local_index.hour.to_numpy(dtype=float) + local_index.minute.to_numpy(dtype=float) / 60.0
    meal_signal = (
        np.exp(-0.5 * ((hours - 7.5) / 0.75) ** 2)
        + 0.65 * np.exp(-0.5 * ((hours - 12.5) / 0.9) ** 2)
        + 1.2 * np.exp(-0.5 * ((hours - 18.5) / 1.0) ** 2)
    )
    occupancy_signal = np.minimum(active_count, 3) / 3.0
    standby = 0.10 + 0.02 * np.minimum(asleep_count, 2)
    dates = np.asarray(local_index.date)
    noise = np.zeros(len(local_index), dtype=float)
    for date in pd.unique(dates):
        mask = dates == date
        rng = _stable_rng(config, dwelling_id, f"appliances|date={date.isoformat()}")
        noise[mask] = rng.beta(2.0, 8.0, mask.sum()) * 0.20
    fraction = standby + occupancy_signal * (0.20 + 0.38 * meal_signal + noise)
    return np.clip(fraction, 0.0, 1.0)


def _dhw_draws(
    spec: DwellingSpec,
    config: SimulationConfig,
    local_index: pd.DatetimeIndex,
    active_count: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    dates = np.asarray(local_index.date)
    hours = local_index.hour.to_numpy(dtype=float) + local_index.minute.to_numpy(dtype=float) / 60.0
    litres = np.zeros(len(local_index), dtype=float)
    occupants = len(spec.resolved_members())

    for date in pd.unique(dates):
        rng = _stable_rng(config, spec.dwelling_id, f"dhw|date={date.isoformat()}")
        mask = dates == date
        local_hours = hours[mask]
        active = active_count[mask]
        # Activity-linked propensity only; daily volume is then calibrated to
        # the CHAP value (40.1 L/person/day by default).
        propensity = (active > 0).astype(float) * (
            0.10
            + 1.8 * np.exp(-0.5 * ((local_hours - 7.4) / 0.8) ** 2)
            + 0.45 * np.exp(-0.5 * ((local_hours - 12.5) / 1.0) ** 2)
            + 1.2 * np.exp(-0.5 * ((local_hours - 19.0) / 1.2) ** 2)
        )
        propensity *= rng.gamma(shape=1.5, scale=1.0, size=mask.sum())
        if propensity.sum() <= 0:
            continue
        daily_target = max(
            0.0,
            rng.normal(
                config.dhw_litre_per_person_day * occupants,
                0.20 * config.dhw_litre_per_person_day * math.sqrt(occupants),
            ),
        )
        litres[mask] = daily_target * propensity / propensity.sum()

    # 1 litre of water is approximately 1 kg; cp = 4180 J/(kg K).
    seconds = config.timestep_minutes * 60.0
    dhw_w = (
        litres
        * 4180.0
        * (config.dhw_hot_temperature_c - config.dhw_cold_temperature_c)
        / seconds
    )
    return litres, dhw_w


def generate_dwelling(spec: DwellingSpec, config: SimulationConfig) -> ScheduleResult:
    """Generate one reproducible dwelling schedule."""

    spec.validate()
    config.validate()
    requested_utc_index = _make_time_index(config)
    utc_index = _complete_local_day_index(
        requested_utc_index, config.timezone, config.timestep_minutes
    )
    local_index = utc_index.tz_convert(config.timezone)
    members = spec.resolved_members()

    if config.occupancy_mode == "crest_tpm":
        present_count, active_count, asleep_count = _crest_tpm_occupancy(
            len(members), local_index, config, spec.dwelling_id
        )
        if config.timestep_minutes == 10:
            present_count = present_count.astype(np.int16); active_count = active_count.astype(np.int16)
            asleep_count = asleep_count.astype(np.int16)
    else:
        states = np.vstack(
            [
                _daily_person_state(member, local_index, config, spec.dwelling_id, i)
                for i, member in enumerate(members)
            ]
        )
        present_count = np.sum(states != AWAY, axis=0).astype(np.int16)
        active_count = np.sum(states == HOME_ACTIVE, axis=0).astype(np.int16)
        asleep_count = np.sum(states == HOME_ASLEEP, axis=0).astype(np.int16)

    heating_rng = _stable_rng(config, spec.dwelling_id, "heating")
    (
        heating_enabled,
        setpoint,
        heating_pattern,
        comfort,
        season_start_month,
        season_length_months,
    ) = _heating_controls(spec, config, local_index, active_count, heating_rng)
    programme_meta = _LAST_PROGRAMME.pop(spec.dwelling_id, None)

    lighting_fraction = _lighting_fraction(
        local_index, active_count, config, spec.dwelling_id
    )
    appliance_fraction = _appliance_fraction(
        local_index,
        active_count,
        asleep_count,
        config,
        spec.dwelling_id,
    )

    people_gain_w = active_count * 131.0 + asleep_count * 73.8
    lighting_gain_w = lighting_fraction * config.lighting_peak_w_per_m2 * spec.floor_area_m2
    appliance_gain_w = appliance_fraction * config.appliance_peak_w_per_m2 * spec.floor_area_m2
    internal_gain_w = people_gain_w + lighting_gain_w + appliance_gain_w
    electric_load_w = lighting_gain_w + appliance_gain_w

    dhw_litre, dhw_w = _dhw_draws(
        spec,
        config,
        local_index,
        active_count,
    )

    frame = pd.DataFrame(
        {
            "timestamp_local": local_index.astype(str),
            "dwelling_id": spec.dwelling_id,
            "physical_archetype_id": spec.physical_archetype_id or "",
            "household_archetype": spec.household_archetype,
            "stock_weight": spec.stock_weight,
            "household_size": len(members),
            "occupants_present": present_count,
            "occupants_active": active_count,
            "occupants_asleep": asleep_count,
            "heating_enabled": heating_enabled,
            "heating_setpoint_c": setpoint,
            "comfort_setpoint_c": comfort,
            "heating_pattern": heating_pattern.pattern_id,
            "heating_season_start_month": season_start_month or 0,
            "heating_season_length_months": season_length_months or 0,
            "lighting_fraction": lighting_fraction,
            "appliance_fraction": appliance_fraction,
            "people_gain_w": people_gain_w,
            "lighting_gain_w": lighting_gain_w,
            "appliance_gain_w": appliance_gain_w,
            "internal_gain_w": internal_gain_w,
            "electric_load_w": electric_load_w,
            "dhw_litre_per_step": dhw_litre,
            "dhw_thermal_w": dhw_w,
        },
        index=utc_index,
    )
    frame = frame.loc[requested_utc_index]
    frame.index.name = "timestamp_utc"

    _validate_result(frame, len(members), config)
    metadata: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "dwelling": asdict(spec),
        "simulation": asdict(config),
        "resolved_members": members,
        "sampled_heating_pattern": heating_pattern.pattern_id,
        "sampled_comfort_setpoint_c": comfort,
        "sampled_heating_season_start_month": season_start_month,
        "sampled_heating_season_length_months": season_length_months,
        "sampled_timer_programme": programme_meta,
        "sampled_quantiles": {"heating_pattern": spec.heating_pattern_quantile, "setpoint": spec.setpoint_quantile},
        "sources": {
            "heating_categories": "EFUS 2011 categories as implemented by CHAP; McKenna et al. (2018)",
            "timing": ("CHAP_model_1.0.xlsm SH_HeatingPattern_Data (15-minute start / duration distributions per category, "
                       "period and day type)" if config.timing_source == "chap_microdata" else "configurable normal assumptions"),
            "thermostat": ("CHAP_model_1.0.xlsm SH_HeatingPattern_Data (1 degC distributions per category)"
                           if config.setpoint_source == "chap_microdata" else "normal approximation per category"),
            "occupancy": ("CREST four-state Markov chain, CHAP transition matrices tpm1-6 weekday/weekend + starting states"
                          if config.occupancy_mode == "crest_tpm" else "person templates (assumed)"),
            "doi": "10.1016/j.enbuild.2018.02.051",
        },
    }
    return ScheduleResult(frame=frame, metadata=metadata)


def _validate_result(frame: pd.DataFrame, household_size: int, config: SimulationConfig) -> None:
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise AssertionError("Time index must be unique and monotonic")
    if frame.isna().any().any():
        raise AssertionError("Schedule contains missing values")
    present = frame["occupants_present"].to_numpy()
    active = frame["occupants_active"].to_numpy()
    asleep = frame["occupants_asleep"].to_numpy()
    if not np.all((-1e-9 <= active) & (active <= present + 1e-9) & (present <= household_size + 1e-9)):
        raise AssertionError("Invalid occupancy counts")
    if not np.allclose(active + asleep, present):
        raise AssertionError("Present occupancy must equal active plus asleep")
    components = frame["people_gain_w"] + frame["lighting_gain_w"] + frame["appliance_gain_w"]
    if not np.allclose(frame["internal_gain_w"], components):
        raise AssertionError("Internal gain components do not sum")
    non_negative = [
        "people_gain_w",
        "lighting_gain_w",
        "appliance_gain_w",
        "internal_gain_w",
        "electric_load_w",
        "dhw_litre_per_step",
        "dhw_thermal_w",
    ]
    if (frame[non_negative].to_numpy() < 0).any():
        raise AssertionError("Schedule has negative load/gain values")
    expected_setpoint = np.where(
        frame["heating_enabled"].to_numpy(dtype=bool),
        frame["comfort_setpoint_c"].to_numpy(),
        config.setback_setpoint_c,
    )
    if not np.allclose(frame["heating_setpoint_c"], expected_setpoint):
        raise AssertionError("Heating setpoint and enable schedule are inconsistent")


def iter_stock_schedules(
    specs: Iterable[DwellingSpec], config: SimulationConfig
) -> Iterable[ScheduleResult]:
    """Yield one dwelling at a time for memory-safe UBEM integration."""

    seen: set[str] = set()
    for spec in specs:
        spec.validate()
        if spec.dwelling_id in seen:
            raise ValueError(f"Duplicate dwelling_id {spec.dwelling_id!r}")
        seen.add(spec.dwelling_id)
        yield generate_dwelling(spec, config)


def generate_stock(specs: Iterable[DwellingSpec], config: SimulationConfig) -> pd.DataFrame:
    """Generate a long-form dataframe for multiple dwellings.

    For very large stocks, call this function on chunks of ``specs`` and write
    each result to a columnar format such as Parquet.
    """

    frames = [result.frame for result in iter_stock_schedules(specs, config)]
    if not frames:
        raise ValueError("At least one DwellingSpec is required")
    return pd.concat(frames, axis=0)


def read_stock_csv(path: str | Path) -> list[DwellingSpec]:
    table = pd.read_csv(path)
    required = {"dwelling_id", "household_archetype", "floor_area_m2"}
    missing = required - set(table.columns)
    if missing:
        raise ValueError(f"Stock CSV is missing required columns: {sorted(missing)}")
    ids = table["dwelling_id"]
    if ids.isna().any() or ids.astype(str).str.strip().eq("").any():
        raise ValueError("Stock CSV contains a missing or blank dwelling_id")
    duplicated = ids.astype(str)[ids.astype(str).duplicated()].unique().tolist()
    if duplicated:
        raise ValueError(f"Stock CSV contains duplicate dwelling_id values: {duplicated[:10]}")

    specs: list[DwellingSpec] = []
    for record in table.to_dict(orient="records"):
        raw_members = record.get("member_types")
        members = None
        if isinstance(raw_members, str) and raw_members.strip():
            members = tuple(part.strip() for part in raw_members.split(";") if part.strip())
        pattern = record.get("heating_pattern")
        if pd.isna(pattern):
            pattern = None
        comfort = record.get("comfort_setpoint_c")
        if pd.isna(comfort):
            comfort = None
        weight = record.get("stock_weight", 1.0)
        physical_archetype = record.get("physical_archetype_id")
        if pd.isna(physical_archetype):
            physical_archetype = None
        specs.append(
            DwellingSpec(
                dwelling_id=str(record["dwelling_id"]),
                household_archetype=str(record["household_archetype"]),
                floor_area_m2=float(record["floor_area_m2"]),
                physical_archetype_id=(
                    None if physical_archetype is None else str(physical_archetype)
                ),
                member_types=members,
                stock_weight=float(weight),
                heating_pattern=None if pattern is None else str(pattern),
                comfort_setpoint_c=None if comfort is None else float(comfort),
            )
        )
    return specs


def _json_default(value: object) -> object:
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Cannot JSON-encode {type(value)}")


def _cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock", required=True, help="Input stock CSV")
    parser.add_argument("--output", required=True, help="Output schedule CSV")
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--timestep-minutes", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260902)
    parser.add_argument("--realization", type=int, default=0)
    parser.add_argument(
        "--heating-control-mode",
        choices=("efus_mixed", "timer_only", "active_occupancy"),
        default="efus_mixed",
    )
    parser.add_argument(
        "--heating-season-mode",
        choices=("efus_sampled", "fixed_dates"),
        default="efus_sampled",
    )
    parser.add_argument("--timing-source", choices=("chap_microdata", "assumed"), default="chap_microdata")
    parser.add_argument("--timer-sampling", choices=("per_dwelling", "per_day"), default="per_dwelling")
    parser.add_argument("--setpoint-source", choices=("chap_microdata", "normal"), default="chap_microdata")
    parser.add_argument("--occupancy-mode", choices=("crest_tpm", "person_templates"), default="crest_tpm")
    args = parser.parse_args()

    config = SimulationConfig.for_year(
        args.year,
        timestep_minutes=args.timestep_minutes,
        master_seed=args.seed,
        realization=args.realization,
        heating_control_mode=args.heating_control_mode,
        heating_season_mode=args.heating_season_mode,
        timing_source=args.timing_source,
        timer_sampling=args.timer_sampling,
        setpoint_source=args.setpoint_source,
        occupancy_mode=args.occupancy_mode,
    )
    specs = read_stock_csv(args.stock)
    result = generate_stock(specs, config)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path)

    metadata_path = output_path.with_suffix(output_path.suffix + ".metadata.json")
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "simulation": asdict(config),
        "number_of_dwellings": len(specs),
        "archetypes": sorted({spec.household_archetype for spec in specs}),
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, default=_json_default), encoding="utf-8"
    )


if __name__ == "__main__":
    _cli()
