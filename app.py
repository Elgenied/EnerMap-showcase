"""EnerMap review dashboard. Run: python -m streamlit run app.py. Reads saved aggregate results only."""
from pathlib import Path
import json
import pandas as pd
import streamlit as st

HERE = Path(__file__).parent
UB = HERE / "outputs" / "ubem"; FIG = HERE / "outputs" / "figures"
SCN = {"U0_baseline": "U0 baseline", "U1_electrification": "U1 electrification", "U2_envelope": "U2 envelope",
       "U3_envelope_elec": "U3 envelope + electrification", "U4_elec_pv": "U4 electrification + PV", "U5_envelope_elec_pv": "U5 envelope + electrification + PV"}

st.set_page_config(page_title="EnerMap · Guildford", layout="wide")
st.image(str(HERE / "assets" / "EnerMap_logo.png"), width=360)
st.title("EnerMap: a UBEM tool for local area energy planning")
st.caption("Guildford demonstration: 57,300 dwellings, 84 LSOAs, ten study-area wards. Saved model outputs for exploration; interpretation and verification limits are documented in docs/INTERPRETATION.md.")


@st.cache_data
def load():
    d = {}
    d["ss"] = pd.read_csv(UB / "10_scenario_summary.csv"); d["ss"]["scenario"] = d["ss"]["scenario"].map(SCN)
    d["sw"] = pd.read_csv(UB / "10_scenario_by_ward.csv"); d["sw"]["scenario"] = d["sw"]["scenario"].map(SCN)
    d["pk"] = pd.read_csv(UB / "10_ward_peaks.csv"); d["pk"]["scenario"] = d["pk"]["scenario"].map(SCN)
    d["cal"] = pd.read_csv(UB / "06_lsoa_calibrated.csv")
    d["val"] = pd.read_csv(UB / "09_validation_table.csv").rename(columns={"Unnamed: 0": "population"})
    d["flex"] = pd.read_csv(UB / "10_flexibility_by_ward.csv")
    d["meth"] = pd.read_csv(UB / "10_peak_method_comparison.csv")
    d["hh"] = pd.read_csv(UB / "09_stock_hourly_heat_MW.csv", index_col=0, parse_dates=True)
    d["tw"] = pd.read_csv(UB / "10_tenward_hourly_elec_MW.csv", index_col=0, parse_dates=True)
    d["theta"] = json.load(open(UB / "06_calibrated_theta.json"))
    d["atlas"] = pd.read_csv(UB / "09_lsoa_atlas.csv")
    return d


D = load()
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Gas WAPE, in-sample", "6.5 %", "bias −2.1 %"); k2.metric("Gas WAPE, spatial refits", "6.7 %"); k3.metric("Electricity WAPE, fitted", "10.9 %")
u = D["pk"].groupby("scenario")["elec_peak_MW"].sum()
k4.metric("Ten-ward peak U0 → U1", "30 → 78 MW"); k5.metric("CO₂ under U5", "−62 %", "bills −24 %")
st.caption("Both fuels entered calibration. Spatial refits inherited the full-data starting point. Peak values include the modelled residential loads; they are not total network demand.")

tabs = st.tabs(["Calibration", "Demand curves", "Scenarios", "Electrified peak", "Flexibility", "Maps and figures", "Parameters"])

with tabs[0]:
    import plotly.express as px
    c1, c2 = st.columns(2)
    cal = D["cal"]
    c1.plotly_chart(px.scatter(cal, x="gas_mean_kWh", y="sim_gas_mean_kWh", hover_name="LSOA", labels={"gas_mean_kWh": "DESNZ gas per meter [kWh]", "sim_gas_mean_kWh": "EnerMap [kWh]"}, title="Gas per meter, 84 LSOAs (fitted)"), use_container_width=True)
    c2.plotly_chart(px.scatter(cal, x="elec_mean_kWh", y="sim_elec_mean_kWh", hover_name="LSOA", labels={"elec_mean_kWh": "DESNZ electricity per meter [kWh]", "sim_elec_mean_kWh": "EnerMap [kWh]"}, title="Electricity per meter, 84 LSOAs (fitted)"), use_container_width=True)
    st.subheader("Annual LSOA assessment")
    assessment = D["val"].copy()
    assessment["population"] = assessment["population"].str.replace("out-of-fold (7 spatial bands, nb07)", "spatial refits (7 bands; full-data warm start)", regex=False)
    st.dataframe(assessment.round(2), hide_index=True, use_container_width=True)
    st.image(str(FIG / "ubem" / "09_atlas_lsoa_demand.png"), caption="LSOA demand atlas")

with tabs[1]:
    hh = D["hh"]
    st.line_chart(hh["heat_MW"].resample("1D").mean().rename("daily mean space heat [MW]"))
    wk = st.slider("Week of the weather year", 1, 52, 5)
    sl = hh.iloc[(wk - 1) * 168:wk * 168]
    st.line_chart(sl[["p5_MW", "heat_MW", "p95_MW"]])
    st.image(str(FIG / "ubem" / "09_demand_curves.png"), caption="Demand curves from the stochastic households")

with tabs[2]:
    ss = D["ss"]
    c1, c2 = st.columns(2)
    c1.bar_chart(ss.set_index("scenario")["carbon_kt"].rename("ktCO₂e / yr")); c2.bar_chart(ss.set_index("scenario")["bills_MGBP"].rename("bills £M / yr"))
    st.dataframe(ss.round(1), hide_index=True, use_container_width=True)
    scn = st.selectbox("Scenario by ward", list(SCN.values()), index=5)
    st.dataframe(D["sw"][D["sw"].scenario == scn].round(1), hide_index=True, use_container_width=True)
    for f in ["10_scenarios_by_ward.png", "10_low_hanging_fruit.png"]:
        st.image(str(FIG / "ubem" / f))
    for f in ["12_retrofit_gap.png", "12_pies_in_maps.png"]:
        st.image(str(FIG / "planning" / f))

with tabs[3]:
    tw = D["tw"]; wk = st.slider("Week", 1, 52, 3, key="pk")
    st.line_chart(tw.iloc[(wk - 1) * 168:wk * 168], y_label="Electricity demand [MW]")
    pk = D["pk"].pivot(index="ward", columns="scenario", values="elec_peak_MW").round(1)
    st.bar_chart(pk, stack=False)
    st.subheader("Peak by method (U1)"); st.dataframe(D["meth"].round(2), hide_index=True, use_container_width=True)
    for f in ["10_heat_pump_shape.png", "10_peaks_map.png", "10_substation_cells.png"]:
        st.image(str(FIG / "ubem" / f))

with tabs[4]:
    st.dataframe(D["flex"].round(2), hide_index=True, use_container_width=True)
    st.image(str(FIG / "ubem" / "10_flexibility.png"))

with tabs[5]:
    for f in ["12_prioritisation_strategies.png", "12_carbon_x_vulnerability.png"]:
        st.image(str(FIG / "planning" / f))
    st.image(str(HERE / "assets" / "EnerMap_workflow.png"), caption="The modular workflow")

with tabs[6]:
    st.json(D["theta"])
    st.caption("Saved fitted coefficients are shown above. The parameters folder records additional assumptions and sources; editing those files does not rerun this dashboard.")
