# Robot profile (`samples/robot_profile.yaml`)

Filled in **once per rig**. Every episode snapshots it into its own header when it is created,
so editing the profile later only affects new episodes. Create it with `datahive new-profile`
(or the Robot Profile panel in `datahive interface`) — the skeleton leaves required fields blank
on purpose so a half-edited profile cannot stamp placeholders into episodes. `datahive validate`
refuses to run until it is complete.

Ask the user for the values; do not guess a robot's DOF, control mode or camera setup.

## Required fields

| field | value |
|---|---|
| `robot_name` | e.g. `franka_panda` |
| `gripper_name` | e.g. `robotiq_2f_85` |
| `platform_id` | leave blank: derived as `<robot>_<gripper>` slug (`franka_panda_robotiq_2f_85`) |
| `is_biarm`, `uses_mobile_base` | `true` / `false` |
| `control_freq` | control/recording rate in Hz, > 0 |
| `action_space` | list, see below |
| `policy` | who/what drives the robot: `teleop_spacemouse`, `vla_pi0`, `diffusion_policy`, ... (override per episode with `EpisodeWriter(policy=...)`) |
| `manipulator.model` | follows `robot_name` |
| `manipulator.joint_names` | required if `action_space` has a joint action; `dof` is derived |
| `action_joint_names` | required if `action_space` has a joint action; what each index of the action vector is |
| `end_effector.type` | `gripper` \| `dexterous_hand` \| `prosthetic_hand` \| `other` (`other` needs `type_description`) |
| `end_effector.actuated_dof` | whole number >= 1 |
| `end_effector.command_modality` | `binary` \| `position` \| `velocity` |
| `low_level.mode` | **`stock` or `custom`, never empty.** `custom` needs `custom_description` (what the controller does, its loop rate, anything needed to compare results) |
| `cameras` | at least one; each needs a unique `name` |

## `action_space`

Choose from:

- arm (>= 1): `joint_position`, `joint_velocity`, `cartesian_position`, `cartesian_velocity`
- gripper (>= 1): `gripper_position`, `gripper_velocity`, `gripper_binary`
- base (<= 1): `base_velocity`, `base_position` — required if `uses_mobile_base: true`

Example: `[joint_position, gripper_binary]`. The order defines how `/commands/target` is laid out.

## Orientation

- `orientation_representation` — required when `action_space` has `cartesian_position`.
- `robot_state_orientation_representation` — required when it has `cartesian_position` or
  `cartesian_velocity` (recommended otherwise): how `ee_pose` orientation is stored.
- Values: `quat` (scalar-last, 4), `matrix` (3x3), `rot6d` (6), `rotvec` (3), or
  `euler_<order>` — lowercase = extrinsic (`euler_xyz`), uppercase = intrinsic (`euler_ZYX`);
  no repeated adjacent axes.

## Cameras

```yaml
cameras:
  - name: External          # used in the mp4 filename; unique ignoring case
    position: "front, 60 cm from board, 45 deg down"   # free text, recommended
    orientation: "facing board centre"
    resolution: 1280x720    # normalized from "1280 x 720"; filled from the video on recording
    fps: 30
```

All cameras on the rig must share the same resolution and fps. `resolution`, `fps` and
`encoding` are overwritten with what `attach_video` measures from each real video.

## Optional fields

- `control_mode`: `cartesian_impedance`, `osc`, `joint_position`, ...
- `gains`: `{joint_position: {kp: [...], kd: [...]}}`, `{joint_velocity: {kv: [...]}}` or
  `{osc: {kp_pos, kd_pos, kp_ori, kd_ori}}`. Arrays of numbers; for the joint controllers, one
  value per joint.
- `intrinsic_calibration_matrix` (3x3) / `extrinsic_calibration_matrix` (4x4), keyed by camera
  name (camera must exist in `cameras`).
- `board_mounting` (`horizontal` | `vertical`), `hiveboard_version`, `board_fabrication`
  (`printer`, `material`, `print_settings`, `post_processing`, `calibration_notes`).
- `units_and_frames`: defaults are `rad`, `rad/s`, `N*m`, `position_m_quaternion_xyzw_in_base_frame`,
  `monotonic_seconds`. Change them only if the recorded data truly uses other units — and then
  it is usually better to convert the data.

## Not collected (yet)

Depth, point clouds, base odometry, force/torque, tactile, IMU, audio and camera calibration
streams are not part of the episode format. Do not try to add extra datasets expecting them to
be validated or uploaded.

## Questions to ask, in order

1. Robot and gripper names? Single or dual arm? Mobile base?
2. Control/recording frequency? (Must be >= 100 Hz to submit.)
3. What does the policy output (action space)? Joint names and their order?
4. Stock low-level controller, or custom? If custom, how does it work?
5. End effector type, actuated DOF, command modality?
6. Cameras: names, placement, resolution, fps (all the same)?
7. Who or what drives it (`policy`)?
