---
description: Convert LeRobot Dataset v3.x recordings into a DataHive samples root with `datahive convert`, including camera mapping, resampling and the annotation sidecar.
---

# Converting existing datasets with `datahive convert`

`datahive convert` is bundled with `datahive-tools`. It reads episodes recorded outside DataHive
and writes a normal `samples/` root, so the standard `datahive check`, `validate`, `upload` and
`sync` commands accept the result unchanged.



::: warning Target schema
Converter output is **`datahive_episode_v1`**. The OOPSIE project's `oopsiedata_format_v1` is a
different schema with different field names, action keys and annotation taxonomy. The converter's
structure (helper-based writer, per-episode isolation, validation-first loop) follows the
[OOPSIE conversion guide](https://oopsie-data.com/conversion/), but the data written here is
`datahive_episode_v1` and is validated by `datahive validate`.
:::

## Supported sources

| Source | Adapter | Status |
|---|---|---|
| LeRobot Dataset `v3.x` | `src/datahive/converter/sources/lerobot_v3.py` | Implemented and tested on `victorvanhalst/hive_botao_100` |
| RLDS, ROS bag, custom HDF5 | — | Add one `DatasetSource` module under `sources/` and register it in `ADAPTERS` (`sources/__init__.py`); the conversion core, CLI, annotations and video pipeline stay unchanged |

## Install

```bash
pip install "datahive-tools[converter]"
```

In a checkout, `pip install -e ".[dev]"` installs the same dependencies. Verify the installation
with:

```bash
datahive convert-selftest
```

The converter commands are part of the main `datahive` CLI. The underlying argparse module is
also runnable as `python -m datahive.converter.cli` for development.

## Commands

```text
datahive convert <samples>              convert source episodes into a samples root
datahive convert-profile <samples>      write a robot_profile.yaml into a samples root
datahive convert-probe                  print what the source adapter sees
datahive convert-annotations-template   write the sidecar annotation CSV skeleton
datahive convert-selftest               run the converter's unit tests
```

Run `datahive <command> --help` for every flag.

## Output

The converter writes a standard DataHive samples root:

```text
<samples>/
  robot_profile.yaml
  <session_id>/
    trials_<session_id>.csv
    episodes/
      <episode_id>.h5
      <episode_id>_cam_wrist.mp4
      <episode_id>_cam_external.mp4
  conversion_report.json
```

- `session_id` — `{lab_id}_{platform_id}_{YYYYMMDD}` unless `--session-id` is given.
- `trial_id` — `1..N` in source-episode order.
- `episode_id` — `{session_id}_t{trial_id}`, globally unique under the samples root.
- `conversion_report.json` — settings, summary counts and one record per source episode
  (`written`, `exists`, `skipped`, `failed`, reason/detail). Override the path with `--report`.

## What the converter writes

The output is `datahive_episode_v1`; the format itself is documented in the DataHive format guide.
Converter-specific choices:

- **`joint_position` holds arm joints only.** The gripper is written to `ee_state`
  (observation) and `gripper_position` (action); `manipulator.joint_names` and
  `action_joint_names` therefore list arm joints, and `end_effector.actuated_dof` describes the
  gripper.
- **Arm joints are converted to radians.** The bundled SO-101 profile declares
  `source_joint_units: degrees`, so arm state and action columns are converted from degrees to
  radians before resampling. Gripper values are copied unchanged.
- **Provenance.** Resampled state/action datasets are written with `provenance: estimated`;
  unresampled data is `measured`.
- **`task_ids`** is `[--attachment-id]`, and the trials row uses the same `attachment_id`.
- **The robot profile is snapshotted** into each episode header at creation time.

Source-to-target mapping for LeRobot v3.x:

| Target | LeRobot v3.x source |
|---|---|
| `robot_states/joint_position` | `observation.state[:, :-1]` — arm joints |
| `robot_states/ee_state` | `observation.state[:, -1:]` — gripper column |
| `actions/joint_position` | `action[:, :-1]` |
| `actions/gripper_position` | `action[:, -1:]` |
| timestamps | uniform grid at the output rate, starting at 0 |
| arm joint units | degrees → radians when `source_joint_units: degrees` |
| `video_paths/<camera>` | per-episode segment sliced from the shard for the mapped video key |
| `task_ids` / trials `attachment_id` | `--attachment-id` |
| profile `control_freq` | `--resample-hz` (default 100) |

Joint names come from `meta/info.json` (`features["observation.state"]["names"]`, with the `.pos`
suffix stripped). The adapter requires the gripper to be the last entry and refuses the dataset
otherwise, because silently mislabelling a joint is worse than failing.

### Camera mapping

LeRobot video keys map to camera names by their last token:
`observation.images.wrist` → `wrist`. Override one or more names without dropping the defaults:

```bash
datahive convert-probe --source <dataset> \
  --camera-map wrist=observation.images.top
```

The target names must match the `cameras` in `robot_profile.yaml`; otherwise conversion refuses
the episode.

## Resampling to 100 Hz

DataHive rejects proprioception below **99 Hz** (`MIN_SAMPLE_RATE_HZ = 100.0`). Most third-party
datasets are 25–30 Hz.

The converter resamples **proprioception and actions** onto a uniform grid at `--resample-hz`
(default 100) by linear interpolation:

```text
duration = (T_source - 1) / source_fps
T_output = round(duration * resample_hz) + 1
```

First and last samples are preserved exactly, so start and end poses are untouched. Cameras are
**not** resampled: the validator treats camera fps as independent of the control rate, so videos
keep the source frame rate and only their duration must match the episode (within 0.5 s).

::: warning Resampling is interpolation, not measurement
`provenance` is a dataset-level attribute, so a resampled array is written wholly as `estimated` —
original and synthesised ticks cannot be distinguished inside the file. If 100 Hz fidelity matters
for your scientific question, collect at 100 Hz rather than relying on the upsample.
:::

`--resample-hz` below 100 is refused unless `--allow-low-rate` is passed. That flag writes the data
at its native rate; `datahive validate` will then reject it. It exists for archival, not for
submission.

## Robot profile

Write the profile once per samples root, then fill in or replace it as needed:

```bash
datahive convert-profile out/ --profile so101
```

The bundled SO-101 profile (`src/datahive/converter/profiles/so101.yaml`) declares:

- `control_freq: 100` — the converted rate, not the source rate.
- `action_space: [joint_position, gripper_position]`.
- five arm joints plus `end_effector: {type: gripper, actuated_dof: 1, command_modality: position}`.
- `low_level.mode: stock`.
- cameras `wrist` and `external` at the recorded resolution and fps.
- `source_joint_units: degrees` — converter-only: LeRobot's SO-101 `*.pos` arm values are in
  degrees, so they are converted to radians. The gripper column is copied unchanged.
- `units_and_frames` describes the **output**: `joint_position: rad` and
  `ee_state: normalized_percent_of_servo_range`.

To convert a different robot, add a profile with matching `manipulator.joint_names`,
`action_joint_names`, `control_freq` and `cameras`, then pass `--profile <bundled-name-or-path>`.
Add `source_joint_units` when the source arm values are not already radians. `profile
--control-freq` and `--policy` override those two fields when writing the skeleton.

## Annotations

`datahive_episode_v1` has no unlabelled state: every episode needs a trials row, and annotation is
human judgement. The converter therefore never guesses silently.

Supply a sidecar CSV keyed by source episode index. Generate the skeleton first — it pre-fills
`strategy` and `stage_reached` and leaves `outcome` for you:

```bash
datahive convert-annotations-template \
  --source victorvanhalst/hive_botao_100 --stage-reached 2 --output labels.csv
```

The filled file looks like:

```csv
episode_index,outcome,strategy,stage_reached,failure_cause,failure_cause_detail,severity,n_attempts,n_regrasps,notes
0,success,prehensile,2,,,,,,
1,fail,prehensile,1,slip,,minor,2,1,gripper slipped on the cover
```

| Column | Required | Allowed values |
|---|---|---|
| `episode_index` | yes | source episode index, unique |
| `outcome` | yes | `success`, `fail`, `timeout`, `safety_stop` |
| `strategy` | optional | `prehensile`, `non_prehensile`; defaults to `--strategy` |
| `stage_reached` | composed assemblies only | integer ≥ 0; **forbidden** for other tasks |
| `failure_cause` | non-success | `grasp_geometry`, `kinematic_limit`, `perception`, `slip`, `force_limit`, `control_precision`, `other` |
| `failure_cause_detail` | `failure_cause=other` | free text |
| `severity` | optional, non-success | `minor`, `moderate`, `critical` |
| `n_attempts`, `n_regrasps` | optional | integer ≥ 0 |
| `notes` | optional | free text |

Rows whose `outcome` is still blank are treated as not yet annotated, so you can convert a subset
(`--limit`) before the whole sheet is filled. A selected episode with no filled row is an error.

Rules enforced before anything is written:

- A successful trial must not carry `failure_cause`, `severity` or `failure_cause_detail`.
- A non-successful trial must carry `failure_cause`; `other` requires detail.
- `completion_time_s` is filled from the episode's own duration, never from the CSV.
- Composed assemblies (`button`, `lock`, `drawer`, `shock_absorber`) need `stage_reached`; simple
  attachments must not have it.

Without a CSV the run refuses (exit code 2). If you genuinely have no labels, `--assume-success`
records `outcome=success` with `--strategy` and, for composed tasks, `--stage-reached`, and writes
this into the row's notes:

> `[conversion] outcome=success assumed: no annotation sidecar was supplied and --assume-success was set. Verify labels before submission.`

The run banner and the report both count assumed episodes. **An assumed label is not a label.**

## Video

Per camera and episode, the converter slices the source segment, re-encodes to H.264/`yuv420p`
with `+faststart`, keeps the source frame rate, and forces the segment to exactly the source frame
count (trimming, or cloning the last frame inside the validator's tolerance). Sides are capped at
1280 px and must be at least 180 px. Segments are located by accumulating each episode's `length`
within its shard, because LeRobot packs whole episodes and concatenates them; nominal
`from_timestamp` values are used only as a cross-check.

`--video-mode copy` stream-copies the source codec. It is much faster and is **not** suitable for
submission: AV1 output may not open in OpenCV or a browser. Copied output is not checked for
browser playability; re-run with the default `--video-mode transcode` for submission.
`--video-crf` (default 19) and `--video-preset` (default `medium`) tune the transcode.

## Usage

```bash
# 1. write the robot profile into the samples root
datahive convert-profile out/ --profile so101

# 2. see what the adapter reads, before writing anything
datahive convert-probe \
  --source victorvanhalst/hive_botao_100 --metadata-only

# 3. create the annotation sheet, one row per source episode
datahive convert-annotations-template \
  --source victorvanhalst/hive_botao_100 --stage-reached 2 --output labels.csv
#    ... then fill in the outcome column

# 4. rehearse
datahive convert out/ \
  --source victorvanhalst/hive_botao_100 \
  --lab-id my_lab --operator-name OPERATOR_NAME --annotator-name ANNOTATOR_NAME \
  --attachment-id button --strategy prehensile --stage-reached 2 \
  --annotations labels.csv --limit 3 --dry-run

# 5. convert, validating each episode as it is written
datahive convert out/ \
  --source victorvanhalst/hive_botao_100 \
  --lab-id my_lab --operator-name OPERATOR_NAME --annotator-name ANNOTATOR_NAME \
  --attachment-id button --strategy prehensile --stage-reached 2 \
  --annotations labels.csv
```

**No labels yet?** Omit `--annotations` and add `--assume-success` (plus `--stage-reached` for
composed tasks). Every row is then stamped as an assumption; see [Annotations](#annotations).

`--operator-name` and `--annotator-name` are required and copied verbatim; the converter has no
defaults. The names in these examples are placeholders — replace them with the real people.
`datahive validate` rejects empty names and the `Unassigned` placeholder.

`--source-joint-units radians|degrees` overrides the profile's `source_joint_units`. Use it when a
profile does not declare the source convention; arm joints are converted to radians and the
gripper column is copied unchanged.

`--source` accepts a local directory or a Hugging Face `owner/name` repo id (downloaded into
`--cache-dir`, default `.cache/datasets`). `--start-index` and `--limit` select source episodes;
`--overwrite` redoes an episode; a re-run leaves completed episodes untouched
(`written=0 exists=N`). `--collection-mode` (default `manual`) is stored in the episode header,
`--keep-invalid` keeps an episode that failed validation, and `--no-validate` skips the per-episode
validation.

Exit codes: `0` no failures (skips allowed), `1` at least one episode failed, `2` configuration
refusal. A **skip** is an episode that is legitimately out of scope — under one second, no video,
unresolvable segment — and is listed with its reason in `conversion_report.json`.

## Reading a converted episode

A converted episode aligns states, actions and video as follows:

```python
import h5py, cv2, json

EPISODE = "out/my_lab_so101_follower_so101_gripper_20261117/my_lab_so101_follower_so101_gripper_20261117_t1"

with h5py.File(f"{EPISODE}.h5", "r") as f:
    states = f["observations/robot_states/joint_position"][:]   # (T, arm joints)
    gripper = f["observations/robot_states/ee_state"][:]        # (T, 1)
    actions = f["actions/joint_position"][:]                    # (T, arm joints)
    timestamps = f["observations/robot_states/timestamp"][:, 0]
    cameras = {k: f["observations/video_paths"][k][()].decode()
               for k in f["observations/video_paths"]}
    camera_meta = {c["name"]: c for c in json.loads(f.attrs["cameras"])}

fps = camera_meta["wrist"]["fps"]
video = cv2.VideoCapture(f"{EPISODE.rsplit('/', 1)[0]}/{cameras['wrist']}")
frame_at = lambda i: video.read()[1]   # frame index i, at the camera's own fps
```

States, actions and timestamps share one index; camera frames run at the camera fps declared in the
header.

## Validation

Every converted episode is validated as it is written (`--no-validate` disables this). To re-check
independently:

```bash
datahive validate <episode_id> --samples out/
datahive check --samples out/ --json
```

A valid converted episode reports a sample rate of `--resample-hz`, no timestamp gaps, and one
entry per declared camera. Once valid, `datahive upload` accepts it like any DataHive-recorded
episode.

## Limitations

- **No `language_instruction`.** The episode schema has no free-text instruction field; the task
  lives in `task_ids` and the annotation. Name the HiveBoard task in your annotation notes if you
  need it recoverable.
- **No additional sensor streams.** Depth, force/torque and tactile data are not carried by this
  converter.
- **No cartesian, velocity or base actions from LeRobot.** The adapter reads joint-position +
  gripper only; use a different adapter for other action spaces.
- **Gripper units are not normalised.** Arm joints are converted to radians; the gripper is
  written in the source servo units (percent of range for SO-101).
- **Upsampling adds no information.** 25 Hz data becomes a 100 Hz file, not a 100 Hz recording.
- **One profile per samples root.** Converting two different robots means two samples roots.

## Appendix: `hive_botao_100` probe

`probe --metadata-only` on `victorvanhalst/hive_botao_100`:

```text
version    v3.0   fps=25.0   robot=so_follower
episodes   100   frames=30358
cameras    wrist<-observation.images.wrist, external<-observation.images.external
joints     ['shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper']
tasks      {0: 'Open the cover and press the button'}
columns    data/chunk_index, data/file_index, dataset_from_index, dataset_to_index,
           episode_index, length, tasks,
           videos/observation.images.<cam>/{chunk_index,file_index,from_timestamp,to_timestamp}
episode0   length=263 dataset=[0, 263)  wrist/file_index=0  from=0.0  to=10.52
```

Timestamps in `meta/episodes` are seconds (`to - from = length / fps`), and `length` matches the
data rows for every episode. State and action values span roughly −103 to 98, consistent with
degrees; the converter converts the arm columns to radians and leaves the gripper column in
servo units.

Converting all 100 episodes with `--assume-success --attachment-id button --stage-reached 2`
writes 100 HDF5 episodes and 200 H.264 videos. The run summary is
`written=100 exists=0 skipped=0 failed=0`; each episode is validated as it is written.

The output totals 121,132 state/action steps, exactly
`sum(round((length - 1) / 25 * 100) + 1)` over the source episodes, so no frames were dropped or
invented beyond the interpolation. Source episodes run 5.64–18.16 s, inside the schema's 1–600 s
bounds. The 917 MB source produced 540 MB of output (640×480/25 fps videos), in about five minutes
on CPU.
