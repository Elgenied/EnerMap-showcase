"""U4 demand/PV coincidence study; reads completed EnerMap runs, never reruns physics.

This module lives outside ukubem so the existing thermal-source fingerprints are
not changed by adding an analysis. All new artefacts go to one separate directory.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib
import json
import platform

import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
import pvlib
import shapely

import config as project
from ukubem import unified as U, measures, heatpumps as HP, pypsa_export as PX
from ukubem.accounting import family_masks, usage_for


@dataclass
class Settings:
    output_dir: str = "outputs/current/u4_pv_balance"
    power_factor: float = 0.95
    tilt_deg: float = 35.0
    albedo: float = 0.20
    batch_size: int = 256
    orientation_sensitivity: bool = True
    full_potential_sensitivity: bool = False


CASE_LABELS = {
    "U0_existing_no_pv": "U0: existing heating, gross demand",
    "U1_electrification_no_pv": "U1: electrification, no PV",
    "U4_capped_south": "U4: current PV cap",
    "U4_full_south": "U4: full assessed PV potential",
    "U4_capped_east_west": "U4: current cap, east/west shape",
    "U4_full_east_west": "U4: full potential, east/west shape",
}


def check_close(a, b, label, checks, rtol=1e-8, atol=1e-5):
    a, b = np.asarray(a), np.asarray(b)
    np.testing.assert_allclose(a, b, rtol=rtol, atol=atol, err_msg=label)
    checks[label] = {"passed": True, "max_abs_error": float(np.max(np.abs(a-b)))}


def load_study(settings=Settings()):
    if not 0 < settings.power_factor <= 1:
        raise ValueError("power_factor must be in (0, 1]")
    for f in ["baseline/group_hourly.npz", "baseline/annual_shapes.npz",
              "scenarios/U0_baseline.parquet", "scenarios/U1_electrification.parquet",
              "scenarios/U4_elec_pv.parquet"]:
        if not (U.OUT/f).exists():
            raise FileNotFoundError(f"Completed current run required: {U.OUT/f}")
    out = U.ROOT/settings.output_dir
    out.mkdir(parents=True, exist_ok=True)
    (out/"figures").mkdir(exist_ok=True)
    inputs, _ = U.load_inputs()
    planning = inputs.loc[inputs.in_study_area.astype(bool)].copy()
    zones = PX.electricity_zones(planning, project)
    mapped = zones.network_assignment_method.eq("spatial_inference_nearest_substation")
    planning.loc[~mapped, ["UPRN", "toid", "LSOA", "pv_kWp", "pv_annual_kWh"]].to_csv(out/"excluded_unassigned_dwellings.csv")
    x = planning.loc[mapped].copy()
    x["zone_id"] = zones.loc[x.index, "electricity_zone_id"]
    if not x.index.is_unique:
        raise ValueError("Duplicate dwelling IDs")
    pvcols = x[["pv_kWp", "pv_annual_kWh"]].to_numpy(float)
    if not np.isfinite(pvcols).all() or (pvcols < 0).any():
        raise ValueError("Missing/negative PV inputs")
    if ((x.pv_kWp.eq(0)) != (x.pv_annual_kWh.eq(0))).any():
        raise ValueError("PV yield and capacity disagree on zero-potential roofs")
    subs = gpd.read_file(project.SUBSTATIONS_GEOJSON).to_crs(27700)
    if subs.functionallocation.duplicated().any():
        raise ValueError("Substation IDs are not unique")
    subs["zone_id"] = "SUB:" + subs.functionallocation.astype(str)
    subs = subs.set_index("zone_id")
    zone_ids = pd.Index(sorted(x.zone_id.unique()), name="zone_id")
    cells = subs.loc[zone_ids].copy()
    cells["modelled_dwellings"] = x.groupby("zone_id").size()
    cells["reported_rating_kVA"] = pd.to_numeric(cells.onanrating, errors="coerce")
    cells["screen_rating_kW"] = cells.reported_rating_kVA * settings.power_factor
    cells["multiple_transformers"] = pd.to_numeric(cells.numberoftransformers).gt(1)
    cells["rating_valid"] = cells.reported_rating_kVA.gt(0) & np.isfinite(cells.reported_rating_kVA)
    cells["modelled_dwellings_per_reported_customer"] = cells.modelled_dwellings / pd.to_numeric(cells.customer_count).replace(0, np.nan)
    checks = {}
    check_close(cells.reported_rating_kVA.reindex(x.zone_id).to_numpy(),
                zones.loc[x.index, "zone_rating_kVA"].to_numpy(), "rating_sources_agree", checks)
    # Reconcile allocated dwelling potential against physical roof totals.
    roofs = gpd.read_file(project.PV_BUILDINGS_GPKG).drop(columns="geometry").set_index("toid")
    if not roofs.index.is_unique:
        raise ValueError("PV roof IDs must be unique")
    roof_audit = x.groupby("toid").agg(modelled_dwellings=("UPRN", "size"),
        allocated_kWp=("pv_kWp", "sum"), allocated_kWh=("pv_annual_kWh", "sum")).join(
        roofs[["peak_power_kwp", "energy_kwh_yr", "addresses", "use_class_final"]])
    if roof_audit.loc[roof_audit.allocated_kWp.gt(0), "peak_power_kwp"].isna().any():
        raise ValueError("Positive dwelling PV has no matching roof source")
    if ((roof_audit.allocated_kWp > roof_audit.peak_power_kwp + 1e-5) |
        (roof_audit.allocated_kWh > roof_audit.energy_kwh_yr + 1e-3)).any():
        raise ValueError("Dwelling allocation duplicates/exceeds a source roof")
    checks["no_duplicated_roof_potential"] = {"passed": True}
    roof_audit.to_csv(out/"roof_allocation_audit.csv")
    x.nlargest(30, "pv_kWp")[["UPRN", "toid", "accommodation_type", "floor_area_final", "zone_id", "pv_kWp", "pv_annual_kWh"]].join(
        roofs[["use_class_final"]], on="toid").to_csv(out/"largest_pv_allocations.csv")
    source = {name: pd.read_parquet(U.OUT/"scenarios"/f"{name}.parquet").loc[x.index]
              for name in ["U0_baseline", "U1_electrification", "U4_elec_pv"]}
    check_close(source["U1_electrification"].delivered_elec_kWh,
                source["U4_elec_pv"].delivered_elec_kWh, "U1_U4_same_gross_demand", checks)
    cap = float(measures.ASSUMPTIONS["pv"]["cap_kWp"])
    x["capped_kWp"] = np.minimum(x.pv_kWp, cap)
    x["capped_kWh"] = x.pv_annual_kWh * (x.capped_kWp / x.pv_kWp.replace(0, np.nan)).fillna(0)
    check_close(x.capped_kWp, source["U4_elec_pv"].pv_kWp_installed, "current_U4_capacity_reproduced", checks)
    check_close(x.capped_kWh, source["U4_elec_pv"].pv_generation_kWh, "current_U4_annual_PV_reproduced", checks)
    x[["UPRN", "toid", "zone_id", "construction_sa", "size_class", "pv_kWp", "pv_annual_kWh", "capped_kWp", "capped_kWh"]].to_csv(out/"dwelling_pv_inputs.csv")
    coverage = pd.DataFrame([
        ("Planning-area dwellings", len(planning)), ("Included mapped dwellings", len(x)),
        ("Unassigned dwellings excluded", int((~mapped).sum())), ("Included substation cells", len(cells)),
        ("Dwellings with positive PV potential", int(x.pv_kWp.gt(0).sum())),
        ("Reported ratings available", int(cells.rating_valid.sum())),
        ("Multi-transformer stations (rating basis unverified)", int(cells.multiple_transformers.sum())),
    ], columns=["item", "count"])
    coverage.to_csv(out/"coverage.csv", index=False)
    return dict(settings=settings, out=out, x=x, cells=cells, all_substations=subs,
                zone_ids=zone_ids, source=source, checks=checks, coverage=coverage,
                cap_kWp=cap, roof_audit=roof_audit)


def solar_shapes(study):
    """EPW interval-average irradiance, midpoint sun angles, fixed model clock.

    The shapes distribute existing annual PV yields. They do not re-estimate
    annual roof potential or apply the paper's efficiency/roof-use factors again.
    """
    s = study["settings"]
    weather, meta = pvlib.iotools.read_epw(U.EPW, coerce_year=2021)
    if len(weather) != 8760 or weather.index.has_duplicates:
        raise ValueError("Expected 8760 EPW intervals")
    for col in ["ghi", "dni", "dhi"]:
        if not np.isfinite(weather[col]).all() or weather[col].max() >= 9999 or weather[col].min() < 0:
            raise ValueError(f"Invalid EPW radiation: {col}")
    solar = pvlib.solarposition.get_solarposition(weather.index + pd.Timedelta(minutes=30),
        meta["latitude"], meta["longitude"], altitude=meta["altitude"])
    zenith, azimuth = solar.apparent_zenith.to_numpy(), solar.azimuth.to_numpy()
    def poa(az):
        p = pvlib.irradiance.get_total_irradiance(s.tilt_deg, az, zenith, azimuth,
            weather.dni.to_numpy(), weather.ghi.to_numpy(), weather.dhi.to_numpy(),
            albedo=s.albedo, model="isotropic")["poa_global"]
        p = np.maximum(np.asarray(p, float), 0)
        p[zenith >= 90] = 0
        return p
    profiles = {"south": poa(180)}
    if s.orientation_sensitivity:
        profiles["east_west"] = 0.5*(poa(90)+poa(270))
    profiles = {k: v/v.sum() for k,v in profiles.items()}
    frame = pd.DataFrame(profiles, index=weather.index.tz_localize(None)).rename_axis("time")
    check_close(frame.sum(), np.ones(len(frame.columns)), "PV_shapes_sum_to_one", study["checks"], atol=1e-12)
    check_close(weather.temp_air.to_numpy(), U.epw_hourly(U.EPW).T_out_C.to_numpy(),
                "PV_and_demand_weather_temperatures_align", study["checks"])
    if frame.loc[zenith >= 90].to_numpy().max() != 0:
        raise AssertionError("Night-time generation")
    frame.to_csv(study["out"]/"pv_hourly_shapes.csv")
    study.update(pv_shapes=profiles, time=frame.index, temperature=weather.temp_air.to_numpy(), weather_meta=meta)
    return frame


def add_by_zone(target, codes, values):
    for code in np.unique(codes):
        target[code] += values[codes == code].sum(axis=0)


def reconstruct(study, electrified, pv_cases=None, fabric=False):
    """Reconstruct household profiles from exactly the current scenario equations.

    Batches avoid storing dwelling x 8760 arrays. Household self-consumption is
    calculated before aggregation; boundary netting is a separate calculation.
    """
    x=study["x"]
    package = list(PX.R_PACKAGES['R2']['measures']) if fabric else []
    if electrified:
        package.append('ashp')
    inp, applied = measures.apply_package(x, package, "planning")
    conv = applied.ashp.to_numpy(bool)
    area = inp.floor_area_final.to_numpy(float)
    masks = family_masks(inp.systems_fam.to_numpy(), inp.gas_connected.fillna(False).to_numpy(bool))
    usage = usage_for(area, masks)
    heat, eshapes, t = U.group_heat("R2" if fabric else "R0"), U.shapes(), study["temperature"]
    water = HP.annual_dhw_shape()
    cop_h = HP.cop_hourly(t, kind="ASHP", emitter="radiator")
    cop_w = HP.cop_hourly(t, kind="ASHP", emitter="dhw")
    codes = study["zone_ids"].get_indexer(x.zone_id)
    nz = len(study["zone_ids"])
    demand = np.zeros((nz,8760))
    cases = pv_cases or {}
    self_by_case = {k:np.zeros_like(demand) for k in cases}
    annual_demand = np.zeros(len(x))
    annual_self = {k:np.zeros(len(x)) for k in cases}
    for g, pos in inp.groupby(["construction_sa","size_class"], observed=True).indices.items():
        q = np.asarray(heat[g],float)/1000
        qnew = HP.heat_pump_hourly(q*1000,t)/1000 if conv[pos].any() else q
        for start in range(0,len(pos),study["settings"].batch_size):
            ii=np.asarray(pos[start:start+study["settings"].batch_size])
            h=np.where(conv[ii,None],qnew[None,:],q[None,:])
            d = usage["elec_nonheat_kWh"][ii,None]*eshapes[g][None,:] + usage["dhw_pou_elec_kWh"][ii,None]*water[None,:]
            d += (area[ii]*masks["hp"][ii])[:,None]*h/cop_h[None,:]
            d += (usage["dhw_system_kWh"][ii]*masks["hp"][ii])[:,None]*water[None,:]/cop_w[None,:]
            d += (area[ii]*masks["direct"][ii])[:,None]*h
            d += (usage["dhw_system_kWh"][ii]*masks["direct"][ii])[:,None]*water[None,:]
            annual_demand[ii]=d.sum(axis=1)
            add_by_zone(demand,codes[ii],d)
            for key,spec in cases.items():
                generation=spec["annual_kWh"][ii,None]*spec["shape"][None,:]
                self_use=np.minimum(d,generation)
                annual_self[key][ii]=self_use.sum(axis=1)
                add_by_zone(self_by_case[key],codes[ii],self_use)
    name=("U3_envelope_elec" if electrified else "U2_envelope") if fabric else ("U1_electrification" if electrified else "U0_baseline")
    check_close(annual_demand,study["source"][name].delivered_elec_kWh,
                f"{name}_every_dwelling_annual_electricity",study["checks"])
    # Independent canonical aggregate accounting checks the entire hourly shape.
    _,canonical = U.stock_accounting(inp,heat,applied.ashp)
    check_close(demand.sum(axis=0),canonical.delivered_electricity_kWh,
                f"{name}_all_8760_hours_match_canonical",study["checks"])
    if not np.isfinite(demand).all() or (demand<0).any():
        raise AssertionError("Invalid demand")
    metrics=pd.DataFrame({"annual_demand_kWh":annual_demand},index=x.index)
    for key in cases: metrics[key+"_self_consumed_kWh"]=annual_self[key]
    metrics.to_csv(study["out"]/f"{name}_dwelling_annual_checks.csv")
    return demand,self_by_case


def balance(demand, generation, household_self):
    """Power balance in kW, one-hour means; no battery, losses or curtailment."""
    net=demand-generation
    return dict(demand_kw=demand, pv_kw=generation,
        dwelling_self_kw=household_self,
        meter_import_kw=np.maximum(demand-household_self,0),
        meter_export_kw=np.maximum(generation-household_self,0),
        net_kw=net, cell_import_kw=np.maximum(net,0), cell_export_kw=np.maximum(-net,0),
        cell_absorbed_pv_kw=np.minimum(demand,generation),
        within_cell_coincidence_kw=np.maximum(np.minimum(demand,generation)-household_self,0))


def synthetic_checks(checks):
    # Two dwellings: one exports 3 kW while its neighbour imports 2 kW.
    d=np.array([[1.],[2.]]);g=np.array([[4.],[0.]])
    b=balance(d.sum(0),g.sum(0),np.minimum(d,g).sum(0))
    check_close([b[k][0] for k in ["meter_import_kw","meter_export_kw","cell_import_kw","cell_export_kw","within_cell_coincidence_kw"]],
                [2,3,0,1,2],"synthetic_meter_vs_cell_balance",checks)
    b=balance(np.array([0.,3.]),np.array([5.,0.]),np.array([0.,0.]))
    check_close(b["cell_import_kw"],[0,3],"synthetic_no_PV_and_no_load_import",checks)
    check_close(b["cell_export_kw"],[5,0],"synthetic_no_PV_and_no_load_export",checks)


def run_balances(study):
    synthetic_checks(study["checks"])
    x=study["x"]; codes=study["zone_ids"].get_indexer(x.zone_id); nz=len(study["zone_ids"])
    cases={}
    for orient,shape in study["pv_shapes"].items():
        for cap in (["capped","full"] if study["settings"].full_potential_sensitivity else ["capped"]):
            key=f"U4_{cap}_{orient}"
            cases[key]={"annual_kWh":x["capped_kWh" if cap=="capped" else "pv_annual_kWh"].to_numpy(float),
                        "capacity_kWp":x["capped_kWp" if cap=="capped" else "pv_kWp"].to_numpy(float),"shape":shape}
    d0,_=reconstruct(study,False)
    d1,selfs=reconstruct(study,True,cases)
    results={"U0_existing_no_pv":balance(d0,np.zeros_like(d0),np.zeros_like(d0)),
             "U1_electrification_no_pv":balance(d1,np.zeros_like(d1),np.zeros_like(d1))}
    capacities={key:np.zeros(nz) for key in results}
    for key,spec in cases.items():
        gy=np.bincount(codes,weights=spec["annual_kWh"],minlength=nz)
        g=gy[:,None]*spec["shape"][None,:]
        results[key]=balance(d1,g,selfs[key])
        capacities[key]=np.bincount(codes,weights=spec["capacity_kWp"],minlength=nz)
        check_close(g.sum(1),gy,key+"_PV_conserves_annual_yield",study["checks"])
    for key,b in results.items():
        check_close(b["demand_kw"],b["cell_import_kw"]+b["cell_absorbed_pv_kw"],key+"_load_balance",study["checks"])
        check_close(b["pv_kw"],b["cell_export_kw"]+b["cell_absorbed_pv_kw"],key+"_PV_balance",study["checks"])
        check_close(b["meter_import_kw"]-b["meter_export_kw"],b["net_kw"],key+"_meter_balance",study["checks"])
        check_close(b["meter_import_kw"]-b["cell_import_kw"],b["within_cell_coincidence_kw"],key+"_coincidence_identity",study["checks"])
        for v in b.values():
            if not np.isfinite(v).all(): raise AssertionError("Nonfinite balance")
    study.update(results=results,capacities=capacities)
    return export_results(study)


def export_results(study):
    out=study["out"]; cells=study["cells"]; time=study["time"]; ids=study["zone_ids"]
    rating=cells.screen_rating_kW.to_numpy(float)
    d1=study["results"]["U1_electrification_no_pv"]["demand_kw"]
    peak_idx=d1.argmax(axis=1)
    records=[]; totals=[]; aggregate={}
    for key,b in study["results"].items():
        ag=pd.DataFrame({k:v.sum(0) for k,v in b.items()},index=time)
        aggregate[key]=ag
        cap=study["capacities"][key]
        cy=b["pv_kw"].sum(1);dy=b["demand_kw"].sum(1)
        sc=b["dwelling_self_kw"].sum(1);absorb=b["cell_absorbed_pv_kw"].sum(1)
        metric=pd.DataFrame({"zone_id":ids,"case":key,"dwelling_count":cells.modelled_dwellings.to_numpy(),
            "pv_capacity_kWp":cap,"demand_MWh":dy/1000,"pv_MWh":cy/1000,
            "meter_import_MWh":b["meter_import_kw"].sum(1)/1000,"meter_export_MWh":b["meter_export_kw"].sum(1)/1000,
            "cell_import_MWh":b["cell_import_kw"].sum(1)/1000,"cell_export_MWh":b["cell_export_kw"].sum(1)/1000,
            "dwelling_self_consumed_MWh":sc/1000,"cell_absorbed_PV_MWh":absorb/1000,
            "additional_cell_coincidence_MWh":(absorb-sc)/1000,
            "dwelling_self_consumption_pct":np.divide(100*sc,cy,out=np.full_like(cy,np.nan),where=cy>0),
            "cell_PV_absorption_pct":np.divide(100*absorb,cy,out=np.full_like(cy,np.nan),where=cy>0),
            "cell_self_sufficiency_pct":100*absorb/dy,
            "gross_peak_kW":b["demand_kw"].max(1),"cell_import_peak_kW":b["cell_import_kw"].max(1),
            "cell_export_peak_kW":b["cell_export_kw"].max(1),
            "import_peak_time":time[b["cell_import_kw"].argmax(1)],
            "export_peak_time":time[b["cell_export_kw"].argmax(1)].where(b["cell_export_kw"].max(1)>0,pd.NaT),
            "reported_rating_kVA":cells.reported_rating_kVA.to_numpy(),"screen_rating_kW":rating,
            "peak_import_rating_ratio":b["cell_import_kw"].max(1)/rating,
            "peak_export_rating_ratio":b["cell_export_kw"].max(1)/rating,
            "import_hours_above_rating_screen":(b["cell_import_kw"]>rating[:,None]).sum(1),
            "export_hours_above_rating_screen":(b["cell_export_kw"]>rating[:,None]).sum(1),
            "PV_offset_at_U1_cell_peak_kW":(d1[np.arange(len(ids)),peak_idx]-b["cell_import_kw"][np.arange(len(ids)),peak_idx]) if key!="U0_existing_no_pv" else np.full(len(ids),np.nan),
            "multiple_transformers":cells.multiple_transformers.to_numpy()})
        records.append(metric)
        csv=ag.rename_axis("time");csv.to_csv(out/f"{key}_aggregate_hourly.csv")
        pd.DataFrame({"zone_id":np.repeat(ids,8760),"time":np.tile(time,len(ids)),
                     **{k:v.ravel() for k,v in b.items()}}).to_parquet(out/f"{key}_cell_hourly.parquet",index=False)
        annual=ag.sum()/1e6
        totals.append({"case":key,"capacity_MWp":cap.sum()/1000,
            "gross_demand_GWh":annual.demand_kw,"PV_GWh":annual.pv_kw,
            "meter_import_GWh":annual.meter_import_kw,"meter_export_GWh":annual.meter_export_kw,
            "cell_import_GWh":annual.cell_import_kw,"cell_export_GWh":annual.cell_export_kw,
            "dwelling_self_consumption_pct":100*annual.dwelling_self_kw/annual.pv_kw if annual.pv_kw else np.nan,
            "cell_PV_absorption_pct":100*annual.cell_absorbed_pv_kw/annual.pv_kw if annual.pv_kw else np.nan,
            "additional_cell_coincidence_GWh":annual.within_cell_coincidence_kw,
            "coincident_cell_import_peak_MW":ag.cell_import_kw.max()/1000,
            "coincident_cell_export_peak_MW":ag.cell_export_kw.max()/1000,
            "cells_above_import_rating_screen":int(metric.peak_import_rating_ratio.gt(1).sum()),
            "cells_above_export_rating_screen":int(metric.peak_export_rating_ratio.gt(1).sum())})
    cell_summary=pd.concat(records,ignore_index=True)
    summary=pd.DataFrame(totals).set_index("case")
    summary.to_csv(out/"scenario_summary.csv");cell_summary.to_csv(out/"cell_summary.csv",index=False)
    monthly=pd.concat({k:v.resample("MS").sum()/1e6 for k,v in aggregate.items()},names=["case","month"])
    monthly.to_csv(out/"monthly_GWh.csv")
    # The original 45/55 annual split is a comparator, never an extra offset.
    src=study["source"]["U4_elec_pv"]
    comparison=pd.DataFrame({"method":["Original fixed annual split","Hourly dwelling matching","Hourly cell boundary matching"],
        "self_or_local_absorption_pct":[100*measures.ASSUMPTIONS["pv"]["self_consumption"],summary.loc["U4_capped_south","dwelling_self_consumption_pct"],summary.loc["U4_capped_south","cell_PV_absorption_pct"]],
        "import_GWh":[src.elec_net_kWh.sum()/1e6,summary.loc["U4_capped_south","meter_import_GWh"],summary.loc["U4_capped_south","cell_import_GWh"]],
        "export_GWh":[src.pv_exported_kWh.sum()/1e6,summary.loc["U4_capped_south","meter_export_GWh"],summary.loc["U4_capped_south","cell_export_GWh"]]})
    comparison.to_csv(out/"original_fixed_split_comparison.csv",index=False)
    study.update(aggregate=aggregate,summary=summary,cell_summary=cell_summary,fixed_split_comparison=comparison)
    return summary


def make_cells(study):
    """Illustrative nearest-point polygons, checked against retained assignments."""
    boundary=gpd.read_file(project.TOWN_GPKG,layer="town_boundary").to_crs(27700).geometry.union_all()
    stations=study["all_substations"]
    geoms=shapely.voronoi_polygons(shapely.MultiPoint(stations.geometry.to_list()),
        extend_to=boundary.envelope.buffer(15000),ordered=True).geoms
    poly=gpd.GeoDataFrame({"zone_id":stations.index,"geometry":[g.intersection(boundary) for g in geoms]},crs=27700).set_index("zone_id")
    study["background_polygons"]=poly.loc[~poly.geometry.is_empty].copy()
    poly=poly.loc[study["zone_ids"]].join(study["cells"].drop(columns="geometry"))
    x=study["x"]
    points=gpd.GeoSeries(gpd.points_from_xy(x.lon,x.lat),index=x.index,crs=4326).to_crs(27700)
    assigned=poly.geometry.reindex(x.zone_id).to_numpy()
    # Geographic labels refer to footprints/addresses; tiny outside-boundary cases are audited.
    covered=shapely.covers(shapely.buffer(assigned,1),points.to_numpy())
    study["checks"]["mapped_points_in_illustrative_cells"]={"passed":bool(covered.all()),"inside":int(covered.sum()),"total":len(x),"note":"Map geometry only; hourly membership always uses original ID mapping."}
    x.loc[~covered,["UPRN","toid","zone_id","lon","lat"]].to_csv(study["out"]/"map_geometry_exceptions.csv")
    target=study["out"]/"substation_cells.gpkg"
    if target.exists(): target.unlink()
    poly.to_file(target,layer="inferred_cells",driver="GPKG")
    study["polygons"]=poly
    return poly


def annual_solar_potential(study):
    """Aggregate existing residential roof allocations, not irradiance or net export."""
    x = study['x']
    table = x.groupby('zone_id').agg(
        modelled_dwellings=('UPRN', 'size'),
        dwellings_with_PV_potential=('pv_kWp', lambda v: int(v.gt(0).sum())),
        assessed_capacity_kWp=('pv_kWp', 'sum'),
        assessed_annual_PV_kWh=('pv_annual_kWh', 'sum'),
        capped_capacity_kWp=('capped_kWp', 'sum'),
        capped_annual_PV_kWh=('capped_kWh', 'sum'),
    ).reindex(study['zone_ids'])
    table['assessed_annual_PV_MWh'] = table.assessed_annual_PV_kWh / 1000
    table['capped_annual_PV_MWh'] = table.capped_annual_PV_kWh / 1000
    table['potential_removed_by_cap_MWh'] = table.assessed_annual_PV_MWh - table.capped_annual_PV_MWh
    table['potential_retained_pct'] = 100 * table.capped_annual_PV_kWh / table.assessed_annual_PV_kWh.replace(0, np.nan)
    for source, target in [('pv_kWp','assessed_capacity_kWp'), ('pv_annual_kWh','assessed_annual_PV_kWh'),
                           ('capped_kWp','capped_capacity_kWp'), ('capped_kWh','capped_annual_PV_kWh')]:
        check_close(table[target].sum(), x[source].sum(), 'annual_cell_solar_' + target, study['checks'])
    if (table.capped_annual_PV_kWh > table.assessed_annual_PV_kWh + 1e-5).any():
        raise AssertionError('Capped cell PV exceeds assessed potential')
    check_close(table.capped_annual_PV_kWh.sum()/1e6,
                study['summary'].loc['U4_capped_south','PV_GWh'],
                'annual_cell_solar_matches_hourly_U4', study['checks'])
    table.to_csv(study['out']/'annual_solar_potential_by_substation.csv')
    geo = study['polygons'][['geometry']].join(table)
    geo.to_file(study['out']/'substation_cells.gpkg', layer='annual_solar_potential', driver='GPKG')
    fig, axs = plt.subplots(1, 2, figsize=(12, 6), layout='constrained')
    upper = float(table.assessed_annual_PV_MWh.max())
    upper = max(upper, 1.)
    for ax, col, title in zip(axs,
            ['assessed_annual_PV_MWh', 'capped_annual_PV_MWh'],
            ['Full assessed rooftop potential', f"PV capped at {study['cap_kWp']:g} kWp per dwelling"]):
        study['background_polygons'].plot(ax=ax, color='#eeeeee', edgecolor='#888888', linewidth=.15)
        geo.plot(column=col, ax=ax, cmap='YlOrRd', vmin=0, vmax=upper,
                 edgecolor='#555555', linewidth=.2)
        ax.set_title(f"{title}\nTotal: {table[col].sum()/1000:.2f} GWh/year", fontsize=11)
        ax.set_axis_off()
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=Normalize(0,upper), cmap='YlOrRd'), ax=axs, shrink=.65)
    cb.set_label('Annual PV generation potential (MWh/year per cell)')
    fig.suptitle('Annual residential solar PV potential by secondary-substation Voronoi cell', fontsize=12)
    fig.supxlabel('Same colour scale in both maps. Grey: no included residential records. Inferred service areas.', fontsize=9)
    savefig(study, fig, '08_annual_solar_potential_by_cell')
    study['annual_solar_potential'] = table
    return table


def savefig(study,fig,name):
    fig.savefig(study["out"]/"figures"/(name+".png"),dpi=180,bbox_inches="tight")
    fig.savefig(study["out"]/"figures"/(name+".pdf"),bbox_inches="tight")
    plt.close(fig)


def make_figures(study):
    plt.rcParams.update({"font.size":10,"axes.spines.top":False,"axes.spines.right":False,"figure.facecolor":"white"})
    main=["U0_existing_no_pv","U1_electrification_no_pv","U4_capped_south"]
    if "U4_full_south" in study["results"]: main.append("U4_full_south")
    colors=["#777777","#a44b28","#207f8c","#33784a"][:len(main)]
    ag=study["aggregate"];summary=study["summary"]
    fig,axs=plt.subplots(1,2,figsize=(12,4.5),layout="constrained")
    names=["Existing\nheating","Electrified\nno PV","Electrified\n4 kWp cap","Electrified\nfull PV"][:len(main)]
    vals=summary.loc[main]
    axs[0].bar(names,vals.cell_import_GWh,color=colors,label="Import across cell boundaries")
    absorbed=vals.gross_demand_GWh-vals.cell_import_GWh
    axs[0].bar(names,absorbed,bottom=vals.cell_import_GWh,color="#d5b669",label="PV absorbed within cells")
    axs[0].set(ylabel="Annual electricity (GWh)",title="Gross demand and its supply")
    axs[0].legend(fontsize=8)
    xx=np.arange(len(main)-2);width=.35
    for j,(metric,label) in enumerate([("dwelling_self_consumption_pct","At dwelling allocation"),("cell_PV_absorption_pct","Within inferred cell")]):
        axs[1].bar(xx+(j-.5)*width,summary.loc[main[2:],metric],width,label=label,color=["#207f8c","#d5b669"][j])
    axs[1].set(xticks=xx,xticklabels=["4 kWp cap","Full assessed potential"][:len(xx)],ylabel="Share of PV generation used (%)",ylim=(0,100),title="PV use at dwelling and cell scales")
    axs[1].legend(fontsize=8)
    savefig(study,fig,"01_annual_balance")
    fig,axs=plt.subplots(2,2,figsize=(12,7.5),layout="constrained")
    for ax,(months,label) in zip(axs.flat,[([1,2,12],"Winter (DJF)"),([6,7,8],"Summer (JJA)"),(list(range(1,13)),"Annual average day"),([],"Actual U1 peak-demand day")]):
        if months:
            curves={k:v.loc[v.index.month.isin(months)].groupby(lambda t:t.hour).mean()/1000 for k,v in ag.items()}
        else:
            day=ag[main[1]].demand_kw.idxmax().normalize()
            curves={k:v.loc[(v.index>=day)&(v.index<day+pd.Timedelta(days=1))].set_axis(range(24))/1000 for k,v in ag.items()}
            label+=f" ({day:%d %b})"
        ax.plot(curves[main[1]].demand_kw,color=colors[1],label="U1 gross electricity")
        ax.plot(curves[main[2]].net_kw,color=colors[2],label="U4: signed whole-area net balance")
        ax.plot(curves[main[2]].cell_import_kw,color="#765a8c",ls=":",label="U4: sum of cell imports")
        if len(main)>3: ax.plot(curves[main[3]].net_kw,color=colors[3],label="U4 full: signed net balance")
        ax.plot(curves[main[2]].pv_kw,color="#c3a23f",ls="--",label="PV generation (4 kWp cap)")
        ax.plot(curves[main[0]].demand_kw,color=colors[0],ls=":",label="U0 existing-heating gross demand")
        ax.axhline(0,color="black",lw=.7);ax.set(title=label,xlabel="Model-clock hour",ylabel="Mean power (MW)",xticks=[0,6,12,18,23]);ax.grid(alpha=.15)
    handles,labels=axs[0,0].get_legend_handles_labels()
    fig.legend(handles,labels,loc="outside lower center",ncol=3,fontsize=8)
    fig.suptitle("Hourly electrical load, PV and grid balance",fontsize=13)
    savefig(study,fig,"02_daily_profiles")
    fig,axs=plt.subplots(1,2,figsize=(12,4.5),layout="constrained")
    for k,c in zip(main,colors):
        monthly=ag[k].resample("MS").sum()/1e6
        axs[0].plot(monthly.index.month,monthly.cell_import_kw,marker="o",color=c,label=CASE_LABELS[k])
        axs[1].plot(np.arange(8760),np.sort(ag[k].cell_import_kw.to_numpy())[::-1]/1000,color=c,label=CASE_LABELS[k])
    axs[0].set(xlabel="Month",ylabel="Import across cells (GWh)",title="Monthly electricity imports",xticks=range(1,13))
    axs[1].set(xlabel="Hours ranked by import",ylabel="Coincident import (MW)",title="Import duration: inspect the annual peak separately")
    axs[0].legend(fontsize=7.5);savefig(study,fig,"03_monthly_and_duration")
    poly=study["polygons"]
    tab=study["cell_summary"].set_index(["case","zone_id"])
    fig,axs=plt.subplots(1,3,figsize=(14,5.5),layout="constrained")
    upper=max(1.1,float(tab.loc[main,"peak_import_rating_ratio"].quantile(.98)))
    for ax,key,title in zip(axs,[main[0],main[1],main[2]],["Existing heating (U0)","Electrification (U1)","Electrification + capped PV (U4)"]):
        g=poly.join(tab.loc[key,["peak_import_rating_ratio"]])
        study["background_polygons"].plot(ax=ax,color="#eeeeee",edgecolor="#888888",linewidth=.15)
        g.plot(column="peak_import_rating_ratio",ax=ax,cmap="YlOrRd",vmin=0,vmax=upper,edgecolor="#444444",linewidth=.15)
        ax.set_title(title);ax.set_axis_off();ax.set_aspect("equal")
    cb=fig.colorbar(plt.cm.ScalarMappable(norm=Normalize(0,upper),cmap="YlOrRd"),ax=axs,shrink=.65,extend="max")
    cb.set_label("Peak import / rating (kW/kW)")
    fig.suptitle("Existing transformer rating screen: inferred cells; residential load only",fontsize=12)
    savefig(study,fig,"04_existing_capacity_screen")
    fig,axs=plt.subplots(1,2,figsize=(11,6),layout="constrained")
    for ax,col,title,cmap in [(axs[0],"cell_PV_absorption_pct","PV absorbed within each cell (%)","YlGnBu"),
                             (axs[1],"cell_export_MWh","Annual export across cell boundary (MWh)","YlOrBr")]:
        g=poly.join(tab.loc[main[2],[col]])
        study["background_polygons"].plot(ax=ax,color="#eeeeee",edgecolor="#888888",linewidth=.15)
        g.plot(column=col,ax=ax,cmap=cmap,legend=True,edgecolor="#444444",linewidth=.15,legend_kwds={"shrink":.6})
        ax.set_title(title,fontsize=10);ax.set_axis_off()
    fig.suptitle("U4 with 4 kWp cap: geographical balance screen",fontsize=12)
    savefig(study,fig,"05_local_absorption_and_export")
    fig,axs=plt.subplots(1,2,figsize=(11,4.5),layout="constrained")
    peak_cases=[(main[2],colors[2],"4 kWp cap")]
    if len(main)>3: peak_cases.append((main[3],colors[3],"Full potential"))
    for key,c,label in peak_cases:
        a=tab.loc[key]
        axs[0].scatter(a.gross_peak_kW,a.cell_import_peak_kW,s=10,alpha=.6,color=c,label=label)
        axs[1].scatter(a.pv_capacity_kWp,a.cell_export_peak_kW,s=10,alpha=.6,color=c,label=label)
    mx=tab.loc[main[1],"gross_peak_kW"].max()
    axs[0].plot([0,mx],[0,mx],color="grey",ls="--",lw=.8)
    axs[0].set(xlabel="U1 gross cell peak (kW)",ylabel="U4 cell import peak (kW)",title="Does PV reduce the annual cell peak?")
    axs[1].set(xlabel="PV capacity (kWp)",ylabel="Peak cell export (kW)",title="Potential export under unchanged ratings")
    axs[0].legend();savefig(study,fig,"06_peaks_and_exports")
    # Fixed-hour spatial balance, following the paper's changing spatial patterns.
    fig,axs=plt.subplots(2,3,figsize=(13,8),layout="constrained")
    net=study["results"][main[2]]["net_kw"]
    vals=[]
    for month in [1,7]:
        for hour in [8,12,18]:
            take=(study["time"].month==month)&(study["time"].hour==hour)
            vals.append(net[:,take].mean(1))
    limit=max(10.,float(np.quantile(np.abs(np.concatenate(vals)),.98)))
    for ax,value,(month,hour) in zip(axs.flat,vals,[(m,h) for m in [1,7] for h in [8,12,18]]):
        study["background_polygons"].plot(ax=ax,color="#eeeeee",edgecolor="#888888",linewidth=.15)
        g=poly.copy();g["net"]=value
        g.plot(column="net",ax=ax,cmap="RdBu_r",vmin=-limit,vmax=limit,edgecolor="#555555",linewidth=.15)
        ax.set_title(f"{'January' if month==1 else 'July'} {hour:02d}:00 (monthly mean)")
        ax.set_axis_off()
    cb=fig.colorbar(plt.cm.ScalarMappable(norm=Normalize(-limit,limit),cmap="RdBu_r"),ax=axs,shrink=.7,extend="both")
    cb.set_label("Cell net power (kW); import + / export −")
    fig.suptitle("U4 at 4 kWp per dwelling: daytime offset and summer exports",fontsize=12)
    savefig(study,fig,"07_time_of_day_cell_balance")
    return sorted((study["out"]/"figures").glob("*.png"))


def write_report(study):
    summary=study["summary"];out=study["out"]
    files=[U.ROOT/"outputs/representatives/dwelling_inputs.parquet",U.BASE/"group_hourly.npz",U.BASE/"annual_shapes.npz",U.EPW,
        project.PV_BUILDINGS_GPKG,project.NETWORK_BUILDINGS_CSV,project.SUBSTATIONS_CSV,project.SUBSTATIONS_GEOJSON,
        U.ROOT/"parameters/pv/rooftop_pv.json",Path(__file__),U.ROOT/"ukubem/unified.py",U.ROOT/"ukubem/heatpumps.py"]
    files += list((U.ROOT/"parameters").rglob("*.json"))
    files += list((U.ROOT/"ukubem").glob("*.py"))
    files += [project.TOWN_GPKG] + [U.OUT/"scenarios"/f"{k}.parquet" for k in study["source"]]
    hashes={str(p.relative_to(U.ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    metadata={"settings":asdict(study["settings"]),"source_hashes":hashes,
        "software":{"python":platform.python_version(),"pandas":pd.__version__,"numpy":np.__version__,"pvlib":pvlib.__version__,"shapely":shapely.__version__},
        "PV_cap_kWp_per_dwelling":study["cap_kWp"],"installed_PV_inventory_available":False,
        "demand_scope":"planning-area residential dwellings with original inferred nearest-substation mapping",
        "rating_basis":"ONAN kVA as reported, not multiplied by numberoftransformers; real-power screen at stated PF",
        "time_basis":"8760 hour-start labels in a fixed 2021 model clock; no daylight-saving shift; sun position at interval midpoint",
        "solar_shape":f"EPW isotropic POA at {study['settings'].tilt_deg:g} degrees; annual-normalised to original assessed roof yields; shared orientation within each run",
        "PV_boundary":"No battery, inverter clipping, network loss, curtailment or export constraint; DC kWp is not an AC export permission",
        "paper":"Park and Wang (2024), Buildings 14, 3398, https://doi.org/10.3390/buildings14113398"}
    (out/"manifest.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")
    (out/"checks.json").write_text(json.dumps(study["checks"],indent=2),encoding="utf-8")
    cols=["capacity_MWp","PV_GWh","cell_import_GWh","cell_export_GWh","dwelling_self_consumption_pct","cell_PV_absorption_pct","coincident_cell_import_peak_MW"]
    table=summary[cols].round(3).to_markdown()
    report=f'''# U4 hourly PV and substation-cell balance

{study['coverage'].to_markdown(index=False)}

## Results

{table}

U0 is the current stock's gross modelled electricity demand with its existing heating systems. It is not measured substation loading and does not deduct unknown existing installed PV. U1 electrifies the eligible fossil-heated dwellings. U4 has exactly U1's gross electricity demand; only PV differs. The primary U4 case uses a 4 kWp-per-dwelling cap, limited by the available allocated roof potential. Full potential is an optional sensitivity, disabled by default. No source scenario outputs or parameters were changed.

## Relation to the Sheffield study

Park and Wang (2024), *An Integrated Urban Building Energy Benchmarking Workflow to Support Urban Energy Evaluation: A Case Study of Sheffield UK*, Buildings 14, 3398, https://doi.org/10.3390/buildings14113398, use hourly D-G balances. Their Section 4 uses 12% module efficiency, 80% performance ratio and 60% roof use. No separate kWp-per-property cap is stated. Therefore they use suitable-roof potential under those assumptions, not every square metre at unrestricted output.

Here the existing EnerMap solar assessment supplies annual yield and capacity. Its roof filters and yield assumptions are retained. The paper's factors are not applied again. Capped U4 reproduces the configured {study['cap_kWp']:g} kWp/dwelling capacity and annual generation; full-potential U4 removes that extra dwelling cap while retaining the assessed allocation to modelled residential addresses. It is neither all roofs in the borough nor observed installed capacity. The largest allocations are separately listed for review.

## Interpretation

* PV offsets electricity, including hourly heat-pump electricity; it is never subtracted from useful heat or gas.
* Dwelling import/export applies min(D,G) at each dwelling PV allocation. Shared roofs retain their existing apportioned shares; this is not a verified metering arrangement.
* Cell import/export uses max(sum(D-G),0) / max(sum(G-D),0). Within-cell coincidence is the difference between local absorption and dwelling self-use. This is an accounting boundary comparison, not additional physical generation, guaranteed household bill savings, peer-to-peer trading or proof of switchable microgrids.
* The overall import/export series sums separate cell boundaries; it never cancels one cell's export against another cell's import. Signed whole-area net balance is reported separately.
* Transformer ratings remain fixed. Reported kVA is converted to an illustrative kW threshold at PF={study['settings'].power_factor:g}. Multi-transformer records are flagged, not multiplied. These are residential-load screening ratios, not actual network headroom or overload findings. Non-residential demand, other customers, reactive power, cable impedances, phases, voltage limits and export permissions are unresolved.
* Every positive annual PV yield is preserved in the hourly profile. The shared south-facing 35-degree shape is a modelling assumption, with equal east/west sensitivity. The same weather clock is used for heat and PV. Hourly roof shading and individual roof directions are not resolved; annual shading/roof suitability remain in the input potential. Inverter clipping and export curtailment are not modelled.
* The fixed 45% self-consumption calculation is included only as a comparison. It is not applied on top of the hourly balance. Canonical U4 annual results are not overwritten.
* Maps regenerate nearest-point Voronoi polygons from all supplied substation points and clip to the planning boundary. Hourly membership always uses the original mapping; any geometric exceptions are exported.

## Validation and files

Every included dwelling's annual demand is reconciled to stored U0/U1 outputs. All 8760 aggregated hours are checked against canonical scenario accounting, without a thermal rerun. Capped PV reproduces current U4 capacity and annual yield, source roofs are checked for duplicate allocations, night-time PV is zero, and import/export energy identities are checked. A two-dwelling synthetic test distinguishes household export from cell export.

`scenario_summary.csv` and `cell_summary.csv` are the principal results. `*_cell_hourly.parquet` contains full per-cell series; `*_aggregate_hourly.csv` contains simultaneous sums. `coverage.csv`, `roof_allocation_audit.csv`, `largest_pv_allocations.csv`, `map_geometry_exceptions.csv`, `checks.json` and `manifest.json` document scope and reproducibility. Figures are saved as PNG and PDF.

`annual_solar_potential_by_substation.csv` and the `annual_solar_potential` layer in `substation_cells.gpkg` give full assessed and capped annual PV generation potential for each included secondary-substation Voronoi cell. Figure 08 maps both on the same colour scale. These are sums of residential dwelling roof shares in the planning area, not all residential/non-residential roofs across the complete service area, measured installed generation, irradiance, self-consumption or export. The uncapped annual inventory is provided even when the full-potential hourly sensitivity is disabled.

Method references: https://pvlib-python.readthedocs.io/en/stable/reference/generated/pvlib.iotools.read_epw.html and https://pvlib-python.readthedocs.io/en/stable/reference/generated/pvlib.irradiance.get_total_irradiance.html
'''
    (out/"README.md").write_text(report,encoding="utf-8")
    return out/"README.md"
