"""EnerMap results explorer. Run with: python -m streamlit run app.py.

Reads only the completed outputs/current snapshot; never imports the engine.
The identical app is distributed in the GitHub showcase.
"""
from pathlib import Path
import json

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

HERE = Path(__file__).resolve().parent
CURRENT = HERE / "outputs" / "current"
FIG = CURRENT / "figures"
SCENARIOS = {
    "U0_baseline": "U0 · Baseline",
    "U1_electrification": "U1 · Electrification",
    "U2_envelope": "U2 · Fabric retrofit",
    "U3_envelope_elec": "U3 · Fabric + electrification",
    "U4_elec_pv": "U4 · Electrification + PV",
    "U5_envelope_elec_pv": "U5 · Fabric + electrification + PV",
}
SCOPE = {"Ten planning wards": "planning_wards", "Borough stock": "borough"}
COLORS = ["#267c88", "#d08a49", "#628dae", "#9a649f", "#609578", "#b56572"]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


@st.cache_data
def load(snapshot_signature):
    # File size/mtime signature invalidates cached tables when a rerun replaces them.
    return {
        "summary": read_json(CURRENT / "baseline/summary.json"),
        "metrics": read_json(CURRENT / "baseline/metrics.json"),
        "completion": read_json(CURRENT / "completion.json"),
        "validation": pd.read_csv(CURRENT / "baseline/lsoa_validation.csv"),
        "alignment": pd.read_csv(CURRENT / "baseline/lsoa_alignment_ranking.csv"),
        "totals": pd.read_csv(CURRENT / "baseline/lsoa_demand_totals.csv"),
        "hourly": pd.read_csv(CURRENT / "baseline/stock_hourly.csv", index_col="time", parse_dates=True),
        "scenarios": pd.read_csv(CURRENT / "scenarios/scenario_summary.csv"),
        "wards": pd.read_csv(CURRENT / "scenarios/planning_ward_summary.csv"),
        "scenario_hourly": {
            code: pd.read_csv(CURRENT / f"scenarios/{code}_hourly.csv", index_col="time", parse_dates=True)
            for code in SCENARIOS
        },
    }


def figure(filename, caption=None):
    path = FIG / filename
    if path.exists():
        st.image(str(path), caption=caption, width="stretch")
    else:
        st.warning(f"Figure unavailable in this snapshot: {filename}")


def csv_download(frame, filename, label="Download table"):
    st.download_button(label, frame.to_csv(index=False).encode("utf-8"), filename, "text/csv", key=filename)


def scatter(frame, fuel, label, color):
    x, y = f"{fuel}_mean_kWh", f"sim_{fuel}_mean_kWh"
    chart = px.scatter(frame, x=x, y=y, hover_name="lsoa_name", hover_data=["LSOA"],
        labels={x: "Observed (kWh / consuming meter / year)", y: "Modelled (kWh / eligible dwelling / year)"},
        title=label, color_discrete_sequence=[color])
    limit = float(frame[[x, y]].max().max()) * 1.06
    chart.add_trace(go.Scatter(x=[0, limit, limit, 0], y=[0, .9*limit, 1.1*limit, 0],
        mode="lines", fill="toself", fillcolor="rgba(120,130,140,0.10)", line={"width": 0},
        name="±10%", hoverinfo="skip"))
    chart.add_trace(go.Scatter(x=[0, limit], y=[0, limit], mode="lines",
        line={"color": "#65747a", "dash": "dash"}, name="Equality", hoverinfo="skip"))
    chart.update_xaxes(range=[0, limit], constrain="domain")
    chart.update_yaxes(range=[0, limit], constrain="domain", scaleanchor="x", scaleratio=1)
    chart.update_layout(height=460, margin={"t": 80, "b": 70},
        legend={"orientation": "h", "y": 1.06, "yanchor": "bottom", "x": 0})
    return chart


st.set_page_config(page_title="EnerMap · LAEP framework", page_icon="🌍", layout="wide")
logo = HERE / "assets/EnerMap_logo.png"
if logo.exists():
    st.image(str(logo), width=270)
st.title("EnerMap framework for local area energy planning")
st.caption("Completed building stock → construction archetypes → household schedules → demand simulation → planning scenarios")

