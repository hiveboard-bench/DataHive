# Layout and format

Everything below is enforced by `datahive check` / `datahive validate` (source of truth:
`check.py`, `consistency.py`, `validate.py`). Schema id: `datahive_episode_v1`.

## Directory layout (inside `samples/`)

```
samples/
  robot_profile.yaml
  <session_id>/
    trials.csv
    episodes/
      <episode_id>.h5
      <episode_id>_cam_<camera_name>.mp4
```

- `session_id`: letters, digits, `_` and `-` only. Runner default:
  `<lab_id>_<platform_id>_<YYYYMMDD>` (a `_2`, `_3` suffix if that exists). Use one session per
  testing day, or per batch.
- `episode_id`: unique across all of `samples/`. Runner uses `<session_id>_t<trial_id>`
  (e.g. `mylab_franka_x_20260919_t7`). `trial_id` is the position in the trial plan
  (`"1"`..`"65"`); it links the `.h5` header to its row in `trials.csv`.
- Videos: the file name must be exactly `<episode_id>_cam_<camera_name>.mp4`, where
  `<camera_name>` is a camera `name` from `robot_profile.yaml` (case and spelling identical),
  and the header's `cameras[].file` must hold that file name (relative, no directories).
- HuggingFace rejects more than **10,000 files in one directory**. `episodes/` holds one `.h5`
  plus one `.mp4` per camera per episode, so start a new session well before that (a warning
  appears at 90%). A 65-trial run is nowhere near it.

## How many trials, and what each one is

- 5 trials on each of the 13 tasks = 65 trials. Runner shuffles them by default so conditions
  interleave. One episode = one trial = one attempt at one task.
- Record the whole attempt from before motion starts until success, failure or timeout. Each
  task has a timeout (see `annotation.md`); an episode must last **1 s to 600 s**.

## The `.h5` file

Root attributes (the header) + two groups. Use `EpisodeWriter` to produce all of it.

### Header (root attrs)

Dict/list values are stored as JSON strings, and the attr `_json_fields` lists which keys are
JSON. Native scalars otherwise. Required for validation:

- `lab_id` (not empty, not `your_lab_id`), `platform_id`, `session_id`, `episode_id`, `trial_id`
- `task_ids`: `[attachment_id]`, e.g. `["valve_ball"]`
- `schema_version`: `datahive_episode_v1` (`_v0` still reads, with a warning)
- `created_at`: UTC timestamp
- `low_level.mode` (`stock`|`custom`) and a non-empty `cameras` list
- the rest is a snapshot of `robot_profile.yaml` (see `robot-profile.md`), plus
  `collection_mode` (`manual` | `automatic` | none) and `policy`

### `/proprioception` — robot state, at least 100 Hz

| dataset | shape per step | notes |
|---|---|---|
| `timestamp` | scalar | **required**; monotonic seconds (`units_and_frames.timestamp`) |
| `joint_position` | `(n_joints,)` | `n_joints` must equal `len(manipulator.joint_names)` |
| `joint_velocity` | `(n_joints,)` | optional |
| `joint_torque_or_current` | `(n_joints,)` | optional |
| `ee_pose` | e.g. `(7,)` | position (m) + orientation in `robot_state_orientation_representation` |
| `ee_state` | e.g. `(1,)` | gripper opening/state |

Each dataset carries a `provenance` attr: `measured` (default) or `estimated` (interpolated,
filtered, or computed rather than read from a sensor). Be honest about it.

### `/commands` — what was sent to the robot

- `timestamp` (scalar), `target` (the action vector, in `action_space` order), `control_mode`
  (UTF-8 bytes as `uint8` per step; `append_command` handles it)
- If `action_space` contains `joint_position` or `joint_velocity`, `target`'s width must equal
  `len(action_joint_names)`.

### Consistency rules across the file

- **Every dataset in both groups must have the same number of steps.** Log proprioception and
  commands on the same tick.
- Values numeric and finite (no NaN/inf). No empty datasets.
- Timestamps strictly increasing; median sample rate **>= 100 Hz** (99 Hz tolerated); no gap
  larger than 3x the median interval. `control_freq` in the profile should match the real rate
  within 10% (else a warning).
- At least 2 steps; duration 1-600 s.

## Videos (`.mp4`, one per profile camera)

- Side length between **180 and 1280 px** (each side); e.g. 640x480 or 1280x720.
- **All cameras identical** in resolution, fps and frame count (frame counts within 1).
- Video duration (`frames / fps`) within **0.5 s** of the episode duration; fps within 0.5 of
  what the header records; header resolution matches the real one.
- Non-empty, decodable. H.264 is recommended so the browser GUI can play it (not enforced).
- Video content is only inspected when OpenCV is installed: `pip install 'datahive-tools[video]'`.
  Without it you get a warning, not a pass — install it.
- `EpisodeWriter.attach_video(camera, src)` copies/moves the file into place and fills the
  header's `file`, `resolution`, `fps`, `encoding` from the actual video.

## Converting existing data

Write each episode through the library so header, names and profile snapshot are right:

```python
from pathlib import Path
from datahive.episode import EpisodeWriter

samples = Path("samples")
with EpisodeWriter(samples, session_id, episode_id, trial_id="7",
                   task_ids=["valve_ball"], lab_id="mylab",
                   policy="teleop_spacemouse") as w:        # profile loaded from samples/
    for t, q, qd, tau, pose, grip, cmd in my_rows:
        w.append_proprioception(
            timestamp=t, joint_position=q, joint_velocity=qd,
            joint_torque_or_current=tau, ee_pose=pose, ee_state=grip,
            provenance={"joint_position": "measured", "ee_pose": "estimated"},
        )
        w.append_command(timestamp=t, target=cmd, control_mode="joint_position")
    for cam_name, mp4 in my_videos.items():
        w.attach_video(cam_name, mp4)                        # cam_name must be in the profile
```

Conversion checklist:

1. Timestamps to monotonic **seconds** (not ms/ns), starting anywhere, strictly increasing.
2. Resample so state and commands share ticks, at >= 100 Hz. If the source is slower than
   100 Hz it cannot be submitted; interpolating up must be declared `estimated` and the user
   should know that is a judgement call.
3. Units: joints in rad, rad/s, N*m; `ee_pose` position in metres. Convert, do not relabel.
4. Video: transcode to a matching resolution/fps across cameras and trim/pad so duration
   matches the state stream (`ffmpeg -i in.mp4 -vf scale=640:480 -r 30 -c:v libx264 out.mp4`).
5. Task id: map to one of the 13 `attachment_id`s. Ask if unclear.
6. `profile` argument: pass one only if the episode came from a different rig than
   `samples/robot_profile.yaml`.

Ask the user where each value came from when a mapping is not obvious; do not guess frames,
units or joint order.

## Reading a file back

```python
from datahive.episode import read_header, episode_stats, episode_dataset_info
read_header(Path("samples/S/episodes/E.h5"))    # EpisodeHeader
episode_stats(path)          # n_steps, duration_s, sample_rate_hz
episode_dataset_info(path)   # shape / dtype / provenance per dataset
```
