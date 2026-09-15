"""
Decarbonisation measures, packages, costs, incentives and the low-hanging-fruit ranking.

The scenario set (U0 baseline, U1 electrification,
U2 envelope, U3 envelope + electrification, U4 electrification + PV, U5 envelope + electrification + PV)
and the low-hanging-fruit analysis (benefit-cost
ranking in kgCO2e per pound over a 15-year horizon, payback against the 12.5-year Wilson threshold,
A-F quantile groups, hard-to-decarbonise split) on top of this tool's ISO 52016 archetype tier.

Eligibility depends on CONSTRUCTION: cavity fill only on uninsulated/partial cavity
walls, internal wall insulation only on solid masonry, loft top-up only under an exposed pitched roof
above the trigger U-value, flat-roof insulation only on exposed flat roofs, glazing replacement only
for single/secondary glazing, ASHP only for fossil boilers (communal, existing heat pumps and electric
heating untouched), PV only where the rooftop study gives a potential.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import params

FOSSIL = ["S.GasBoiler", "S.OilBoiler", "S.LPGBoiler", "S.SolidFuel"]

# every assumption comes from parameters/ (ukubem.params)
def _assumptions() -> dict:
    """Every scenario assumption from parameters/ (retrofit_measures, pv, energy_prices, grid_carbon, finance_incentives)."""
    ft = params.load("retrofit_measures", "fabric_targets"); uc = params.load("retrofit_measures", "unit_costs")
    pv = params.load("pv", "rooftop_pv"); pr = params.load("energy_prices", "prices_2021")
    cf = params.load("grid_carbon", "carbon_factors_2021"); gt = params.load("grid_carbon", "grid_trajectory")
    fin = params.load("finance_incentives", "finance"); inc = params.load("finance_incentives", "incentives")
    return {
        "targets": {k: v for k, v in ft.items() if k != "source"},
        "unit_costs_GBP": dict(uc["fabric_GBP_per_unit"]),
        "ashp": dict(uc["ashp"]),
        "pv": {**uc["pv_cost"], "cap_kWp": pv["cap_kWp"], "self_consumption": pv["self_consumption"], "seg_export_GBP_per_kWh": pv["seg_export_GBP_per_kWh"]},
        "prices_GBP_per_kWh": dict(pr["GBP_per_kWh"]),
        "carbon_kg_per_kWh": dict(cf["kg_per_kWh"]),
        "grid_trajectory": {"years": gt["years"], "elec_kg_per_kWh": gt["elec_kg_per_kWh"]},
        "finance": {k: v for k, v in fin.items() if k != "source"},
        "incentives": {k: v for k, v in inc.items() if k != "source"},
    }


ASSUMPTIONS = _assumptions()
PACKAGES = dict(params.load("retrofit_measures", "packages")["ward_scenarios"])   # parameters/retrofit_measures/packages.json
SCENARIO_NOTES = {
    "U0_baseline": "2021 stock as modelled (calibrated archetype tier)",
    "U1_electrification": "every fossil-boiler dwelling to an ASHP (space + DHW), fabric unchanged",
    "U2_envelope": "construction-eligible fabric package only",
    "U3_envelope_elec": "fabric first, then full electrification (heat pumps sized on the improved fabric)",
    "U4_elec_pv": "electrification plus rooftop PV where the rooftop study gives a potential (cap 4 kWp)",
    "U5_envelope_elec_pv": "fabric, electrification and PV together",
}


def eligibility(inputs: pd.DataFrame, target_set: str = "planning") -> pd.DataFrame:
    T = ASSUMPTIONS["targets"][target_set]
    d = inputs
    wall = d["wall_construction_resolved"].astype(str); ins = d["wall_insulation"].astype(str)
    roof_type = d["roof_type"].astype(str).str.lower()
    flat_roof = d["is_flat_roof"].astype(bool) | roof_type.str.startswith("flat")
    exposed = d["roof_exposed"].astype(int).eq(1)
    e = pd.DataFrame(index=d.index)
    e["cavity_fill"] = wall.isin(["Cavity", "System built"]) & ins.isin(["Uninsulated", "Partial", "Unknown"])
    e["solid_wall_iwi"] = wall.isin(["Solid brick", "Stone"]) & ~ins.eq("Insulated")
    e["loft_topup"] = exposed & ~flat_roof & (d["roof_U"] > T["loft_eligible_above_U"])
    e["flat_roof"] = exposed & flat_roof & (d["roof_U"] > T["flat_roof_eligible_above_U"])
    e["glazing_upgrade"] = d["glazing_type"].astype(str).isin(["Single", "Secondary"])
    e["draught_proofing"] = True
    e["ashp"] = d["systems_fam"].astype(str).isin(FOSSIL)
    e["pv"] = (d["pv_kWp"] > 0) if "pv_kWp" in d else False
    return e


def apply_package(inputs: pd.DataFrame, package: list[str], target_set: str = "planning") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (modified inputs, applied-measure flags). Fabric changes edit U-values / SHGC; `ach_factor` carries draught proofing."""
    T = ASSUMPTIONS["targets"][target_set]
    d = inputs.copy(); e = eligibility(inputs, target_set)
    applied = pd.DataFrame(False, index=d.index, columns=list(e.columns))
    d["ach_factor"] = 1.0
    for m in package:
        mask = e[m]
        applied[m] = mask
        if m == "cavity_fill":
            d.loc[mask, "wall_U"] = np.minimum(d.loc[mask, "wall_U"], T["cavity_fill_U"]); d.loc[mask, "wall_insulation"] = "Insulated"
        elif m == "solid_wall_iwi":
            d.loc[mask, "wall_U"] = np.minimum(d.loc[mask, "wall_U"], T["solid_wall_iwi_U"]); d.loc[mask, "wall_insulation"] = "Insulated"
        elif m == "loft_topup":
            d.loc[mask, "roof_U"] = np.minimum(d.loc[mask, "roof_U"], T["loft_topup_U"])
        elif m == "flat_roof":
            d.loc[mask, "roof_U"] = np.minimum(d.loc[mask, "roof_U"], T["flat_roof_U"])
        elif m == "glazing_upgrade":
            d.loc[mask, "window_U"] = np.minimum(d.loc[mask, "window_U"], T["glazing_U"]); d.loc[mask, "window_SHGC"] = T["glazing_SHGC"]
        elif m == "draught_proofing":
            d.loc[mask, "ach_factor"] = T["draught_ach_factor"]
        elif m == "ashp":
            d.loc[mask, "systems_fam"] = "S.HeatPump"
        elif m == "pv":
            pass                                              # PV does not change the thermal model; see `pv_terms`
    return d, applied