required = [CURRENT / "completion.json", CURRENT / "baseline/summary.json", CURRENT / "baseline/metrics.json",
    CURRENT / "baseline/lsoa_validation.csv", CURRENT / "baseline/lsoa_alignment_ranking.csv",
    CURRENT / "baseline/lsoa_demand_totals.csv", CURRENT / "baseline/stock_hourly.csv",
    CURRENT / "scenarios/scenario_summary.csv", CURRENT / "scenarios/planning_ward_summary.csv"]
required += [CURRENT / f"scenarios/{code}_hourly.csv" for code in SCENARIOS]
missing = [str(p.relative_to(HERE)) for p in required if not p.exists()]
if missing:
    st.error("The current results snapshot is incomplete. Missing: " + ", ".join(missing))
    st.stop()
D = load(tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in required))
if not D["completion"].get("completed") or D["completion"].get("calibration") is not False:
    st.error("This explorer requires a completed uncalibrated results snapshot.")
    st.stop()
M = D["metrics"]
st.sidebar.subheader("Guildford demonstration")
st.sidebar.write(f"Engine run completed {D['completion']['completed_utc'][:10]}.")
st.sidebar.caption("Current method: typical construction properties at three median floor areas, with three household draws per size.")
st.sidebar.write("Planning scenarios: the dense ten-ward study area. Demand and validation: the modelled borough stock and available LSOAs.")
st.sidebar.caption("This app explores saved results. Controls change the presentation; they do not rerun the thermal model.")

cards = st.columns(4)
cards[0].metric("Modelled dwellings", f"{D['summary']['stock_dwellings']:,}")
cards[1].metric("Gas WAPE", f"{M['gas_WAPE_pct']:.2f}%")
cards[2].metric("Electricity WAPE", f"{M['elec_WAPE_pct']:.2f}%")
cards[3].metric("Validation areas", f"{len(D['validation'])} LSOAs")
st.caption("Annual gas and electricity are compared with observed consumption. No parameters were fitted to these benchmarks.")

tabs = st.tabs(["Overview", "Validation", "Demand through time", "Planning scenarios", "Electricity peaks", "Demand maps", "Methods and assumptions"])

with tabs[0]:
    st.subheader("From the baseline to local energy planning")
    st.write("EnerMap links each dwelling to construction, size and household representations, calculates hourly thermal demand, and converts heat into delivered fuel and electricity. The Guildford case demonstrates how those results support electrification, fabric retrofit and rooftop-PV comparisons.")
    totals = D["summary"]["totals_GWh"]
    cols = st.columns(4)
    for col, key, label in zip(cols, ["Q_H_kWh", "delivered_gas_kWh", "delivered_elec_kWh", "delivered_other_kWh"],
        ["Useful space heat", "Delivered gas", "Delivered electricity", "Other fuels"]):
        col.metric(label, f"{totals[key]:,.1f} GWh/year")
    st.caption("Borough totals. Useful space heat and delivered energy describe different stages of the energy balance and should not be added together.")
    figure("09_annual_and_hourly_demand.png")

with tabs[1]:
    st.subheader("Validation against observed annual consumption")
    cols = st.columns(2)
    for col, fuel, label, color in zip(cols, ["gas", "elec"], ["Gas", "Electricity"], [COLORS[0], COLORS[3]]):
        with col:
            st.plotly_chart(scatter(D["validation"], fuel, label, color), width="stretch")
    assessment = pd.DataFrame([{"Fuel": label, "WAPE (%)": M[f"{fuel}_WAPE_pct"],
        "Signed bias (%)": M[f"{fuel}_NMBE_pct"], "Predictive R²": M[f"{fuel}_R2_predictive"]}
        for fuel, label in [("gas", "Gas"), ("elec", "Electricity")]])
    st.dataframe(assessment.round(3), hide_index=True, width="stretch")
    st.write("WAPE is the meter-count-weighted absolute discrepancy between area means. Signed bias shows the overall direction of the difference. The shaded ±10% band is a visual reference, not a formal acceptance threshold.")
    st.caption("Modelled means are per eligible dwelling; observed means are per consuming meter. These observations were inspected during development. Annual area comparisons do not establish dwelling-level or hourly accuracy.")
    figure("06_validation_maps.png", "Where annual modelled and observed consumption agree or differ")
    figure("06_canet_style_distribution.png", "Canet-inspired presentation of area errors; the energy boundary and sign convention follow this study")
    st.subheader("Which areas align most closely?")
    ranking = D["alignment"]
    fields = ["lsoa_name", "LSOA", "gas_error_pct", "elec_error_pct", "joint_absolute_error_pct"]
    st.dataframe(ranking[fields].round(2), hide_index=True, width="stretch")
    st.caption("Sorted by the mean of each area's absolute gas and electricity percentage errors. Every validation area is retained. This ranking is distinct from the weighted WAPE metric.")
    figure("06_lsoa_alignment.png")
    csv_download(ranking, "lsoa_validation_and_alignment.csv")

