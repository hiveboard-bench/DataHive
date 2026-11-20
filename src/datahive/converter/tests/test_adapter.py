import tempfile
import unittest
from pathlib import Path

import numpy as np

from ..sources.lerobot_v3 import LeRobotV3Source
from .fixtures import make_dataset


class LeRobotAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.root = make_dataset(cls.tmp / "ds", episode_lengths=(40, 30))
        cls.source = LeRobotV3Source(cls.root)

    def test_info_and_camera_map(self):
        self.assertEqual(self.source.info.fps, 25.0)
        self.assertEqual(self.source.info.total_episodes, 2)
        self.assertEqual(
            self.source.camera_map,
            {
                "wrist": "observation.images.wrist",
                "external": "observation.images.external",
            },
        )

    def test_joint_names_strip_pos_and_keep_gripper_last(self):
        episode = self.source.read_episode(0)
        self.assertEqual(episode.joint_names[-1], "gripper")
        self.assertEqual(episode.joint_names[0], "shoulder_pan")
        self.assertEqual(len(episode.joint_names), 6)

    def test_episode_arrays(self):
        episode = self.source.read_episode(0)
        self.assertEqual(episode.state.shape, (40, 6))
        self.assertEqual(episode.action.shape, (40, 6))
        self.assertTrue(np.isfinite(episode.state).all())

    def test_segment_offsets_accumulate_within_shard(self):
        first = self.source.episode_meta(0).videos["wrist"]
        second = self.source.episode_meta(1).videos["wrist"]
        self.assertEqual(first.start_frame, 0)
        self.assertEqual(first.frame_count, 40)
        self.assertEqual(second.start_frame, 40)
        self.assertEqual(second.frame_count, 30)

    def test_segments_do_not_cross_shards_in_fixture(self):
        for index in (0, 1):
            for segment in self.source.episode_meta(index).videos.values():
                self.assertTrue(segment.path.exists(), segment.path)

    def test_unknown_camera_key_reports_available(self):
        with self.assertRaisesRegex(KeyError, "not a video feature"):
            LeRobotV3Source(self.root, camera_map={"top": "observation.images.top"}).episode_meta(0)

    def test_probe_reports_real_columns(self):
        payload = self.source.probe()
        self.assertIn("videos/observation.images.wrist/from_timestamp", payload["episodes_columns"])
        self.assertEqual(payload["tasks"][0], "Open the cover and press the button")


if __name__ == "__main__":
    unittest.main()
