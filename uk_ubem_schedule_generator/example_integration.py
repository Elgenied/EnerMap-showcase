"""Minimal integration example for an existing 5R1C demand engine."""

from uk_ubem_schedules import DwellingSpec, SimulationConfig, generate_dwelling


spec = DwellingSpec(
    dwelling_id="EPC_0001",
    household_archetype="family_children",
    floor_area_m2=96.0,
    physical_archetype_id="UK_SEMI_1945_1964",
)
config = SimulationConfig.for_year(
    2025,
    timestep_minutes=10,
    master_seed=42,
    heating_control_mode="efus_mixed",
)

result = generate_dwelling(spec, config)
inputs = result.to_5r1c()

# Pass these arrays alongside weather and EPC-derived physical parameters:
theta_set_heating_c = inputs["theta_set_heating_c"]
heating_enabled = inputs["heating_enabled"]
heating_power_available_fraction = inputs["heating_power_available_fraction"]
phi_internal_w = inputs["phi_internal_w"]

# Pseudocode: adapt names to your engine.
# demand = engine.run(
#     theta_external_c=weather.dry_bulb_c,
#     solar_irradiance_w_m2=weather.solar_w_m2,
#     theta_set_heating_c=theta_set_heating_c,
#     heating_enabled=heating_enabled,
#     heating_power_limit_w=design_heating_power_w * heating_power_available_fraction,
#     phi_internal_w=phi_internal_w,
#     physical_archetype=epc_archetype,
# )

print(result.metadata)
print(result.frame.head())
