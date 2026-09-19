from __future__ import annotations

import yaml
import pytest

from datahive.errors import ProfileIncomplete, ProfileMissing
from datahive.episode import EpisodeWriter, read_header
from datahive.paths import profile_path
from datahive.profile import camera_consistency_problems, load_profile, write_profile_skeleton

from conftest import make_episode


def test_new_profile_writes_skeleton(samples_root):
    path = write_profile_skeleton(samples_root)
    assert path == profile_path(samples_root)
    data = yaml.safe_load(path.read_text())
    assert data["low_level"]["mode"] is None
    assert data["cameras"] == []
    assert data["manipulator"]["joint_names"] == []


def test_new_profile_refuses_to_clobber_without_force(samples_root):
    write_profile_skeleton(samples_root)
    with pytest.raises(FileExistsError):
        write_profile_skeleton(samples_root)
    write_profile_skeleton(samples_root, force=True)  # should not raise


def test_load_profile_missing_raises(samples_root):
    with pytest.raises(ProfileMissing):
        load_profile(samples_root)


def test_load_profile_incomplete_low_level_mode(samples_root):
    write_profile_skeleton(samples_root)
    with pytest.raises(ProfileIncomplete, match="low_level.mode"):
        load_profile(samples_root)


def test_joint_names_are_optional_without_a_joint_action(samples_root):
    path = write_profile_skeleton(samples_root)
    data = yaml.safe_load(path.read_text())
    data.update(platform_id="rig-01", policy="teleop", robot_name="arm", gripper_name="g", is_biarm=False,
                uses_mobile_base=False, control_freq=100, action_space=["cartesian_velocity", "gripper_binary"],
                robot_state_orientation_representation="quat")
    data["end_effector"] = {"type": "gripper", "actuated_dof": 1, "command_modality": "position"}
    data["low_level"]["mode"] = "stock"
    data["cameras"] = [{"name": "external"}]
    path.write_text(yaml.safe_dump(data))
    assert load_profile(samples_root).manipulator.get("joint_names") in ([], None)


def test_camera_facts_are_not_typed_into_the_profile():
    from datahive.profile import incompleteness_problems
    assert incompleteness_problems(_complete(cameras=[{"name": "external"}])) == []


def test_load_profile_incomplete_cameras(samples_root):
    path = write_profile_skeleton(samples_root)
    data = yaml.safe_load(path.read_text())
    data["low_level"]["mode"] = "stock"
    data["manipulator"]["joint_names"] = ["j1"]
    data["platform_id"] = "rig-01"
    data["robot_name"] = "test_arm"
    data["gripper_name"] = "test_gripper"
    data["is_biarm"] = False
    data["uses_mobile_base"] = False
    data["control_freq"] = 100
    data["action_space"] = ["joint_position", "gripper_binary"]
    data["action_joint_names"] = ["j0", "j1", "j2", "j3", "j4", "j5"]
    data["policy"] = "teleop_spacemouse"
    data["end_effector"] = {"type": "gripper", "actuated_dof": 1, "command_modality": "position"}
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ProfileIncomplete, match="cameras"):
        load_profile(samples_root)


def test_camera_consistency_noop_for_fewer_than_two_cameras():
    assert camera_consistency_problems([]) == []
    assert camera_consistency_problems([{"name": "external", "resolution": "1280x720", "encoding": "h264", "fps": 30}]) == []


def test_camera_consistency_detects_mismatched_resolution():
    cameras = [
        {"name": "external", "resolution": "1280x720", "encoding": "h264", "fps": 30},
        {"name": "wrist", "resolution": "640x480", "fps": 30},
    ]
    problems = camera_consistency_problems(cameras)
    assert len(problems) == 1
    assert "resolution" in problems[0]


def test_camera_consistency_detects_mismatched_fps():
    cameras = [
        {"name": "external", "resolution": "1280x720", "encoding": "h264", "fps": 30},
        {"name": "wrist", "resolution": "1280x720", "fps": 60},
    ]
    problems = camera_consistency_problems(cameras)
    assert len(problems) == 1
    assert "fps" in problems[0]


