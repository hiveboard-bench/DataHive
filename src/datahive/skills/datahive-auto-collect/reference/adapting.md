# Adapting automatic collection to a robot

Before writing code, ask the user (one message, not a quiz):

1. **How do you talk to the robot?** ROS 2 / ROS 1, a vendor SDK (franka, UR RTDE, xArm, Kinova,
   ...), a simulator (MuJoCo, Isaac), or a custom controller?
2. **What is your control/recording rate?** It must be >= 100 Hz. Where does it run: your own
   loop, a real-time thread, a ROS timer?
3. **What drives the arm?** Teleop (device?), a learned policy (which?), a scripted skill? This
   is the profile's `policy`.
4. **How do you get camera frames?** OpenCV, RealSense, ROS image topics, a separate recorder
   that writes mp4? Camera names, and are resolution and fps identical across cameras?
5. **What is the action?** Joint targets, Cartesian poses, velocities? Must equal
   `robot_profile.yaml`'s `action_space` and `action_joint_names`.
6. **How do you reset between trials?** DataHive shows the task's reset instructions, but the
   robot must be at a safe start pose before the next `wait_for_task()` returns work.
7. **Is the profile already filled in?** If not, do that first (`datahive-data-prep`).

## The three blanks

```python
def read_state():   # -> dict with joint_position, joint_velocity, joint_torque_or_current, ee_pose, ee_state
    ...
def send_command(): # apply the next action; return the action vector you sent (target)
    ...
def camera_frames(): # -> {camera_name: BGR frame}  or a recorder you start/stop
    ...
```

Everything else is the same for every lab.

## Recording loop (one trial)

```python
import time
import cv2
from pathlib import Path

RATE_HZ = 200        # >= 100, and equal to the profile's control_freq
TMP = Path("/tmp/datahive_video")

def run_trial(writer, task):
    TMP.mkdir(exist_ok=True)
    cams = open_cameras()                         # {name: cv2.VideoCapture} - names from the profile
    vids = {n: cv2.VideoWriter(str(TMP / f"{n}.mp4"), cv2.VideoWriter_fourcc(*"mp4v"),
                               CAM_FPS, (W, H)) for n in cams}
    prov = {"joint_position": "measured", "joint_velocity": "measured",
            "ee_pose": "measured"}                # "estimated" for anything computed/filtered
    period, t0 = 1.0 / RATE_HZ, time.monotonic()
    next_tick = t0
    reset_to_start_pose()
    while True:
        now = time.monotonic()
        if now - t0 > task["timeout"] or operator_stop_requested():
            break                                 # the operator decides the outcome later
        state = read_state()
        action = policy_or_teleop(task, state)    # your code
        send_command(action)
        writer.append_proprioception(timestamp=now - t0, provenance=prov, **state)
        writer.append_command(timestamp=now - t0, target=action, control_mode="joint_position")
        grab_frames_into(vids, cams)              # keep this fast: see Timing
        next_tick += period
        time.sleep(max(0.0, next_tick - time.monotonic()))
    for v in vids.values():
        v.release()
    for name in vids:
        writer.attach_video(name, TMP / f"{name}.mp4", move=True)   # name must be in the profile
```

Notes:

- **Same tick for both groups.** Call `append_proprioception` and `append_command` once each
  per step so all datasets end with the same number of rows.
- **`timestamp`** in seconds from one monotonic clock (`time.monotonic()`, or the ROS clock
  converted to seconds). `EpisodeWriter` stores it as given; start at 0 or wall-clock, either is
  fine, but never mix clocks.
- **`state` keys** map straight to `append_proprioception`: `joint_position`, `joint_velocity`,
  `joint_torque_or_current`, `ee_pose`, `ee_state` (any may be omitted, `timestamp` is required).
  `joint_position` width must equal the profile's joint count.
- **`provenance`** is keyed by field: `measured` (from a sensor) or `estimated` (interpolated,
  filtered, numerically differentiated).
- **`target`** is the action in `action_space` order; joint actions must be as wide as
  `action_joint_names`.