def envelope_areas(inputs: pd.DataFrame, assumptions=None) -> pd.DataFrame:
    """Per-dwelling envelope areas and heat-transfer coefficients from the tool's own geometry (build_bui derived dict)."""
    from .geometry import UKGeometryAssumptions, build_bui
    a = assumptions or UKGeometryAssumptions()
    rows = []
    for did, r in inputs.iterrows():
        try:
            _, dv = build_bui(r, a)
            rows.append({"dwelling_id": did, **{k: dv[k] for k in ("A_floor", "A_wall", "A_window", "A_roof", "A_ground", "HTC_fabric_W_K", "H_ve_W_K")}})
        except Exception:
            rows.append({"dwelling_id": did})
    return pd.DataFrame(rows).set_index("dwelling_id")


def ashp_size_kW(htc_total_W_K: pd.Series) -> pd.Series:
    A = ASSUMPTIONS["ashp"]
    kw = htc_total_W_K * (A["design_T_in_C"] - A["design_T_out_C"]) / 1000.0
    return (np.ceil(kw * 2) / 2).clip(A["min_kW"], A["max_kW"])


def measure_costs(inputs: pd.DataFrame, applied: pd.DataFrame, areas: pd.DataFrame, htc_total_W_K: pd.Series | None = None) -> pd.DataFrame:
    """Installed cost per applied measure (GBP, 2021 prices; parameters/retrofit_measures) and the ASHP size."""
    U = ASSUMPTIONS["unit_costs_GBP"]; A = ASSUMPTIONS["ashp"]; P = ASSUMPTIONS["pv"]
    c = pd.DataFrame(0.0, index=inputs.index, columns=[f"cost_{m}_GBP" for m in applied.columns])
    ar = areas.reindex(inputs.index)
    c["cost_cavity_fill_GBP"] = np.where(applied["cavity_fill"], U["cavity_fill"] * ar["A_wall"], 0.0)
    c["cost_solid_wall_iwi_GBP"] = np.where(applied["solid_wall_iwi"], U["solid_wall_iwi"] * ar["A_wall"], 0.0)
    c["cost_loft_topup_GBP"] = np.where(applied["loft_topup"], U["loft_topup"] * ar["A_roof"], 0.0)
    c["cost_flat_roof_GBP"] = np.where(applied["flat_roof"], U["flat_roof"] * ar["A_roof"], 0.0)
    c["cost_glazing_upgrade_GBP"] = np.where(applied["glazing_upgrade"], U["glazing_upgrade"] * ar["A_window"], 0.0)
    c["cost_draught_proofing_GBP"] = np.where(applied["draught_proofing"], U["draught_proofing"], 0.0)
    if htc_total_W_K is None:
        htc_total_W_K = ar["HTC_fabric_W_K"] + ar["H_ve_W_K"]
    c["ashp_kW"] = np.where(applied["ashp"], ashp_size_kW(htc_total_W_K.reindex(inputs.index)), 0.0)
    elec_heated = inputs["systems_fam"].astype(str).isin(["S.ElecResistive", "S.ElecStorage"])
    c["cost_ashp_GBP"] = np.where(applied["ashp"], A["fixed_GBP"] + A["per_kW_GBP"] * c["ashp_kW"] + np.where(elec_heated, A["wet_system_uplift_GBP"], 0.0), 0.0)
    kwp = np.minimum(inputs["pv_kWp"].fillna(0.0), P["cap_kWp"]) if "pv_kWp" in inputs else 0.0
    c["pv_kWp_costed"] = np.where(applied["pv"], kwp, 0.0)                 # same capacity as pv_terms' pv_kWp_installed
    c["cost_pv_GBP"] = np.where(applied["pv"], P["fixed_GBP"] + P["per_kWp_GBP"] * c["pv_kWp_costed"], 0.0)
    c["capex_gross_GBP"] = c[[k for k in c.columns if k.startswith("cost_")]].sum(axis=1)
    return c