def test_camera_consistency_passes_when_matching():
    cameras = [
        {"name": "external", "resolution": "1280x720", "encoding": "h264", "fps": 30},
        {"name": "wrist", "resolution": "1280x720", "encoding": "h264", "fps": 30},
        {"name": "overhead", "resolution": "1280x720", "encoding": "h264", "fps": 30},
    ]
    assert camera_consistency_problems(cameras) == []


def test_load_profile_rejects_mismatched_cameras(samples_root):
    path = write_profile_skeleton(samples_root)
    data = yaml.safe_load(path.read_text())
    data["low_level"]["mode"] = "stock"
    data["manipulator"]["joint_names"] = ["j1"]
    data["platform_id"] = "rig-01"
    data["robot_name"] = "test_arm"
    data["gripper_name"] = "test_gripper"
    data["is_biarm"] = False
    data["uses_mobile_base"] = False
    data["control_freq"] = 100
    data["action_space"] = ["joint_position", "gripper_binary"]
    data["action_joint_names"] = ["j0", "j1", "j2", "j3", "j4", "j5"]
    data["policy"] = "teleop_spacemouse"
    data["end_effector"] = {"type": "gripper", "actuated_dof": 1, "command_modality": "position"}
    data["cameras"] = [
        {"name": "external", "resolution": "1280x720", "encoding": "h264", "fps": 30},
        {"name": "wrist", "resolution": "640x480", "fps": 30},
    ]
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ProfileIncomplete, match="resolution"):
        load_profile(samples_root)


def test_episode_header_merges_profile_and_episode_fields(samples_root, filled_profile):
    h5_path = make_episode(samples_root, "sess1", "ep1", profile=filled_profile)
    header = read_header(h5_path)
    assert header.manipulator["model"] == "TestArm"
    assert header.low_level["mode"] == "stock"
    assert header.episode_id == "ep1"
    assert header.session_id == "sess1"
    assert header.trial_id == "trial-1"
    assert header.cameras[0]["name"] == "external"
    # attach_video() must write the actual filename back onto the matching
    # camera spec -- both in memory and in the persisted header attrs --
    # so the header stays self-describing (and validate.py's "referenced
    # video file exists" check has something real to check).
    assert header.cameras[0]["file"] == "ep1_cam_external.mp4"


def test_episode_keeps_profile_snapshot_after_profile_edited(samples_root, filled_profile):
    h5_path = make_episode(samples_root, "sess1", "ep1", profile=filled_profile)
    header_before = read_header(h5_path)
    assert header_before.manipulator["model"] == "TestArm"

    # Edit robot_profile.yaml after the fact -- the existing episode's
    # header must stay a byte-identical snapshot.
    path = profile_path(samples_root)
    data = yaml.safe_load(path.read_text())
    data["manipulator"]["model"] = "NewArmV2"
    path.write_text(yaml.safe_dump(data))

    header_after = read_header(h5_path)
    assert header_after.manipulator["model"] == "TestArm"

    # A newly written episode picks up the change.
    new_profile = load_profile(samples_root)
    h5_path2 = make_episode(samples_root, "sess1", "ep2", profile=new_profile)
    header2 = read_header(h5_path2)
    assert header2.manipulator["model"] == "NewArmV2"


def _complete(**overrides):
    data = {
        "platform_id": "rig-01",
        "policy": "teleop_spacemouse",
        "robot_name": "test_arm", "gripper_name": "g", "is_biarm": False, "uses_mobile_base": False, "control_freq": 100, "action_space": ["joint_position", "gripper_binary"], "action_joint_names": ["j0", "j1", "j2", "j3", "j4", "j5"],
        "manipulator": {"model": "A", "joint_names": ["j1", "j2"]},
        "end_effector": {"type": "gripper", "actuated_dof": 1, "command_modality": "position"},
        "low_level": {"mode": "stock"},
        "cameras": [{"name": "external", "resolution": "1280x720", "encoding": "h264", "fps": 30}],
    }
    data.update(overrides)
    return data


def test_complete_profile_has_no_problems():
    from datahive.profile import incompleteness_problems
    assert incompleteness_problems(_complete()) == []


