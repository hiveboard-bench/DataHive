---
name: datahive-auto-collect
description: Use when a user wants their robot to record HiveBoard trials automatically with DataHive — the Runner's "Automatic" mode, CollectClient / EpisodeWriter, writing or adapting a robot-side collection script (ROS, ROS 2, a vendor SDK, a simulator, a custom controller), or debugging why the browser and the robot script are not in step.
---

# DataHive: automatic collection

Automatic mode lets the **browser drive the trial plan** while **the user's robot script
records each episode**. The operator presses "Send to robot" in the Runner; the script, running
on the robot's computer, picks the task up, runs the trial, writes an `EpisodeWriter` file, and
hands it back; the operator then annotates it in the Annotate tab and the loop repeats.

DataHive does not know how the user's robot works. The script is theirs: the only DataHive
parts are `CollectClient` (talk to the browser) and `EpisodeWriter` (write the file). Adapting
the mode to a lab means filling in three blanks in a script: *read the robot state*, *send the
command*, *grab the camera video*. For the file format, the profile and the validation rules use
the `datahive-data-prep` skill; do not duplicate them here.

## How it works

```
browser (Runner)                   datahive interface            robot script
 pick session, "Send to robot" ──► state: pending  ◄── wait_for_task() polls, claims → running
                                                       record episode (EpisodeWriter)
 (timer shown)                     state: annotating ◄── finish(writer): closes .h5, hands over
 annotate episode, Save     ──►    annotation saved ──► finish() returns True
                                   state: idle         next loop iteration
```

State machine on the server (`collect.py`):
`idle → pending → running → annotating → idle`. Only one trial at a time. `abort` (browser)
resets to idle and makes `finish()` return `False`.

## Set up, once

1. `pip install datahive-tools` on **the machine the robot script runs on** (add
   `'datahive-tools[video]'` for OpenCV), run `datahive init`, and `datahive new-profile` then
   fill in the profile (`datahive-data-prep` skill, `reference/robot-profile.md`). Camera
   **names in the profile must match the names the script passes to `attach_video`**.
2. Start the GUI: `datahive interface --port 8000` (`datahive serve` is the same). It never
   returns; run it in the background or give the user the command. It binds to **localhost
   only**.
3. In the browser: Runner → create a session (operator + date; lab id comes from `datahive init`,
   platform id from the profile) → choose the tasks → generate the plan (5 per task) → switch the
   trial screen to **Automatic**.
4. Start the robot script (below). It blocks until the operator presses **Send to robot**.

The script must run where it can reach `http://127.0.0.1:8000` **and** see the same
`samples/` folder the GUI uses. If the robot computer is not the GUI computer, forward the port
(`ssh -L 8000:127.0.0.1:8000 robot-pc`) and point `base_url` at the tunnel; note the script
writes `samples/` locally, so run `datahive interface --samples <dir>` on the machine that holds
those files, or the episode will not be found.

## Minimal script

```python
from pathlib import Path
from datahive.collect import CollectClient

robot = CollectClient(Path("samples"), base_url="http://127.0.0.1:8000")

while True:
    task = robot.wait_for_task()           # blocks until "Send to robot"; claims the trial
    with robot.new_writer(task) as writer: # collection_mode="automatic", profile snapshotted
        run_trial(writer, task)            # <- the lab-specific part (reference/adapting.md)
    if not robot.finish(writer):           # closes, hands over, waits for the annotation
        print("Trial aborted by the operator")
```

(`finish()` closes the writer itself; the `with` block closing it first is harmless. If
`run_trial` raises, do not call `finish` — see "When things go wrong".)

`task` contains: `session_id`, `trial_id`, `attachment_id`, `name`, `family`, `timeout`
(seconds), `stages`, `success`, `reset`, `instruction` (defaults to `success`), `lab_id`,
`platform_id`, `operator_name`. Use `task["attachment_id"]` to select a policy/skill,
`task["instruction"]` to prompt a language-conditioned policy, and `task["timeout"]` to bound
the run.

## Rules

**The script records, the human annotates.** Never have the script pick `outcome`,
`failure_cause` or `operator_name`. Success detection in the robot code is not a substitute for
the operator watching the video. Do not call the annotation API to auto-save a label.

**Do not fake data to satisfy the checks.** State must be logged at >= 100 Hz from the real
loop, timestamps from one monotonic clock, all datasets appended on the same tick. If the robot
cannot reach 100 Hz, say so; do not pad or duplicate rows.

**Do not run the user's robot.** Writing the script is fine; executing a script that moves a
physical arm is the user's call. Read-only checks (importing, `--help`, dry runs with the
robot calls stubbed) are fine.

**Do not hand-write the HTTP calls or the HDF5.** Use `CollectClient` and `EpisodeWriter`; the
endpoints in `reference/protocol.md` are for debugging only.

## After the run

`datahive check` (all episodes, no annotation needed), fix anything it reports, annotate the
rest in the GUI, then `datahive validate <id>` and hand the user `datahive upload` / `datahive sync`.
Health checks and error meanings: `datahive-data-prep` skill.

## Reference files

- `reference/adapting.md` — turning the minimal script into the user's: recording loop,
  cameras, ROS 2 / vendor SDK / simulator / policy variants, timing, and an interview to
  ask the user before writing code.
- `reference/protocol.md` — the state machine, HTTP endpoints, `CollectClient` methods, and
  failure modes when the browser and the script disagree.
