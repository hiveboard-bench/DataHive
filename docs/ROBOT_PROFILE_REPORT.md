# Robot profile: DataHive vs oopsie-data-tools

Reviewed: `src/datahive/profile.py`, the profile form in `interface/static/app.js`
(`renderProfileForm`), `check.py`/`validate.py`/`consistency.py`, and oopsie-data 1.1.0
(`utils/robot_profile/robot_profile.py`, `template.py`, `skill/reference/robot-profile.md`).

## 1. What each one is trying to do

| | oopsie-data | DataHive |
|---|---|---|
| Purpose of the profile | Describes what the policy **observes and outputs**, so the validator can check the recorded arrays against it | Describes the **rig** (arm, gripper, low-level layer, cameras, board) so runs are comparable across labs |
| Who edits it | Human, in YAML (skeleton from `new-profile`), guided by an agent skill that asks questions step by step | Human, in a 6-card web form or YAML |
| Where it lives | Named files in a `robot_profiles/` dir (many profiles per lab) | One `samples/robot_profile.yaml` |
| Stored per episode | Whole profile as JSON attr | Snapshot of the profile in the header |
| Enforced against the data | Both directions, as **errors** (see 2.3) | Only partly, mostly as warnings |
| Required fields | 9, explicit list, enforced at load | 3 rules enforced (`low_level.mode`, `joint_names`, cameras) out of about 30 form fields |

Both use the same good idea: a skeleton that is deliberately not loadable until a human fills it in, so a
half-edited profile cannot stamp placeholder metadata into every episode. Keep that.

## 2. Why DataHive's profile feels confusing

### 2.1 It is unclear what is mandatory
- The form shows about 30 inputs in six cards. Only a few carry the red asterisk, and the loader enforces
  only three rules (`incompleteness_problems`). A user cannot tell "needed to record" from "nice to have".
- `platform_id` is required on every annotation and header (`TrialAnnotation.platform_id: str`) but is not
  enforced in the profile and can be an empty string. The form puts it under "Board", where nobody looks for it.
- oopsie states the rule once: nine required keys, listed in one table, and everything else is "optional,
  stored, never validated".

### 2.2 Overlapping fields that say almost the same thing
| Fields | Problem |
|---|---|
| `control_mode` (top level), `low_level.controller_type`, `end_effector.command_modality`, `low_level.mode` | Four "how is it controlled" fields in three different cards. Free text in two of them. Nothing says which one wins |
| `manipulator.dof` and `len(manipulator.joint_names)` | Two sources of truth. `dof` is never checked; `consistency.py` now checks the joint count against the data but not against `dof` |
| `low_level.rate_hz` (e.g. 500), recorded rate (>= 100 Hz), camera `fps` (30) | Three different rates with no explanation. A user will type one into the wrong box |
| `policy` | It changes between runs (teleop today, a VLA tomorrow) but lives in a rig-level file, so changing policy means editing the profile and changing every later episode's snapshot |
| `platform_id` in the profile and in every trial row | Duplicated, and the row can drift from the profile |
| `units_and_frames` | Invisible in the form, never validated, but it is what tells a reader `ee_pose` is `xyz + quaternion xyzw` |

### 2.3 The profile does not describe what is actually recorded
oopsie declares `robot_state_keys` and `action_space` and then validates both directions: every declared key
must be present, nothing undeclared may be present, joint-name count must equal the array's last axis, and
`cartesian_position` must be 7 (or 14) numbers with a unit quaternion. That is what makes its profile
useful rather than decorative.

DataHive hard-codes the streams (`proprioception`: joint_position/velocity/torque, ee_pose, ee_state;
`commands`: target, control_mode) and the profile never says which are present. Consequences:
- A lab that has no torque sensor cannot say so; a missing stream and a broken recording look the same.
- `check.py` only compares camera names and joint-name sets, and only as warnings.
- The orientation convention is a fixed string inside `units_and_frames`. oopsie makes it an explicit,
  validated choice (`quat`, `rot6d`, `euler_xyz`, ...) and warns that case is meaningful.

### 2.4 Free text where a choice or a number is needed
`control_mode`, `controller_type`, camera `encoding`, `position`, `orientation`, and `gains` are all free
text; camera `resolution` is a `"1280x720"` string. Since `validate` now compares the declared resolution and
fps with the real MP4, a typo such as `1280 x 720` or `1920X1080` becomes a hard validation error at record
time, far from where it was typed.

### 2.5 Camera setup is the most error-prone part and gets no help
Cameras must all match in resolution and fps (rule we added) and must match the real videos, yet the user
types each one by hand. Camera `position` and `orientation` are words ("front", "level"), so they cannot be
used for anything. oopsie stores intrinsic and extrinsic matrices per camera name, which is the usable form.

### 2.6 One profile, no history
- Only one profile per `samples/` directory: a lab with two rigs has to switch files. oopsie allows named
  profiles (`new-profile --name`).
- The profile itself has no `schema_version`, no revision, and no hash. Editing it silently makes later
  episodes differ from earlier ones, and nothing in the UI says "these 40 episodes used revision A, the next
  60 used B".
- Saving does not warn that existing episodes keep the old snapshot.

### 2.7 The form layout adds to it
All six cards are open at once, there is no "3 of 5 required fields done" indicator, explanations are per
card rather than per field, and the order is hardware first while the mandatory fields are scattered across
cards. oopsie's own guidance is to ask **step by step**, because "a full list up front easily overwhelms".

## 3. What DataHive does better (keep)
- A web form. oopsie has none; its users hand-edit YAML.
- `low_level.mode = stock | custom` (a HiveBoard-specific requirement that matters for comparison).
- `board_fabrication`, which mirrors the Evaluation Runner.
- Camera consistency is enforced across cameras.
- Snapshot per episode is the right semantics.
- Progressive disclosure is already used elsewhere in the app.