def test_platform_id_is_not_asked_for_and_is_derived_from_robot_and_gripper_names(samples_root):
    from datahive.profile import derive_platform_id, incompleteness_problems, load_profile, read_raw_profile, save_profile
    data = _complete(robot_name="Franka Panda", gripper_name="Robotiq 2F-85")
    data.pop("platform_id")
    assert incompleteness_problems(data) == []                                   # not required any more
    assert derive_platform_id("Franka Panda", "Robotiq 2F-85") == "franka_panda_robotiq_2f_85"
    assert derive_platform_id(None, None) == ""
    save_profile(samples_root, data)
    assert read_raw_profile(samples_root)["platform_id"] == "franka_panda_robotiq_2f_85"
    assert load_profile(samples_root).platform_id == "franka_panda_robotiq_2f_85"


def test_platform_field_is_gone_from_the_profile_form(samples_root):
    from fastapi.testclient import TestClient
    from datahive.interface.app import create_app
    js = TestClient(create_app(samples_root)).get("/app.js").text
    assert 'fieldLabel("Platform ID")' not in js and 'name="platform_id"' not in js and '["Platform ID"' not in js
    assert 'fieldLabel("Policy name")' in js and 'fieldLabel("Robot name")' in js and 'fieldLabel("Gripper name")' in js


def test_camera_rules_are_enforced():
    from datahive.profile import incompleteness_problems
    cams = [
        {"name": "Ext", "resolution": "1280x720", "encoding": "h264", "fps": 30},
        {"name": "ext", "resolution": "big", "fps": 0},
        {"resolution": "1280x720", "encoding": "h264", "fps": 30},
    ]
    text = "\n".join(incompleteness_problems(_complete(cameras=cams)))
    assert "used twice" in text
    assert "has no name" in text


def test_duplicate_joint_names_rejected():
    from datahive.profile import incompleteness_problems
    problems = incompleteness_problems(_complete(manipulator={"joint_names": ["a", "A"]}))
    assert any("duplicates" in p for p in problems)


def test_save_normalizes_resolution_and_derives_dof(samples_root):
    from datahive.profile import read_raw_profile, save_profile
    save_profile(samples_root, _complete(
        manipulator={"model": "A", "dof": 99, "joint_names": [" j1 ", "j2", "j3"]},
        cameras=[{"name": " ext ", "resolution": "1280 X 720", "encoding": "h264", "fps": "30.0"}],
    ))
    raw = read_raw_profile(samples_root)
    assert raw["manipulator"]["dof"] == 3
    assert raw["manipulator"]["joint_names"] == ["j1", "j2", "j3"]
    assert raw["cameras"][0] == {"name": "ext", "resolution": "1280x720", "encoding": "h264", "fps": 30}


def test_episode_policy_overrides_profile_default(samples_root, filled_profile):
    h5 = make_episode(samples_root, "s", "ep1", profile=filled_profile)
    assert read_header(h5).policy == "teleop_spacemouse"
    from datahive.episode import EpisodeWriter
    with EpisodeWriter(samples_root, "s", "ep2", trial_id="t2", lab_id="lab_test",
                       profile=filled_profile, policy="vla_pi0") as w:
        assert w.header.policy == "vla_pi0"


def test_profile_form_groups_and_progress(samples_root):
    from fastapi.testclient import TestClient
    from datahive.interface.app import create_app
    js = TestClient(create_app(samples_root)).get("/app.js").text
    for marker in ("Required to record", "Recommended", "profileProgress", "dofReadout"):
        assert marker in js
    assert 'name="manipulator.dof"' not in js


def test_custom_low_level_requires_a_description():
    from datahive.profile import incompleteness_problems
    custom = _complete(low_level={"mode": "custom"})
    assert any("custom_description" in p for p in incompleteness_problems(custom))
    described = _complete(low_level={"mode": "custom", "custom_description": "1 kHz torque loop, gravity comp."})
    assert incompleteness_problems(described) == []
    assert incompleteness_problems(_complete()) == []


def test_end_effector_is_required():
    from datahive.profile import incompleteness_problems
    text = "\n".join(incompleteness_problems(_complete(end_effector={})))
    assert "end_effector.type" in text and "actuated_dof" in text and "command_modality" in text