def incentives(inputs: pd.DataFrame, costs: pd.DataFrame, social: pd.DataFrame | None = None) -> pd.DataFrame:
    """Incentive proxies: BUS (ASHP), ECO4 proxy (area-proxied means test), GBIS proxy."""
    I = ASSUMPTIONS["incentives"]
    tenure = inputs["tenure"].astype(str).str.lower()
    fp = social["fuel_poor_pct"].reindex(inputs["LSOA"]).to_numpy() if social is not None and "fuel_poor_pct" in social else np.full(len(inputs), np.nan)
    imd = social["imd_decile"].reindex(inputs["LSOA"]).to_numpy() if social is not None and "imd_decile" in social else np.full(len(inputs), np.nan)
    eco4_elig = (fp >= 12) | (imd <= 3) | tenure.eq("rented (social)").to_numpy()
    out = pd.DataFrame(index=inputs.index)
    out["inc_BUS_GBP"] = np.where((costs["cost_ashp_GBP"] > 0) & ~tenure.eq("rented (social)").to_numpy(), I["BUS"]["amount"], 0.0)
    fab_cost = costs[[f"cost_{m}_GBP" for m in I["ECO4_proxy"]["measures"]]].sum(axis=1)
    out["inc_ECO4_proxy_GBP"] = np.where(eco4_elig, np.minimum(I["ECO4_proxy"]["share"] * fab_cost, I["ECO4_proxy"]["cap"]), 0.0)
    gbis_cost = costs[[f"cost_{m}_GBP" for m in I["GBIS_proxy"]["measures"]]].sum(axis=1)
    gbis_elig = (inputs["floor_area_final"] < 120).to_numpy() & ~eco4_elig
    out["inc_GBIS_proxy_GBP"] = np.where(gbis_elig, np.minimum(I["GBIS_proxy"]["share"] * gbis_cost, I["GBIS_proxy"]["cap"]), 0.0)
    out["incentives_GBP"] = out.sum(axis=1)
    out["eco4_eligible"] = eco4_elig
    return out


def pv_terms(inputs: pd.DataFrame, applied_pv: pd.Series) -> pd.DataFrame:
    """Annual PV generation, self-consumed and exported electricity for the installed (capped) capacity."""
    P = ASSUMPTIONS["pv"]
    pot_kwp = inputs["pv_kWp"].fillna(0.0) if "pv_kWp" in inputs else pd.Series(0.0, index=inputs.index)
    pot_kwh = inputs["pv_annual_kWh"].fillna(0.0) if "pv_annual_kWh" in inputs else pd.Series(0.0, index=inputs.index)
    kwp = np.where(applied_pv & (pot_kwp > 0), np.minimum(pot_kwp, P["cap_kWp"]), 0.0)
    gen = np.where(pot_kwp > 0, pot_kwh * kwp / pot_kwp.replace(0, np.nan), 0.0)
    gen = np.nan_to_num(gen)
    return pd.DataFrame({"pv_kWp_installed": kwp, "pv_generation_kWh": gen, "pv_self_consumed_kWh": P["self_consumption"] * gen,
                         "pv_exported_kWh": (1 - P["self_consumption"]) * gen}, index=inputs.index)