with tabs[2]:
    st.subheader("Hourly demand across the modelled borough stock")
    hh = D["hourly"]
    enduses = {"space_heat_kWh": "Useful space heat", "dhw_useful_kWh": "Useful hot water",
        "nonheating_electricity_kWh": "Non-heating electricity", "delivered_electricity_kWh": "Total electricity"}
    selected = st.multiselect("Series", list(enduses.values()), default=["Useful space heat", "Total electricity"])
    chosen = [key for key, label in enduses.items() if label in selected]
    week = st.slider("Demand week", 1, 53, 3)
    if chosen:
        view = hh[chosen].rename(columns=enduses) / 1000
        st.line_chart(view.resample("D").mean(), y_label="Daily mean demand (MW)")
        st.line_chart(view.iloc[(week-1)*168:week*168], y_label="Hourly mean demand (MW)")
    st.caption("An hourly energy value in kWh divided by 1,000 equals average MW over that one-hour interval. Week 53 contains the final 24 hours of the 8,760-hour model year. Electricity components are included within total electricity.")
    csv_download(hh.reset_index(), "baseline_stock_hourly.csv", "Download hourly baseline")

with tabs[3]:
    st.subheader("Electrification, fabric retrofit and rooftop PV")
    scope_label = st.radio("Scenario results area", list(SCOPE), horizontal=True)
    ss = D["scenarios"].loc[D["scenarios"].scope.eq(SCOPE[scope_label])].copy()
    ss["Scenario"] = ss.scenario.map(SCENARIOS)
    st.caption(f"{scope_label}: {int(ss.dwellings.iloc[0]):,} dwellings. Annual totals under the retained project assumptions.")
    energy = ss.rename(columns={"delivered_gas_GWh": "Gas", "delivered_elec_GWh": "Electricity", "delivered_other_GWh": "Other fuels"})
    a, b = st.columns(2)
    with a:
        st.plotly_chart(px.bar(energy, y="Scenario", x=["Gas", "Electricity", "Other fuels"], orientation="h",
            title="Delivered energy before PV offsets", labels={"value": "GWh/year", "variable": "Fuel"},
            color_discrete_sequence=COLORS), width="stretch")
    with b:
        st.plotly_chart(px.bar(ss, y="Scenario", x="carbon_kt", orientation="h", title="Operational carbon",
            labels={"carbon_kt": "ktCO₂e/year"}, color_discrete_sequence=[COLORS[0]]), width="stretch")
    st.dataframe(ss.drop(columns="Scenario").round(2), hide_index=True, width="stretch")
    csv_download(ss.drop(columns="Scenario"), "scenario_summary_selected_area.csv")
    st.caption("Net annual electricity includes the assumed PV self-consumption offset. Costs and carbon factors are retained project inputs. PV inputs cover the original planning area, so borough PV results do not represent an exhaustive rooftop assessment.")
    scenario = st.selectbox("Scenario by ward", list(SCENARIOS), format_func=SCENARIOS.get, index=5)
    wards = D["wards"].loc[D["wards"].scenario.eq(scenario)]
    st.plotly_chart(px.bar(wards.rename(columns={"gas_GWh": "Gas", "electricity_GWh": "Electricity"}), x="ward", y=["Gas", "Electricity"], barmode="group",
        labels={"value": "GWh/year", "ward": "Ward", "variable": "Delivered energy"}, color_discrete_sequence=COLORS), width="stretch")
    st.dataframe(wards.round(2), hide_index=True, width="stretch")
    figure("10_retrofit_saving_map.png")