def test_custom_field_is_shown_only_for_custom_mode(samples_root):
    from fastapi.testclient import TestClient
    from datahive.interface.app import create_app
    client = TestClient(create_app(samples_root))
    js = client.get("/app.js").text
    assert 'id="customControllerField" ${ll.mode === "custom" ? "" : "hidden"}' in js
    assert "customField.hidden = !isCustom" in js
    # .field-grid label sets display:flex, so [hidden] must be forced or the field is always visible.
    assert "[hidden] { display: none !important; }" in client.get("/style.css").text


def test_policy_is_required():
    from datahive.profile import incompleteness_problems
    assert any(p.startswith("policy") for p in incompleteness_problems(_complete(policy="")))


def test_form_has_no_optional_tier(samples_root):
    from fastapi.testclient import TestClient
    from datahive.interface.app import create_app
    js = TestClient(create_app(samples_root)).get("/app.js").text
    assert 'profileGroupTitle("Optional"' not in js


def test_action_space_is_required_and_follows_oopsie_rules():
    from datahive.profile import incompleteness_problems
    def text(**kw):
        return "\n".join(incompleteness_problems(_complete(**kw)))
    assert "action_space is empty" in text(action_space=[])
    assert "at least one gripper action" in text(action_space=["joint_position"])
    assert "at least one arm action" in text(action_space=["gripper_binary"])
    assert "unknown action" in text(action_space=["joint_position", "gripper_binary", "teleport"])
    assert "at most one base action" in text(
        action_space=["joint_position", "gripper_binary", "base_velocity", "base_position"])
    assert "must include a base action" in text(uses_mobile_base=True)
    assert text(uses_mobile_base=True, action_space=["cartesian_velocity", "gripper_position", "base_velocity"], robot_state_orientation_representation="quat") == ""


def test_action_space_is_snapshotted_into_the_episode(samples_root, filled_profile):
    h5 = make_episode(samples_root, "s", "ep1", profile=filled_profile)
    assert read_header(h5).action_space == ["joint_position", "gripper_binary"]


def test_action_space_saved_from_form_payload(samples_root):
    from datahive.profile import read_raw_profile, save_profile
    save_profile(samples_root, _complete(action_space=["joint_position", " joint_position", "gripper_binary"]))
    assert read_raw_profile(samples_root)["action_space"] == ["joint_position", "gripper_binary"]


def test_form_has_action_space_card(samples_root):
    from fastapi.testclient import TestClient
    from datahive.interface.app import create_app
    js = TestClient(create_app(samples_root)).get("/app.js").text
    for marker in ('"Action space"', "actionChipsHtml", "baseActionField", 'fd.getAll("action_space")'):
        assert marker in js


def test_profile_form_recommended_cards_are_collapsible_and_new_controls_exist(samples_root):
    from fastapi.testclient import TestClient
    from datahive.interface.app import create_app
    client = TestClient(create_app(samples_root))
    js = client.get("/app.js").text
    css = client.get("/style.css").text
    for marker in ('profileCard(IC_GAIN, "Gains", REC', 'profileCard(IC_CALIB, "Camera calibration", REC', "gains_controller",
                   'name="is_biarm"', 'name="uses_mobile_base"', 'class="switch"', 'data-calib="intrinsic"', "collapsible"):
        assert marker in js, marker
    assert 'segmentedControlHtml("is_biarm"' not in js and 'segmentedControlHtml("uses_mobile_base"' not in js
    assert ".profile-card.collapsible .profile-card-body { display: none;" in css and ".switch-slider" in css
    # cameras: only a full-width name (calibration replaces position/orientation)
    cam = js[js.index("function cameraEntryHtml"):js.index("function renderProfileForm")]
    assert 'data-field="name"' in cam and 'data-field="position"' not in cam and 'data-field="orientation"' not in cam
    assert '<label class="full">HiveBoard version' in js



def test_action_joint_names_are_optional_saved_and_snapshotted(samples_root, filled_profile):
    from datahive.profile import incompleteness_problems, read_raw_profile, save_profile
    assert incompleteness_problems(_complete()) == []                                    # optional
    assert any("action_joint_names contains duplicates" in p for p in incompleteness_problems(_complete(action_joint_names=["a", "A"])))
    save_profile(samples_root, _complete(action_joint_names=[" j1 ", "j2", ""]))
    assert read_raw_profile(samples_root)["action_joint_names"] == ["j1", "j2"]
    assert filled_profile.to_dict()["action_joint_names"] == [f"j{i}" for i in range(6)]


