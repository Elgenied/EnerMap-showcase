"""Stratified (quantile) draws of the heating regime and thermostat setting - v1.2.0."""
import unittest

import numpy as np

from uk_ubem_schedules import (
    HEATING_PATTERNS,
    DwellingSpec,
    SimulationConfig,
    _microdata,
    _sample_heating_pattern,
    _sample_setpoint,
    chap_microdata_available,
    generate_dwelling,
)


@unittest.skipUnless(chap_microdata_available(), "CHAP microdata not bundled")
class StratifiedDrawTests(unittest.TestCase):
    def setUp(self):
        self.config = SimulationConfig(start="2025-01-01", end="2025-01-03", timestep_minutes=60, master_seed=7)

    def _spec(self, did, qp=None, qs=None):
        return DwellingSpec(dwelling_id=did, household_archetype="working_couple", floor_area_m2=80.0,
                            heating_pattern_quantile=qp, setpoint_quantile=qs)

    def test_quantiles_are_validated(self):
        with self.assertRaises(ValueError):
            self._spec("bad", qp=1.0).validate()
        with self.assertRaises(ValueError):
            self._spec("bad", qs=-0.1).validate()
        self._spec("ok", qp=0.0, qs=0.999).validate()

    def test_pattern_quantile_inverts_the_share_cdf(self):
        rng = np.random.default_rng(0)
        _, meta = _microdata()
        cands = list(HEATING_PATTERNS.values())
        shares = np.array([meta["categories"][str(p.efus_category)]["relative_share"] for p in cands]); shares /= shares.sum()
        cdf = np.cumsum(shares)
        first = _sample_heating_pattern(self._spec("a", qp=0.0), self.config, rng)
        last = _sample_heating_pattern(self._spec("b", qp=0.9999), self.config, rng)
        self.assertEqual(first.pattern_id, cands[0].pattern_id)
        self.assertEqual(last.pattern_id, cands[-1].pattern_id)
        # a fine grid of quantiles reproduces the shares
        grid = (np.arange(2000) + 0.5) / 2000
        ids = [_sample_heating_pattern(self._spec("g", qp=float(u)), self.config, rng).pattern_id for u in grid]
        freq = np.array([ids.count(p.pattern_id) / len(ids) for p in cands])
        np.testing.assert_allclose(freq, shares, atol=0.002)
        self.assertTrue(np.all(np.diff(cdf) >= 0))

    def test_setpoint_quantile_is_monotone_and_unbiased(self):
        rng = np.random.default_rng(1)
        pattern = HEATING_PATTERNS["P1_ALL_DAY_FROM_WAKE"]
        arrays, _ = _microdata()
        p = np.asarray(arrays[f"temp|{pattern.efus_category}"], dtype=float)
        mean_true = float(np.sum(np.clip(np.arange(len(p)), *self.config.setpoint_clip_c) * p))   # the clip applies to both sampling paths
        grid = (np.arange(4000) + 0.5) / 4000
        sp = np.array([_sample_setpoint(pattern, self.config, rng, quantile=float(u)) for u in grid])
        self.assertTrue(np.all(np.diff(sp) >= -1e-9))                     # inverse CDF is non-decreasing
        self.assertLess(abs(sp.mean() - mean_true), 0.05)                  # the quantile grid reproduces the mean (up to the 12-27 C clip)
        random = np.array([_sample_setpoint(pattern, self.config, rng) for _ in range(4000)])
        self.assertLess(abs(random.mean() - sp.mean()), 0.15)

    def test_stratified_means_have_less_spread_than_random(self):
        # per-dwelling mean setpoint over K=3 draws: stratified (quantiles (perm[k] + u)/3) vs random
        K, n = 3, 400
        pattern = HEATING_PATTERNS["P1_ALL_DAY_FROM_WAKE"]
        strat, rand = [], []
        for i in range(n):
            r = np.random.default_rng(100 + i)
            perm = r.permutation(K)
            strat.append(np.mean([_sample_setpoint(pattern, self.config, r, quantile=float((perm[k] + r.random()) / K)) for k in range(K)]))
            rand.append(np.mean([_sample_setpoint(pattern, self.config, r) for _ in range(K)]))
        self.assertLess(np.std(strat), 0.6 * np.std(rand))
        self.assertLess(abs(np.mean(strat) - np.mean(rand)), 0.15)

    def test_generate_dwelling_records_quantiles(self):
        res = generate_dwelling(self._spec("q", qp=0.0, qs=0.02), self.config)
        self.assertEqual(res.metadata["sampled_quantiles"], {"heating_pattern": 0.0, "setpoint": 0.02})
        lo = res.metadata["sampled_comfort_setpoint_c"]
        hi = generate_dwelling(self._spec("q", qp=0.0, qs=0.98), self.config).metadata["sampled_comfort_setpoint_c"]
        self.assertLess(lo, hi)


if __name__ == "__main__":
    unittest.main()
