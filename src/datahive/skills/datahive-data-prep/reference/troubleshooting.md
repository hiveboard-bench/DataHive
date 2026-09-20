# Troubleshooting

`datahive check --json` and `datahive validate --json` return the messages below. Fix the
data or the profile; never loosen the checks. Anything reported as an unexpected exception
(not a `problems` line) means the checker broke: report it, do not work around it.

## `datahive check` (recording integrity)

`check` also reports everything in the next table that concerns the recording itself
(NaN/aligned arrays, DOF, duration, video content). The next section only adds the profile
and annotation rules.

| message | cause / fix |
|---|---|
| `No episode ... found` / `HDF5 file does not exist` | File is not at `samples/<session>/episodes/<episode_id>.h5`. Check the folder and the id. |
| `Could not read header` | File is not a DataHive `.h5`, is truncated (recording crashed before `close()`), or was written without `EpisodeWriter`. Re-record or re-convert. |
| `Header has no low_level.mode` | Profile lacked `low_level.mode`. Fix the profile; episodes already written keep the blank, so re-create them. |
| `Header lists no cameras` | Profile `cameras` was empty when the episode was created. |
| `Missing /proprioception group` / `timestamp dataset` | State was never appended. |
| `only N step(s); minimum is 2` | Recording too short or loop never ran. |
| `timestamps not increasing` / `not monotonically increasing` | Mixed clocks (wall + monotonic), unsorted rows, or a clock reset. Use one monotonic clock. |
| `sample rate is X Hz, below the required 100 Hz` | Loop or logger too slow. Log on the control thread at >= 100 Hz, or record the source at its native rate. Slow sources cannot be submitted. |
| `severe timestamp gap(s)` | A gap > 3x the median interval: logger blocked (disk, GC, video encode on the same thread). Move video/IO off the control loop. |
| `Camera 'X' video file missing` / `no mp4 file recorded in header` | `attach_video` not called, wrong camera name, or the mp4 is not named `<episode_id>_cam_<name>.mp4`. |
| `video file is empty (0 bytes)` | Recorder crashed; re-record. |
| `Camera names in header differ from profile` (warning) | Profile was edited after recording, or camera names differ in case/spelling. |

## `datahive validate` (adds consistency + annotation)

| message | cause / fix |
|---|---|
| `robot_profile.yaml is incomplete: ...` | Complete the listed fields (`robot-profile.md`). |
| `lab_id is empty or still 'your_lab_id'` | Run `datahive init` with the real lab id, then re-record; the id is stamped at creation. |
| `Unsupported episode schema` | Upgrade `datahive-tools`. |
| `operator_name / annotator_name is required` | Ask the user; annotate again. |
| `No annotation row found in trials.csv for trial_id 'N'` | Annotate the episode. If the row exists, the `.h5` header `trial_id` differs from the CSV `trial_id`. |
| `Annotation for trial ... is invalid: ...` | See cross-field rules in `annotation.md`. |
| `Annotation stored in the .h5 disagrees with trials.csv` | trials.csv was hand-edited. Save the annotation again. |
| `/group/name is empty` / `not numeric` / `contains NaN` | Sensor dropout or bad conversion. Fix the source; do not zero-fill. |
| `Arrays do not share the same number of steps` | Proprioception and commands logged on different ticks, or one dataset appended conditionally. |
| `joint_position has N DOF but the header lists M joint names` | `manipulator.joint_names` wrong, or fewer/more values written. |
| `/commands/target has N values per step but the profile lists M action joint names` | `action_joint_names` does not match the action vector width. |
| `Episode lasts Xs; it must be between 1 and 600 s` | Trim to the trial, or split runs that were recorded together. |
| `Camera 'X' video is WxH; each side must be between 180 and 1280 px` | Re-encode. |
| `video is WxH but the episode header records ...` / `fps but the header records` | Header was hand-written or video re-encoded after `attach_video`. Call `attach_video` again on the final file. |
| `video lasts Xs but the episode lasts Ys` | Video and state streams not synced; trim or re-record. Tolerance 0.5 s. |
| `All cameras must have the same resolution / fps / frame count` | Re-encode all cameras to one resolution and fps. |
| `video path must be relative` / `escapes the samples directory` | `cameras[].file` must be just the file name in `episodes/`. |
| `Outcome is success but the episode exceeds the ...s timeout` | Should be `timeout`, or the episode includes too much idle time. Ask the user. |
| `Episode is incomplete: missing video: <camera>` | Upload/copy the missing mp4 (also in Annotate > Upload). |

## Mistakes that pass silently

- **No OpenCV**: video checks are skipped with only a warning. Install `datahive-tools[video]`.
- **Wrong units** (degrees, mm, ms): nothing checks magnitudes. Confirm units before converting.
- **Timestamps not in seconds**: a ms clock reads as 0.1 Hz and fails; a ns clock may pass
  with a nonsense rate. Sanity-check `episode_stats`.
- **`estimated` mislabelled as `measured`**: not detectable, but it misleads dataset users.
- **Copy-pasted notes**: only a diversity warning at upload.
- **Task mislabelled**: `attachment_id` is not verified against the video. Look at it.
- **Placeholder profile fields** that are technically non-empty (`policy: test`, `robot_name: TestArm`).
  Real values only.
- **Editing the profile after recording** does not change existing episodes; the header keeps the
  old snapshot.

## Upload-side problems

- `Session ... has N files in episodes/, over the HuggingFace limit`: split the session
  (move the newest episodes and their rows into `<session>_part2`).
- `upload` fails on auth: the token in `~/.datahive/config.yaml` lacks write access; the user
  reruns `datahive init --force`.
- `datahive sync --dry-run` prints `Would upload: <ids>` without uploading anything. Episodes
  that fail validation are never uploaded (the GUI's bulk upload lists them as not validated).