def test_action_joint_names_must_match_the_command_width(samples_root, filled_profile):
    import h5py
    from datahive.errors import ValidationError
    from datahive.validate import validate_episode
    from datahive.annotate import annotate_episode
    h5 = make_episode(samples_root, "s", "ep1", trial_id="t1", profile=filled_profile)
    annotate_episode(samples_root, "ep1", {"attachment_id": "valve_ball", "outcome": "success", "strategy": "prehensile",
                                           "operator_name": "Op", "annotator_name": "Ann"}, validate_after=False)
    import json
    with h5py.File(h5, "r+") as f:
        f.attrs["action_joint_names"] = json.dumps(["a", "b", "c"])                    # 3 names, 6 values per step
    with pytest.raises(ValidationError) as exc:
        validate_episode(samples_root, "ep1", update_index=False)
    assert "3 action joint names" in "\n".join(exc.value.problems)
    with h5py.File(h5, "r+") as f:
        f.attrs["action_joint_names"] = json.dumps([f"j{i}" for i in range(6)])
    assert validate_episode(samples_root, "ep1", update_index=False) == []


def test_action_joint_names_field_is_in_the_profile_form(samples_root):
    from fastapi.testclient import TestClient
    from datahive.interface.app import create_app
    js = TestClient(create_app(samples_root)).get("/app.js").text
    assert 'name="action_joint_names"' in js and "payload.action_joint_names" in js


def test_orientation_representation_rules():
    from datahive.profile import incompleteness_problems, orientation_problem
    for ok in ("quat", "matrix", "rot6d", "rotvec", "euler_xyz", "euler_ZYX", "euler_xyx"):
        assert orientation_problem(ok) is None
    for bad in ("euler_xxy", "euler_xYz", "euler_", "quaternion", "euler_xy"):
        assert orientation_problem(bad)
    cart = ["cartesian_position", "gripper_binary"]
    assert any("orientation_representation is required" in p for p in incompleteness_problems(_complete(action_space=cart)))
    assert incompleteness_problems(_complete(action_space=cart, orientation_representation="rot6d", robot_state_orientation_representation="rot6d")) == []
    assert incompleteness_problems(_complete(action_space=["cartesian_velocity", "gripper_binary"], robot_state_orientation_representation="quat")) == []
    assert any("robot_state_orientation_representation" in p for p in incompleteness_problems(_complete(robot_state_orientation_representation="euler_xxy")))
    assert incompleteness_problems(_complete(robot_state_orientation_representation="quat")) == []              # recommended, optional


def test_orientation_fields_are_saved_and_snapshotted(samples_root):
    from datahive.profile import load_profile, read_raw_profile, save_profile
    save_profile(samples_root, _complete(action_space=["cartesian_position", "gripper_binary"],
                                          orientation_representation="rot6d", robot_state_orientation_representation="quat"))
    raw = read_raw_profile(samples_root)
    assert raw["orientation_representation"] == "rot6d" and raw["robot_state_orientation_representation"] == "quat"
    h5 = make_episode(samples_root, "s", "ep1", profile=load_profile(samples_root))
    header = read_header(h5)
    assert header.orientation_representation == "rot6d" and header.robot_state_orientation_representation == "quat"


def test_orientation_fields_are_in_the_recommended_group_of_the_form(samples_root):
    from fastapi.testclient import TestClient
    from datahive.interface.app import create_app
    js = TestClient(create_app(samples_root)).get("/app.js").text
    card = js[js.index('profileCard(IC_GEN2, "Orientation", REC'):js.index("profileCard(IC_GEN,")]
    assert 'orientationSelectHtml("orientation_representation"' in card
    assert 'orientationSelectHtml("robot_state_orientation_representation"' in card
    assert "rot6d" in js and "euler_XYZ" in js


