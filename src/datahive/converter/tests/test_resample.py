import unittest

import numpy as np

from ..resample import grid_length, provenance, sample_times, to_grid


class ResampleTests(unittest.TestCase):
    def test_grid_length_matches_wall_clock(self):
        # 263 frames at 25 Hz span 10.48 s; at 100 Hz that is 1049 samples.
        self.assertEqual(grid_length(263, 25.0, 100.0), 1049)
        self.assertEqual(grid_length(2, 25.0, 100.0), 5)
        self.assertEqual(grid_length(1, 25.0, 100.0), 1)

    def test_endpoints_preserved_and_grid_uniform(self):
        values = np.array([[0.0, 10.0], [1.0, 20.0], [2.0, 30.0]])
        out = to_grid(values, 2.0, 8.0)
        self.assertEqual(out.shape[0], grid_length(3, 2.0, 8.0))
        np.testing.assert_allclose(out[0], values[0])
        np.testing.assert_allclose(out[-1], values[-1])
        np.testing.assert_allclose(out[1], [0.25, 12.5])

    def test_linear_interpolation_is_monotonic(self):
        values = np.arange(6, dtype=np.float64).reshape(-1, 1)
        out = to_grid(values, 5.0, 100.0)
        self.assertTrue(np.all(np.diff(out[:, 0]) > 0))
        np.testing.assert_allclose(sample_times(3, 2.0), [0.0, 0.5, 1.0])

    def test_same_rate_is_identity(self):
        values = np.arange(12, dtype=np.float64).reshape(3, 4)
        np.testing.assert_allclose(to_grid(values, 50.0, 50.0), values)

    def test_provenance_marks_upsampled_data_estimated(self):
        self.assertEqual(provenance(25.0, 100.0), "estimated")
        self.assertEqual(provenance(100.0, 100.0), "measured")


if __name__ == "__main__":
    unittest.main()
