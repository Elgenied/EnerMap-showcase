import unittest

import numpy as np
import pandas as pd

from uk_ubem_schedules import (
    HEATING_PATTERNS,
    DwellingSpec,
    SimulationConfig,
    chap_microdata_available,
    generate_dwelling,
    generate_stock,
)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.spec = DwellingSpec(
            dwelling_id="test-home",
            household_archetype="working_couple",
            floor_area_m2=80.0,
            heating_pattern="P6_MORNING_SHORT_EVENING_SUSTAINED",
        )
        self.config = SimulationConfig(
            start="2025-01-01",
            end="2025-01-08",
            timestep_minutes=10,
            master_seed=123,
        )

    def test_schema_and_invariants(self):
        result = generate_dwelling(self.spec, self.config)
        frame = result.frame
        self.assertTrue(frame.index.is_unique)
        self.assertTrue(frame.index.is_monotonic_increasing)
        self.assertEqual(len(frame), 7 * 24 * 6)
        self.assertTrue((frame["occupants_active"] <= frame["occupants_present"]).all())
        self.assertTrue((frame["occupants_present"] <= 2).all())
        component_sum = (
            frame["people_gain_w"]
            + frame["lighting_gain_w"]
            + frame["appliance_gain_w"]
        )
        np.testing.assert_allclose(frame["internal_gain_w"], component_sum)
        self.assertTrue((frame["dhw_litre_per_step"] >= 0).all())

    def test_reproducibility(self):
        a = generate_dwelling(self.spec, self.config).frame
        b = generate_dwelling(self.spec, self.config).frame
        pd.testing.assert_frame_equal(a, b)

        changed = SimulationConfig(
            start=self.config.start,
            end=self.config.end,
            timestep_minutes=10,
            master_seed=124,
        )
        c = generate_dwelling(self.spec, changed).frame
        self.assertFalse(np.array_equal(a["occupants_active"], c["occupants_active"]))

    def test_timer_pattern_is_not_disabled_by_absence(self):
        in_season = SimulationConfig(start=self.config.start, end=self.config.end, timestep_minutes=10, master_seed=123,
                                     heating_season_mode="fixed_dates", heating_season_start=(1, 1), heating_season_end=(12, 31))
        frame = generate_dwelling(self.spec, in_season).frame
        self.assertTrue(((frame["occupants_present"] == 0) & frame["heating_enabled"]).any())

    def test_active_occupancy_mode_is_linked(self):
        spec = DwellingSpec(
            dwelling_id="active-home",
            household_archetype="single_working",
            floor_area_m2=55.0,
            heating_pattern="P9_ACTIVE_OCCUPANCY",
        )
        always_in_season = SimulationConfig(
            start=self.config.start,
            end=self.config.end,
            timestep_minutes=self.config.timestep_minutes,
            master_seed=self.config.master_seed,
            heating_season_mode="fixed_dates",
            heating_season_start=(1, 1),
            heating_season_end=(12, 31),
        )
        frame = generate_dwelling(spec, always_in_season).frame
        expected = frame["occupants_active"].to_numpy() > 0
        np.testing.assert_array_equal(frame["heating_enabled"].to_numpy(), expected)

    def test_stock_order_does_not_change_a_dwelling(self):
        second = DwellingSpec("second", "single_retired", 62.0)
        forward = generate_stock([self.spec, second], self.config)
        reverse = generate_stock([second, self.spec], self.config)
        a = forward[forward["dwelling_id"] == self.spec.dwelling_id]
        b = reverse[reverse["dwelling_id"] == self.spec.dwelling_id]
        pd.testing.assert_frame_equal(a, b)

    def test_uk_dst_uses_unique_utc_index(self):
        dst_config = SimulationConfig(
            start="2025-03-29",
            end="2025-04-01",
            timestep_minutes=60,
            master_seed=3,
        )
        frame = generate_dwelling(self.spec, dst_config).frame
        self.assertEqual(len(frame), 71)  # UK spring transition has a 23-hour day
        self.assertTrue(frame.index.is_unique)

    def test_temporal_chunk_matches_annual_slice_including_dhw(self):
        full_config = SimulationConfig(
            start="2025-01-01",
            end="2025-01-10",
            timestep_minutes=10,
            master_seed=91,
        )
        chunk_config = SimulationConfig(
            start="2025-01-03 12:00",
            end="2025-01-05 12:00",
            timestep_minutes=10,
            master_seed=91,
        )
        full = generate_dwelling(self.spec, full_config).frame
        chunk = generate_dwelling(self.spec, chunk_config).frame
        pd.testing.assert_frame_equal(full.loc[chunk.index], chunk)

    def test_duplicate_stock_ids_are_rejected(self):
        duplicate = DwellingSpec(
            dwelling_id=self.spec.dwelling_id,
            household_archetype="single_retired",
            floor_area_m2=61.0,
        )
        with self.assertRaisesRegex(ValueError, "Duplicate dwelling_id"):
            generate_stock([self.spec, duplicate], self.config)

    def test_5r1c_adapter_exposes_strict_power_switch(self):
        result = generate_dwelling(self.spec, self.config)
        inputs = result.to_5r1c()
        np.testing.assert_array_equal(
            inputs["heating_power_available_fraction"],
            inputs["heating_enabled"].astype(float),
        )

    def test_chap_microdata_is_bundled(self):
        self.assertTrue(chap_microdata_available())

    def test_all_day_and_night_category_is_continuous_in_season(self):
        spec = DwellingSpec("p2", "retired_couple", 70.0, heating_pattern="P2_ALL_DAY_AND_NIGHT")
        config = SimulationConfig(start="2025-01-06", end="2025-01-13", timestep_minutes=10, master_seed=5,
                                  heating_season_mode="fixed_dates", heating_season_start=(1, 1), heating_season_end=(12, 31))
        frame = generate_dwelling(spec, config).frame
        self.assertTrue(frame["heating_enabled"].all())

    def test_alias_of_old_pattern_id(self):
        spec = DwellingSpec("alias", "retired_couple", 70.0, heating_pattern="P2_EVENING_THROUGH_NIGHT")
        result = generate_dwelling(spec, self.config)
        self.assertEqual(result.metadata["sampled_heating_pattern"], "P2_ALL_DAY_AND_NIGHT")

    def test_timer_programme_is_fixed_per_dwelling(self):
        spec = DwellingSpec("timer", "working_couple", 80.0, heating_pattern="P5_TWO_SHORT")
        config = SimulationConfig(start="2025-01-06", end="2025-01-20", timestep_minutes=10, master_seed=7,
                                  heating_season_mode="fixed_dates", heating_season_start=(1, 1), heating_season_end=(12, 31))
        result = generate_dwelling(spec, config)
        prog = result.metadata["sampled_timer_programme"]
        self.assertIsNotNone(prog); self.assertEqual(len(prog["WD"]), 2)
        frame = result.frame
        local = frame.index.tz_convert(config.timezone)
        weekdays = frame[local.dayofweek < 5]
        daily = weekdays.groupby(local[local.dayofweek < 5].date)["heating_enabled"].sum()
        self.assertEqual(daily.nunique(), 1)          # identical weekday timer every day

    def test_crest_tpm_occupancy_invariants_and_size(self):
        spec = DwellingSpec("tpm", "family_children", 95.0)
        frame = generate_dwelling(spec, self.config).frame
        self.assertTrue((frame["occupants_present"] <= 4).all())
        self.assertTrue((frame["occupants_active"] <= frame["occupants_present"]).all())
        self.assertGreater(frame["occupants_active"].mean(), 0.3)

    def test_legacy_modes_still_run(self):
        legacy = SimulationConfig(start=self.config.start, end=self.config.end, timestep_minutes=10, master_seed=11,
                                  timing_source="assumed", setpoint_source="normal", occupancy_mode="person_templates")
        frame = generate_dwelling(self.spec, legacy).frame
        self.assertEqual(len(frame), 7 * 24 * 6)

    def test_invalid_fixed_season_date_is_rejected(self):
        invalid = SimulationConfig(
            start="2025-01-01",
            end="2025-01-02",
            heating_season_start=(2, 30),
        )
        with self.assertRaisesRegex(ValueError, r"valid \(month, day\)"):
            generate_dwelling(self.spec, invalid)


if __name__ == "__main__":
    unittest.main()