def test_gains_are_optional_and_follow_oopsie_shape():
    from datahive.profile import incompleteness_problems
    def text(gains, joints=("j1", "j2")):
        return "\n".join(incompleteness_problems(_complete(manipulator={"joint_names": list(joints)}, gains=gains)))
    assert text(None) == "" and text({}) == ""
    assert text({"joint_position": {"kp": [1, 2], "kd": [0.1, 0.2]}}) == ""
    assert text({"joint_velocity": {"kv": [5, 5]}}) == ""
    assert text({"osc": {"kp_pos": [1, 1, 1], "kd_pos": [1, 1, 1], "kp_ori": [1, 1, 1], "kd_ori": [1, 1, 1]}}) == ""
    assert "must be a mapping" in text("Kp=[100]")
    assert "unknown controller" in text({"pid": {"kp": [1]}})
    assert "kd must be a list of numbers" in text({"joint_position": {"kp": [1, 2]}})
    assert "unknown field" in text({"joint_velocity": {"kv": [1, 2], "kp": [1, 2]}})
    assert "has 3 values but there are 2 joint names" in text({"joint_velocity": {"kv": [1, 2, 3]}})
    assert "gains" not in text({"joint_velocity": {"kv": [1, 2, 3]}}, joints=())      # no joint names: length is not checked


def test_calibration_matrices_are_optional_and_validated_per_camera():
    from datahive.profile import incompleteness_problems
    eye3 = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    eye4 = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    assert incompleteness_problems(_complete(intrinsic_calibration_matrix={"external": eye3}, extrinsic_calibration_matrix={"external": eye4})) == []
    text = "\n".join(incompleteness_problems(_complete(
        intrinsic_calibration_matrix={"external": eye4, "ghost": eye3}, extrinsic_calibration_matrix={"external": eye3})))
    assert "intrinsic_calibration_matrix['external'] must be a 3x3" in text
    assert "'ghost', which is not a camera" in text
    assert "extrinsic_calibration_matrix['external'] must be a 4x4" in text


def test_gains_and_calibration_are_saved_and_snapshotted(samples_root):
    from datahive.profile import load_profile, read_raw_profile, save_profile
    eye3 = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    save_profile(samples_root, _complete(gains={"joint_velocity": {"kv": [1, 2]}}, intrinsic_calibration_matrix={"external": eye3}))
    raw = read_raw_profile(samples_root)
    assert raw["gains"] == {"joint_velocity": {"kv": [1, 2]}} and raw["intrinsic_calibration_matrix"]["external"] == eye3
    header = read_header(make_episode(samples_root, "s", "ep1", profile=load_profile(samples_root)))
    assert header.gains == {"joint_velocity": {"kv": [1, 2]}} and header.intrinsic_calibration_matrix["external"] == eye3


def test_action_joint_names_live_in_the_action_space_card(samples_root):
    from fastapi.testclient import TestClient
    from datahive.interface.app import create_app
    js = TestClient(create_app(samples_root)).get("/app.js").text
    action = js[js.index('profileCard(IC_LL, "Action space", REQ'):js.index('profileCard(IC_LL, "Low-level control"')]
    rig = js[js.index('profileCard(IC_ARM, "Rig and manipulator"'):js.index('profileCard(IC_LL, "Action space", REQ')]
    assert 'name="action_joint_names"' in action and 'name="action_joint_names"' not in rig


def test_saving_cameras_does_not_leave_null_fields(samples_root):
    from datahive.profile import read_raw_profile, save_profile
    save_profile(samples_root, _complete(cameras=[{"name": "external", "resolution": None, "position": None}]))
    assert read_raw_profile(samples_root)["cameras"] == [{"name": "external"}]


def test_minimized_recommended_cards_have_no_divider_line(samples_root):
    from fastapi.testclient import TestClient
    from datahive.interface.app import create_app
    css = TestClient(create_app(samples_root)).get("/style.css").text
    assert ".profile-card.collapsible:not(.open) > .card-header-with-icon { border-bottom: none;" in css


def test_no_platform_id_message_is_produced_any_more():
    from datahive.profile import SKELETON, incompleteness_problems
    text = "\n".join(incompleteness_problems(SKELETON))
    assert "platform_id" not in text