def bills_and_carbon(stock: pd.DataFrame, pv: pd.DataFrame | None = None, elec_factor: float | None = None) -> pd.DataFrame:
    """Annual bill (GBP) and carbon (kgCO2e) per dwelling from delivered fuels (2021 prices and factors; exports credited to bills only)."""
    PR = ASSUMPTIONS["prices_GBP_per_kWh"]; CF = dict(ASSUMPTIONS["carbon_kg_per_kWh"])
    if elec_factor is not None:
        CF["elec"] = elec_factor
    s = stock.set_index("dwelling_id") if "dwelling_id" in stock.columns else stock
    fam = s["systems_fam"].astype(str)
    other_price = fam.map(PR).fillna(PR["other"]); other_cf = fam.map(CF).fillna(CF["other"])
    elec = s["delivered_elec_kWh"].copy(); export_income = pd.Series(0.0, index=s.index)
    if pv is not None:
        pv = pv.reindex(s.index).fillna(0.0)
        elec = (elec - pv["pv_self_consumed_kWh"]).clip(lower=0.0)
        export_income = pv["pv_exported_kWh"] * ASSUMPTIONS["pv"]["seg_export_GBP_per_kWh"]
    out = pd.DataFrame(index=s.index)
    out["bill_GBP"] = s["delivered_gas_kWh"] * PR["gas"] + elec * PR["elec"] + s["delivered_other_kWh"] * other_price - export_income
    out["carbon_kg"] = s["delivered_gas_kWh"] * CF["gas"] + elec * CF["elec"] + s["delivered_other_kWh"] * other_cf
    out["elec_net_kWh"] = elec
    return out


def economics(base: pd.DataFrame, scen: pd.DataFrame, costs: pd.DataFrame, inc: pd.DataFrame) -> pd.DataFrame:
    """Low-hanging-fruit economics per dwelling: savings, net upfront, payback, benefit/cost (kgCO2e per GBP over the horizon), ROI."""
    F = ASSUMPTIONS["finance"]
    e = pd.DataFrame(index=base.index)
    e["bill_saving_GBP"] = base["bill_GBP"] - scen["bill_GBP"].reindex(base.index)
    e["carbon_saving_kg"] = base["carbon_kg"] - scen["carbon_kg"].reindex(base.index)
    e["capex_gross_GBP"] = costs["capex_gross_GBP"].reindex(base.index)
    e["incentives_GBP"] = inc["incentives_GBP"].reindex(base.index)
    e["net_upfront_GBP"] = (e["capex_gross_GBP"] - e["incentives_GBP"]).clip(lower=0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        e["payback_years"] = np.where(e["bill_saving_GBP"] > 0, e["net_upfront_GBP"] / e["bill_saving_GBP"], np.inf)
        e["benefit_cost_kg_per_GBP"] = np.where(e["net_upfront_GBP"] > 0, e["carbon_saving_kg"] * F["horizon_years"] / e["net_upfront_GBP"], np.nan)
        e["benefit_cost_gross_kg_per_GBP"] = np.where(e["capex_gross_GBP"] > 0, e["carbon_saving_kg"] * F["horizon_years"] / e["capex_gross_GBP"], np.nan)
        e["roi"] = np.where(e["net_upfront_GBP"] > 0, e["bill_saving_GBP"] / e["net_upfront_GBP"], np.nan)
    e["payback_ok"] = e["payback_years"] <= F["acceptable_payback_years"]
    return e


def rank_groups(econ: pd.DataFrame, metric: str = "benefit_cost_kg_per_GBP") -> pd.Series:
    """A-F groups by quantile cuts of the ranking metric among dwellings with a positive net upfront cost (A = top 5 %)."""
    F = ASSUMPTIONS["finance"]
    v = econ.loc[(econ["net_upfront_GBP"] > 0) & np.isfinite(econ[metric]), metric]
    cuts = v.quantile([1 - q for q in F["group_quantiles"]]).to_numpy()      # descending thresholds
    labels = ["A", "B", "C", "D", "E", "F"]
    g = pd.Series(pd.NA, index=econ.index, dtype="object")
    for i, x in v.items():
        k = int(np.sum(x < cuts))                                            # 0 -> A (above the top cut)
        g.loc[i] = labels[min(k, 5)]
    return g


def hard_to_decarbonise(inputs: pd.DataFrame) -> pd.Series:
    """Hard-to-decarbonise split: solid/stone walls, off-gas fossil fuels, pre-1900 band A, park homes."""
    return (inputs["wall_construction_resolved"].astype(str).isin(["Solid brick", "Stone"])
            | inputs["systems_fam"].astype(str).isin(["S.OilBoiler", "S.LPGBoiler", "S.SolidFuel"])
            | inputs["age_band_resolved"].astype(str).eq("A")
            | inputs["accommodation_type"].astype(str).str.lower().str.startswith("caravan"))