## 4. What oopsie gets wrong or leaves loose (do not copy)
- `orientation_representation` strings are not validated at profile load (bad values fail later, as a plain
  `ValueError` from the recorder).
- Optional fields (`controller`, `gains`, calibration) are stored and never validated.
- The profile is code-facing: the "what policy" questions make sense for a policy-evaluation dataset, not for
  a rig-comparison benchmark.
- No profile versioning either.

## 5. Recommendations

### P0: remove the confusion
1. **One requirements table.** Split the form into three labelled groups and say so on screen:
   *Required to record* (arm name, joint names, gripper type, low-level mode, at least one camera, platform
   ID), *Recommended* (controller, rates, calibration), *Optional* (board fabrication, notes). Show a live
   "5 of 6 required fields" counter and disable nothing, but list what is missing at the top.
2. **Collapse overlapping fields.**
   - Drop `dof` and derive it from `len(joint_names)` (show it as read-only text).
   - Merge `control_mode` and `low_level.controller_type` into one dropdown with an "other" free-text escape.
   - Keep `end_effector.command_modality` separate but explain the difference in its tooltip.
   - Label the three rates plainly: "Controller loop rate", "Recording rate (min 100 Hz)", "Camera fps".
3. **Move `policy` out of the rig profile** into the session or per-episode field, defaulting to the last
   value. Rig facts stay in the profile; run facts do not.
4. **Move `platform_id` to the top of the form, marked required**, and make the annotation copy come only
   from the profile (no separate typing).
5. **Validate the profile as strictly as the data.** Add `resolution` as two numbers (or a dropdown of common
   sizes), `fps` numeric, camera names unique (case-insensitive, as oopsie does), and reject values that will
   later fail `validate`.

### P1: make it describe the recording (the oopsie idea worth adopting)
6. **Declare the streams.** Add `streams: {joint_position, joint_velocity, joint_torque_or_current, ee_pose,
   ee_state}` as checkboxes. Validate both directions: a declared stream must exist and be finite; an
   undeclared one must not.
7. **Explicit orientation convention** for `ee_pose` (`quat_xyzw`, `quat_wxyz`, `rot6d`, `euler_xyz`, ...),
   validated at load, plus a 7-number/unit-quaternion check for quaternion conventions (this also unblocks the
   check I skipped earlier).
8. **Show `units_and_frames`** in a collapsed "Units and conventions" card with sane defaults and one-line
   explanations.
9. **Optional additional sensors** (force/torque, tactile) using oopsie's model: named stream, `sensor`,
   `sensor_info`, `format: array|video`. Only if a lab needs them.
10. **Camera calibration** as optional intrinsic/extrinsic matrices per camera, replacing the "front / level"
    words with something machine-usable; keep the words as a free-text label.

### P1: help the user get it right
11. **"Detect from video"** on each camera: pick a sample MP4 and fill resolution, fps and encoding. This
    removes the most common typo class now that the values are checked against the files.
12. **"Copy from camera 1"** and "Add another identical camera" (cameras must match anyway).
13. **"Generate j1..jN"** for joint names from a DoF number, plus presets (Franka Panda, UR5e, xArm, etc.)
    that fill model, joint names and defaults.
14. **Inline validation and a per-field "why we ask" tooltip**, using the instant tooltip already built for
    outcomes. Rewrite the unclear ones: "Low-level mode: stock = the vendor's own controller/API; custom =
    your own real-time loop".
15. **Order the form by the recording workflow**: Identity (platform, arm, gripper) -> Control -> Cameras ->
    Board. Put a sticky summary of missing fields at the top.

### P2: versioning and multiple rigs
16. **Profile `schema_version`** and a **revision id** (hash of the content). Store the revision in every
    episode header; show "Profile revision a3f9 (41 episodes)" in the UI and the Overview.
17. **Warn on save**: "N episodes already recorded keep the previous snapshot. Only new episodes use this."
    Offer to bump the revision label.
18. **Multiple named profiles** (`profiles/<name>.yaml`), with the active one selected per session, for labs
    with more than one rig.
19. **`datahive profile show/diff`** CLI, so two labs can compare rigs and a lab can see what changed
    between revisions.

## 6. Suggested minimal target shape

```yaml
schema_version: datahive_profile_v1
platform_id: rig-01            # required, top of form
manipulator: {model: Franka Panda, joint_names: [j1, ..., j7]}   # dof derived
end_effector: {type: gripper, command_modality: position, actuated_dof: 1}
control: {mode: stock, controller: cartesian_impedance, rate_hz: 500, gains: null}
recording: {rate_hz: 100, streams: [joint_position, joint_velocity, ee_pose, ee_state],
            ee_pose_orientation: quat_xyzw}
cameras:                      # all identical: resolution, fps
  - {name: external, resolution: [1280, 720], fps: 30, label: front}
board: {mounting: horizontal, hiveboard_version: v2, fabrication: {...}}
units_and_frames: {...}       # collapsed, defaulted
```
`policy` moves out to the session/episode.

## 7. Suggested order
1. Week 1: items 1-5 (clarity, no data-model change beyond `dof`, `policy`, `platform_id`).
2. Week 2: items 11-15 (form helpers) and 6-8 (declared streams and orientation).
3. Week 3: items 16-18 (revision, warnings, multiple rigs), then 9-10 and 19 as needed.

Migration note: keep reading the current flat layout and upcast it on read (as done for annotations), so
existing `robot_profile.yaml` files and episode snapshots stay valid.