def test_base_command_rules():
    from datahive.profile import incompleteness_problems
    def text(**kw):
        return "\n".join(incompleteness_problems(_complete(**kw)))
    arm = ["joint_position", "gripper_binary"]
    assert text(action_space=arm + ["base_velocity"]) == ""                       # may be given without a mobile base
    assert text(action_space=arm + ["base_position"]) == ""
    assert "at most one base action" in text(action_space=arm + ["base_position", "base_velocity"])
    assert "must include a base action" in text(uses_mobile_base=True, action_space=arm)     # required with a mobile base
    assert text(uses_mobile_base=True, action_space=arm + ["base_velocity"]) == ""
    assert text(uses_mobile_base=False, action_space=arm) == ""


def test_base_command_field_is_shown_only_for_a_mobile_base_and_is_single_choice(samples_root):
    from fastapi.testclient import TestClient
    from datahive.interface.app import create_app
    js = TestClient(create_app(samples_root)).get("/app.js").text
    field = js[js.index('id="baseActionField"'):js.index("</label>", js.index('id="baseActionField"'))]
    assert 'profile.uses_mobile_base === true ? "" : "hidden"' in field
    assert 'segmentedControlHtml("base_action"' in field and "BASE_ACTIONS, true" in field
    assert "baseField.hidden = !mobile" in js
    assert 'if (payload.uses_mobile_base && fd.get("base_action")) payload.action_space.push' in js


def test_end_effector_other_requires_a_description():
    from datahive.profile import incompleteness_problems
    ee = {"type": "other", "actuated_dof": 2, "command_modality": "position"}
    assert any("type_description is required" in p for p in incompleteness_problems(_complete(end_effector=ee)))
    assert incompleteness_problems(_complete(end_effector={**ee, "type_description": "Three-finger suction hand"})) == []
    assert incompleteness_problems(_complete(end_effector={**ee, "type": "gripper"})) == []


def test_end_effector_form_has_an_other_option_with_a_description_field(samples_root):
    from fastapi.testclient import TestClient
    from datahive.interface.app import create_app
    js = TestClient(create_app(samples_root)).get("/app.js").text
    assert '"prosthetic_hand", "other"]' in js
    assert 'name="end_effector.type_description"' in js and 'id="eeOtherField"' in js
    assert "eeField.hidden = !eeOther" in js and 'payload.end_effector.type !== "other"' in js


def test_joint_names_and_action_joint_names_are_required_with_a_joint_action():
    from datahive.profile import incompleteness_problems
    for action in ("joint_position", "joint_velocity"):
        data = _complete(action_space=[action, "gripper_binary"], manipulator={"joint_names": []}, action_joint_names=[])
        text = "\n".join(incompleteness_problems(data))
        assert "robot_state_joint_names" in text and "action_joint_names are required" in text
    ok = _complete(action_space=["joint_velocity", "gripper_binary"], manipulator={"joint_names": ["a"]}, action_joint_names=["a"])
    assert incompleteness_problems(ok) == []
    only_state = "\n".join(incompleteness_problems(_complete(manipulator={"joint_names": ["a"]}, action_joint_names=[])))
    assert "action_joint_names are required" in only_state and "robot_state_joint_names" not in only_state


def test_state_orientation_is_required_with_any_cartesian_action():
    from datahive.profile import incompleteness_problems
    for action in ("cartesian_position", "cartesian_velocity"):
        data = _complete(action_space=[action, "gripper_binary"], orientation_representation="quat")
        assert any("robot_state_orientation_representation is required" in p for p in incompleteness_problems(data))
        data["robot_state_orientation_representation"] = "rot6d"
        assert incompleteness_problems(data) == []


def test_conditional_asterisks_stay_on_the_label_line(samples_root):
    from fastapi.testclient import TestClient
    from datahive.interface.app import create_app
    js = TestClient(create_app(samples_root)).get("/app.js").text
    for mark in ("jointNamesMark", "actionJointMark", "actionOrientMark", "stateOrientMark"):
        i = js.index(f'id="{mark}"')
        assert js[js.rindex("<span class=\"field-label-text\">", 0, i):i].count("</span>") == 0, mark   # mark sits inside the label-text wrapper