with tabs[4]:
    st.subheader("Simultaneous hourly electricity demand")
    st.write("These profiles cover the borough's modelled residential stock. Peaks are calculated from the sum of demand at the same hour, before PV offsets. They have not been validated against hourly electricity measurements.")
    profiles = pd.DataFrame({SCENARIOS[k]: v.delivered_electricity_kWh / 1000 for k, v in D["scenario_hourly"].items()})
    choices = st.multiselect("Compare hourly scenarios", list(SCENARIOS.values()), default=list(SCENARIOS.values())[:2])
    peak_week = st.slider("Electricity week", 1, 53, 3)
    if choices:
        st.line_chart(profiles[choices].iloc[(peak_week-1)*168:peak_week*168], y_label="Gross electricity demand (MW)")
    peaks = pd.DataFrame({"Scenario": profiles.columns, "Peak (MW)": profiles.max().values,
        "Peak time": profiles.idxmax().astype(str).values, "Annual electricity (GWh)": (profiles.sum()/1000).values})
    st.dataframe(peaks.round(2), hide_index=True, width="stretch")
    csv_download(peaks, "borough_scenario_electricity_peaks.csv")
    st.caption("PV scenarios can have the same gross demand peak as the corresponding case without PV. Annual PV offsets do not supply an hourly grid-import profile.")

with tabs[5]:
    st.subheader("Locating demand across the completed building stock")
    figure("09_demand_totals_maps.png", "Annual LSOA demand totals")
    figure("09_building_demand_maps.png", "Demand assigned to building footprints")
    figure("09_neighbourhood_demand_detail.png", "Neighbourhood detail")
    csv_download(D["totals"], "lsoa_demand_totals.csv")

with tabs[6]:
    st.subheader("How annual and hourly demand connect")
    st.markdown("""1. **Space heating:** cluster-median construction properties and modal categories define virtual buildings at exact median small, medium and large floor areas. Roof properties are conditioned on template exposure. Three household draws are averaged for each size, then expanded using each dwelling's original floor area. The original dwelling records are not overwritten.
2. **Appliances, lighting and cooking:** annual BREDEM/SAP-based quantities are distributed using normalized hourly profiles. Raw schedule totals are not a second final annual estimate.
3. **Hot water:** annual usage defines useful tap heat, system-side heat including losses, and electric-shower electricity. The hourly profile is normalized to those quantities.
4. **Heat pumps:** useful space heat and system-side hot-water heat are divided by hourly COP. Annual heat-pump electricity is the sum of those hourly values. Electric-shower electricity is counted separately.
5. **Delivered energy:** boiler efficiencies and other system assumptions convert heat into the gas, electricity and other fuels compared with annual observations.""")
    st.latex(r"T_{\mathrm{sink},h}=40-T_{\mathrm{out},h},\quad \Delta T_h=\max(T_{\mathrm{sink},h}-T_{\mathrm{out},h},15)")
    st.latex(r"\mathrm{COP}_h=\max(1,6.08-0.09\Delta T_h+0.0005\Delta T_h^2),\quad E_{\mathrm{HP},h}=Q_h/\mathrm{COP}_h")
    st.write("Space heating uses the radiator-temperature rule above; hot water uses a 50°C sink. Existing heat pumps use the ASHP curve. New electrification retains the configured profile redistribution and 1.08 space-heat uplift; this uplift is not applied again to existing heat pumps.")
    st.write("The rerun corrected thermal-capacity units, thermal-bridge handling, internal-gain overrides, no-ground geometry and heat-balance reporting. Completed-stock inputs, clustering and household sampling were retained.")
    st.caption("The simplified COP curve omits defrost, backup and cycling. Three draws per representative do not establish uncertainty convergence. Annual agreement does not validate occupancy, individual buildings, hourly peaks or future scenarios.")
    st.write("The median construction profiles are modelling representatives, not measured individual homes. Remaining geometry and roof exposure come from each size template. Retrofit eligibility and costs retain individual stock attributes, while simulated savings are shared within each archetype-size group.")
    st.subheader("Run record")
    st.json({"completed_utc": D["completion"]["completed_utc"], "calibration": False,
        "representation_method": D["completion"].get("representation_method"),
        "baseline_simulations": D["completion"]["baseline_simulations"],
        "stochastic_baseline_simulations": D["summary"]["stochastic_simulations"],
        "retrofit_simulations": D["completion"]["retrofit_simulations"],
        "annual_hourly_consistency_passed": D["summary"]["annual_hourly_consistency_passed"]})