- **Videos**: `attach_video(camera_name, path, move=False)` copies (or moves) the file to
  `<episode_id>_cam_<camera_name>.mp4` and fills resolution/fps/encoding from the real file.
  All cameras must match in resolution and fps, and the clip must last as long as the episode
  (within 0.5 s) — start video capture and the state loop together and stop them together.
  Each side 180-1280 px. `mp4v` plays in OpenCV; for browser playback prefer H.264
  (`avc1`, or transcode with `ffmpeg -c:v libx264`).
- **Episode length**: 1-600 s. Bound the loop with `task["timeout"]` (per-task, 60-180 s).
  Stopping early on success detection is fine, but the operator must be able to judge it.

## Timing

- The loop must hold >= 100 Hz **including** video. `cv2.VideoCapture.read()` blocks; capture
  frames in a background thread that keeps the latest frame, and write video from there (or
  use a separate recorder process). A logger that stalls > 3x the period is flagged as a
  "severe timestamp gap".
- Do not print in the loop, and do not block on disk.
- After the first trial, run `datahive check <episode_id>` and read `sample_rate_hz` and
  `timestamp_gaps` before collecting 65.

## Variants

**ROS 2**: use a node with a timer (`create_timer(1/RATE_HZ, cb)`); keep the latest
`JointState` and `Image` messages from subscribers; `cb` reads them, appends, and publishes
the command. Run `robot.wait_for_task()` in the main thread before spinning a per-trial
executor, or in a worker thread; it blocks. Convert `Time` to seconds:
`msg.header.stamp.sec + 1e-9 * msg.header.stamp.nanosec` (use one source for everything).
Video: `cv_bridge` frames into `cv2.VideoWriter`, or record with your own mp4 tool.

**Vendor SDK**: `read_state()` wraps the SDK's state call (e.g. `robot.get_state()`); if the
SDK exposes its own control loop callback, append inside it so timestamps match the controller.

**Simulator**: same code, `read_state()` from the sim; render camera frames per physics step.
Use simulated time as the timestamp (monotonic and >= 100 Hz in sim seconds). Make the profile
say honestly that it is simulation (e.g. in `robot_name`/`policy`), and `hiveboard_version`
etc. as appropriate — ask the user whether simulated episodes are acceptable for their
submission.

**Learned policy**: `policy_or_teleop` runs the model at its own rate but state/command logging
still happens at >= 100 Hz; log the last commanded action each tick. Set `policy` in the profile
or per episode: `robot.new_writer(task, policy="pi0_v2")`.

**Teleop**: `policy_or_teleop` reads the device; log the resulting commanded target. Make the
operator stop control (`operator_stop_requested`) with a hardware button or key, since the
browser cannot stop your loop mid-trial (only abort between states).

## Configuring the script

- `base_url`: default `http://127.0.0.1:8000`; use another port if you passed `--port`.
- `CollectClient(samples_root, ...)`: must be the same `samples/` directory the GUI uses.
- `robot.new_writer(task, episode_id=None, profile=None, policy=None)`: default episode id is
  `t<trial_id>_<UTC timestamp>` (e.g. `t2_20260920T164951`), unlike manual mode's
  `<session_id>_t<trial_id>`; pass `episode_id=` to choose your own. Override `profile` only for a different rig.
- `wait_for_task(timeout_s=...)` raises `CollectError` if nobody sends a trial; catch it to
  loop with a heartbeat.
- `finish(writer, wait_for_annotation=False)` hands over without blocking, so the script can
  run the next reset while the operator annotates; the next `wait_for_task()` still waits until
  the browser is idle again.
- `poll_s` (default 0.5 s) is the polling interval.

## When things go wrong

- **`run_trial` raised mid-episode.** The `with` block closes the file, leaving a partial
  episode on disk and the browser stuck in *running*. Catch the exception, then either call
  `robot.finish(writer)` if the data is usable (the operator annotates it, probably as `fail`
  with a cause) or ask the operator to press **Abort** in the browser and delete the partial
  episode in Annotate. Never leave the loop stuck.
- **Ctrl-C the script.** Same: the browser stays in *running* until **Abort**.
- **Operator presses Abort.** `finish()` returns `False`; the episode file is still on disk.
  Delete it in Annotate if it should not count.
- More failure modes and endpoints: `reference/protocol.md`.
