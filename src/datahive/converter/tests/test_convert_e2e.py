import json
import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np

from ..cli import main
from ..resample import grid_length
from .fixtures import annotation_csv, make_dataset

SESSION = "testsession"


class ConvertEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.samples = self.tmp / "samples"
        self.cache = self.tmp / "cache"

    def run_cli(self, *argv):
        return main(list(argv))

    def base(self, dataset, *extra):
        return [
            "convert",
            "--source", str(dataset),
            "--samples", str(self.samples),
            "--cache-dir", str(self.cache),
            "--lab-id", "testlab",
            "--operator-name", "ana",
            "--annotator-name", "ana",
            "--attachment-id", "valve_ball",
            "--strategy", "prehensile",
            "--session-id", SESSION,
            *extra,
        ]

    def write_profile(self):
        code = self.run_cli("profile", "--samples", str(self.samples), "--profile", "so101")
        self.assertEqual(code, 0)
        self.assertTrue((self.samples / "robot_profile.yaml").exists())

    def test_converts_one_episode_and_validates(self):
        dataset = make_dataset(self.tmp / "ds", episode_lengths=(40, 30))
        self.write_profile()
        code = self.run_cli(*self.base(dataset, "--assume-success", "--limit", "1"))
        self.assertEqual(code, 0, "conversion should succeed")

        episode_id = f"{SESSION}_t1"
        h5_path = self.samples / SESSION / "episodes" / f"{episode_id}.h5"
        self.assertTrue(h5_path.exists())

        expected = grid_length(40, 25.0, 100.0)
        with h5py.File(h5_path, "r") as handle:
            self.assertEqual(handle.attrs["schema_version"], "datahive_episode_v1")
            self.assertEqual(handle.attrs["lab_id"], "testlab")
            self.assertEqual(json.loads(handle.attrs["task_ids"]), ["valve_ball"])
            states = handle["observations/robot_states"]
            self.assertEqual(states["joint_position"].shape, (expected, 5))
            self.assertEqual(states["ee_state"].shape, (expected, 1))
            self.assertEqual(states["joint_position"].dtype, np.float64)
            self.assertEqual(states["joint_position"].attrs["provenance"], "estimated")
            self.assertEqual(handle["actions/joint_position"].shape, (expected, 5))
            self.assertEqual(handle["actions/gripper_position"].shape, (expected, 1))
            timestamps = states["timestamp"][:, 0]
            self.assertTrue(np.all(np.diff(timestamps) > 0))
            measured_rate = 1.0 / float(np.median(np.diff(timestamps)))
            self.assertAlmostEqual(measured_rate, 100.0, places=3)
            self.assertIn("episode_annotations/ana", handle)

        trials = self.samples / SESSION / f"trials_{SESSION}.csv"
        self.assertIn("assumed", trials.read_text(encoding="utf-8"))

        for camera in ("wrist", "external"):
            video = self.samples / SESSION / "episodes" / f"{episode_id}_cam_{camera}.mp4"
            self.assertTrue(video.exists(), video)

    def test_refuses_without_annotations_or_assumption(self):
        dataset = make_dataset(self.tmp / "ds", episode_lengths=(40,))
        self.write_profile()
        code = self.run_cli(*self.base(dataset, "--limit", "1"))
        self.assertEqual(code, 2)
        self.assertFalse((self.samples / SESSION).exists(), "nothing may be written on refusal")

    def test_accepts_a_sidecar_csv(self):
        dataset = make_dataset(self.tmp / "ds", episode_lengths=(40,))
        csv_path = annotation_csv(
            self.tmp / "labels.csv",
            [{"episode_index": 0, "outcome": "fail", "strategy": "prehensile",
              "failure_cause": "slip", "severity": "minor"}],
        )
        self.write_profile()
        code = self.run_cli(*self.base(dataset, "--annotations", str(csv_path), "--limit", "1"))
        self.assertEqual(code, 0)
        trials = (self.samples / SESSION / f"trials_{SESSION}.csv").read_text(encoding="utf-8")
        self.assertIn("fail", trials)
        self.assertIn("slip", trials)
        self.assertNotIn("assumed", trials)

    def test_annotations_template_then_partial_fill(self):
        dataset = make_dataset(self.tmp / "ds", episode_lengths=(40, 30))
        self.write_profile()
        template = self.tmp / "labels.csv"
        self.assertEqual(
            self.run_cli(
                "annotations-template", "--source", str(dataset),
                "--cache-dir", str(self.cache), "--output", str(template),
            ),
            0,
        )
        lines = template.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3, "header plus one row per source episode")

        # Fill only the first row; the unfilled second row must not block --limit 1.
        fields = lines[1].split(",")
        fields[1] = "success"
        lines[1] = ",".join(fields)
        template.write_text("\n".join(lines) + "\n", encoding="utf-8")

        code = self.run_cli(*self.base(dataset, "--annotations", str(template), "--limit", "1"))
        self.assertEqual(code, 0)

    def test_short_episode_is_skipped_not_failed(self):
        dataset = make_dataset(self.tmp / "ds", episode_lengths=(10,))
        self.write_profile()
        code = self.run_cli(*self.base(dataset, "--assume-success", "--limit", "1"))
        self.assertEqual(code, 0, "a skip is not a failure")
        self.assertFalse((self.samples / SESSION / "episodes" / f"{SESSION}_t1.h5").exists())

    def test_second_run_is_idempotent(self):
        dataset = make_dataset(self.tmp / "ds", episode_lengths=(40,))
        self.write_profile()
        args = self.base(dataset, "--assume-success", "--limit", "1")
        self.assertEqual(self.run_cli(*args), 0)
        h5_path = self.samples / SESSION / "episodes" / f"{SESSION}_t1.h5"
        before = h5_path.stat().st_mtime_ns
        self.assertEqual(self.run_cli(*args), 0)
        self.assertEqual(h5_path.stat().st_mtime_ns, before, "existing episode should not be rewritten")

    def test_composed_attachment_requires_stage_reached(self):
        dataset = make_dataset(self.tmp / "ds", episode_lengths=(40,))
        self.write_profile()
        args = self.base(dataset, "--assume-success", "--limit", "1")
        args[args.index("valve_ball")] = "button"
        self.assertEqual(self.run_cli(*args), 2)

    def test_low_rate_is_refused_without_opt_in(self):
        dataset = make_dataset(self.tmp / "ds", episode_lengths=(40,))
        self.write_profile()
        args = self.base(dataset, "--assume-success", "--limit", "1", "--resample-hz", "25")
        self.assertEqual(self.run_cli(*args), 2)

    def test_camera_map_override(self):
        dataset = make_dataset(self.tmp / "ds", episode_lengths=(40,))
        self.write_profile()
        args = self.base(dataset, "--assume-success", "--limit", "1")
        # Remap the wrist camera to the external source key; both still resolve.
        args += ["--camera-map", "wrist=observation.images.external"]
        self.assertEqual(self.run_cli(*args), 0)

    def test_degrees_are_converted_to_radians(self):
        from ..sources.lerobot_v3 import LeRobotV3Source

        dataset = make_dataset(self.tmp / "ds", episode_lengths=(40,))
        self.write_profile()  # so101 declares source_joint_units: degrees
        code = self.run_cli(
            *self.base(
                dataset,
                "--assume-success",
                "--limit",
                "1",
                "--resample-hz",
                "25",
                "--allow-low-rate",
                "--no-validate",
            )
        )
        self.assertEqual(code, 0, "conversion should succeed")

        source = LeRobotV3Source(dataset).read_episode(0)
        h5_path = self.samples / SESSION / "episodes" / f"{SESSION}_t1.h5"
        with h5py.File(h5_path, "r") as handle:
            np.testing.assert_allclose(
                handle["observations/robot_states/joint_position"][:],
                np.deg2rad(source.state[:, :-1]),
                rtol=1e-12,
                atol=1e-12,
            )
            np.testing.assert_allclose(
                handle["actions/joint_position"][:],
                np.deg2rad(source.action[:, :-1]),
                rtol=1e-12,
                atol=1e-12,
            )
            # The gripper is not rescaled.
            np.testing.assert_allclose(
                handle["observations/robot_states/ee_state"][:],
                source.state[:, -1:],
                rtol=1e-12,
                atol=1e-12,
            )


if __name__ == "__main__":
    unittest.main()
