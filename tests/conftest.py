from __future__ import annotations

import socket
from datetime import date
from pathlib import Path

import h5py
import numpy as np
import pytest

from datahive.config import Config, save_config
from datahive.profile import RobotProfile, write_profile_skeleton


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Make any real network call fail loudly. All Hub interaction in tests
    must go through a FakeHfApi injected into datahive.hub.Hub."""

    def guard(*args, **kwargs):
        raise RuntimeError("Network access is not allowed in tests")

    monkeypatch.setattr(socket.socket, "connect", guard)
    yield


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Redirect config_home() (and $HOME) into a tmp dir so tests never
    touch a real ~/.datahive."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("DATAHIVE_CONFIG_HOME", str(fake_home / ".datahive"))
    monkeypatch.setenv("HOME", str(fake_home))
    yield fake_home


@pytest.fixture
def samples_root(tmp_path) -> Path:
    root = tmp_path / "samples"
    root.mkdir()
    return root


@pytest.fixture
def filled_profile(samples_root) -> RobotProfile:
    path = write_profile_skeleton(samples_root)
    import yaml

    data = yaml.safe_load(path.read_text())
    data["manipulator"] = {"model": "TestArm", "dof": 6, "joint_names": ["j1", "j2", "j3", "j4", "j5", "j6"]}
    data["end_effector"] = {"type": "gripper", "actuated_dof": 1, "command_modality": "position"}
    data["low_level"] = {"mode": "stock", "controller_type": "pid", "rate_hz": 500, "gains": None}
    data["control_mode"] = "joint_position"
    data["policy"] = "teleop_spacemouse"
    data["cameras"] = [{"name": "external", "resolution": "1280x720", "encoding": "h264", "fps": 30, "position": "front", "orientation": "level"}]
    data["board_mounting"] = "horizontal"
    data["hiveboard_version"] = "v2"
    data["platform_id"] = "rig-01"
    data["robot_name"] = "test_arm"
    data["gripper_name"] = "test_gripper"
    data["is_biarm"] = False
    data["uses_mobile_base"] = False
    data["control_freq"] = 100
    data["action_space"] = ["joint_position", "gripper_binary"]
    data["action_joint_names"] = ["j0", "j1", "j2", "j3", "j4", "j5"]
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    from datahive.profile import load_profile

    return load_profile(samples_root)


@pytest.fixture
def config(tmp_path) -> Config:
    cfg = Config(
        lab_id="lab_test",
        repo_id="sua-org/lab_test",
        hf_token="hf_faketokenfaketokenfaketoken1234",
        created_at="2026-01-01T00:00:00+00:00",
    )
    # Save outside of any git repo: tmp_path is not a git repo by default.
    save_config(cfg)
    return cfg


class FakeHfApi:
    """Records every call instead of hitting the network."""

    def __init__(self):
        self.uploaded_files: dict[str, bytes] = {}
        self.upload_calls: list[dict] = []
        self.delete_calls: list[list[str]] = []
        self.whoami_calls = 0
        self.whoami_should_fail = False

    def whoami(self, token=None):
        self.whoami_calls += 1
        if self.whoami_should_fail:
            raise RuntimeError("bad token")
        return {"name": "lab_test"}

    def upload_file(self, *, path_or_fileobj, path_in_repo, repo_id, repo_type, commit_message):
        if isinstance(path_or_fileobj, (str, Path)):
            content = Path(path_or_fileobj).read_bytes()
        elif isinstance(path_or_fileobj, bytes):
            content = path_or_fileobj
        else:
            content = path_or_fileobj.read()
        self.uploaded_files[path_in_repo] = content
        self.upload_calls.append({"path_in_repo": path_in_repo, "repo_id": repo_id})
        return f"https://fake/{repo_id}/{path_in_repo}"

    def create_commit(self, *, repo_id, repo_type, operations, commit_message):
        paths = []
        for op in operations:
            paths.append(op.path_in_repo)
            self.uploaded_files.pop(op.path_in_repo, None)
        self.delete_calls.append(paths)
        return f"commit-{len(self.delete_calls)}"

    def list_repo_files(self, *, repo_id, repo_type):
        return list(self.uploaded_files.keys())


@pytest.fixture
def fake_hub(config):
    from datahive.hub import Hub

    api = FakeHfApi()
    hub = Hub(config, api=api)
    hub._fake_api = api
    return hub


def write_test_video(path: Path, *, seconds: float, width: int = 1280, height: int = 720, fps: float = 30.0) -> None:
    """Writes a tiny real mp4 (falls back to a stub when OpenCV is missing)."""
    try:
        import cv2
    except ImportError:
        path.write_bytes(b"\x00" * 128)
        return
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    for _ in range(max(1, round(seconds * fps))):
        out.write(frame)
    out.release()


def make_episode(
    samples_root: Path,
    session_id: str,
    episode_id: str,
    *,
    trial_id: str = "trial-1",
    lab_id: str = "lab_test",
    profile: RobotProfile | None = None,
    n_points: int = 200,
    rate_hz: float = 100.0,
    with_video: bool = True,
    n_joints: int = 6,
) -> Path:
    """Builds a fake, valid episode: .h5 header + streams + a stub .mp4."""
    from datahive.episode import EpisodeWriter

    edir = samples_root / session_id / "episodes"
    edir.mkdir(parents=True, exist_ok=True)

    writer = EpisodeWriter(
        samples_root, session_id, episode_id,
        trial_id=trial_id, task_ids=["insert_peg"], lab_id=lab_id, profile=profile,
    )
    ts = np.arange(n_points) / rate_hz
    for t in ts:
        writer.append_proprioception(
            timestamp=float(t),
            joint_position=np.random.randn(n_joints),
            joint_velocity=np.random.randn(n_joints),
            joint_torque_or_current=np.random.randn(n_joints),
            ee_pose=np.random.randn(7),
            ee_state=np.array([1.0]),
            provenance={"joint_position": "measured", "joint_velocity": "measured",
                        "joint_torque_or_current": "measured", "ee_pose": "estimated", "ee_state": "measured"},
        )
        writer.append_command(timestamp=float(t), target=np.random.randn(n_joints), control_mode="joint_position")
    if with_video:
        stub = samples_root / f"_stub_{episode_id}.mp4"
        write_test_video(stub, seconds=(n_points - 1) / rate_hz)
        writer.attach_video("external", stub, move=True)
    writer.close()
    return writer.paths.h5


def write_valid_annotation(samples_root: Path, session_id: str, trial_id: str, *, outcome="success", **overrides):
    from datahive.trials import upsert_row
    from datahive.paths import trials_csv_path
    from datahive.schema import TrialAnnotation

    data = dict(
        trial_id=trial_id, lab_id="lab_test", platform_id="rig-01", attachment_id="valve_ball",
        date=date.today().isoformat(), outcome=outcome, strategy="prehensile",
        operator_name="op", annotator_name="ann",
    )
    if outcome == "success":
        data["completion_time_s"] = 1.5
    else:
        data["failure_cause"] = "slip"
    composed_assembly = overrides.pop("composed_assembly", False)
    data.update(overrides)
    ann = TrialAnnotation.model_validate(data, context={"composed_assembly": composed_assembly})
    upsert_row(trials_csv_path(samples_root, session_id), ann)
    return ann
