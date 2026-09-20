---
name: datahive-data-prep
description: Use when preparing robot manipulation data for DataHive / HiveBoard — deciding what to record and upload, laying files out under samples/, writing or debugging the robot profile, converting an existing dataset (HDF5, ROS bags, LeRobot, MP4s) into the datahive_episode_v1 format, running `datahive check` / `validate`, annotating trials, or fixing a validation error before upload.
---

# DataHive: getting data ready

DataHive (`datahive-tools`) is the client-side toolkit a lab uses to collect, validate,
annotate and upload **HiveBoard** manipulation trials to its private Hugging Face dataset.
HiveBoard is a benchmark: a 3D-printed board with 13 task attachments (valves, threaded
fasteners, peg insertion, breaker, button, lock, drawer, ...). A lab runs **5 trials on each
of the 13 tasks (65 trials)** on its own robot and submits them.

Each trial becomes one **episode**: one `.h5` file (robot state + commands + a snapshot of the
robot profile), one `.mp4` per camera, and one annotation row in `trials.csv`. Everything lives
under a single `samples/` directory until the user uploads.

Your job with this skill: get the user's data into that shape so that `datahive validate`
passes. Run `datahive <command> --help` rather than guessing flags.

## The pipeline

```bash
datahive init                   # one time: lab id + Hugging Face token (user runs it)
datahive new-profile            # one time: writes samples/robot_profile.yaml, then fill it in
#   ... put episodes in samples/<session_id>/episodes/  (record, or convert) ...
datahive check <episode_id>     # pre-annotation health check: HDF5, Hz, gaps, MP4s
datahive annotate <episode_id>  # outcome + failure info (human judgement)
datahive validate <episode_id>  # full rules; marks the episode "validated"
datahive upload <episode_id>    # publishes to the Hub (user's decision)
datahive interface --port 8000  # local web GUI: Runner + Annotate (also `datahive serve`)
```

`check` needs no annotation and no complete profile, so run it first and often. It applies the
same recording rules as `validate` (rate, gaps, NaN, aligned arrays, 1-600 s, videos). What
only `validate` adds: the robot profile, the annotation, and how the annotation agrees with the
recording. Both take `--json`; prefer it. `datahive check` with no id checks every episode
under `samples/`; `check` exits 1 if any episode fails.

## Where data goes

**Everything goes inside the `samples/` folder** (default `./samples`, or pass `--samples DIR`):

```
samples/
  robot_profile.yaml                       # once per rig
  attachments.yaml                         # optional override of the 13 tasks
  <session_id>/                            # one testing day / batch
    trials.csv                             # one annotation row per trial (annotate writes it)
    session.json                           # Runner sessions only; not needed for imported data
    episodes/
      <episode_id>.h5
      <episode_id>_cam_<camera_name>.mp4   # one per camera in the robot profile
```

DataHive finds episodes by scanning `samples/*/episodes/*.h5`, so copying files in is enough.
Episodes brought in through the GUI (Runner manual mode, or Annotate > **Upload episode**) are
more forgiving: the `.h5` only needs `/proprioception` (with `timestamp`) and `/commands`;
DataHive rebuilds the header from the robot profile and the session, and renames the files to
`<session_id>_t<trial_id>.h5` / `..._cam_<camera>.mp4`. Files copied in by hand must already be
complete and correctly named.
Read `reference/layout-and-format.md` for the exact contents, names and numeric limits.

## Rules

**Never invent identity or annotation content.** `lab_id` comes from the user's registration,
`operator_name` is who ran the trial, `annotator_name` is who labelled it, and `outcome`,
`failure_cause`, `n_attempts` and notes record what actually happened. If you do not know one,
ask. A trial that was never watched cannot be annotated by you.

**Never hand-write `~/.datahive/config.yaml`.** `datahive init` writes it with mode 0600 and
keeps the token out of the repo. The token is the user's secret: do not put it in a command
line you run, and prefer letting the user type `! datahive init` themselves.

**Fix the data, never the checker.** If `check` or `validate` fails, repair the recording or the
profile. Do not edit thresholds, timestamps or header values to make a bad episode pass — a
faked sample rate or a re-labelled outcome corrupts the benchmark. Interpolated or resampled
values are legitimate only if declared `provenance: estimated`.

**Do not run `upload`, `sync` or `delete` for the user.** They publish to, or remove data
from, the lab's Hub repo. Hand over the command. (`delete` also removes remote copies unless
`--keep-remote`.)

**`datahive annotate` needs both `--operator-name` and `--annotator-name`** (the GUI needs them
too). Ask the user for both; never fill them in yourself.

**`datahive interface` never returns** — it serves until Ctrl-C. Run it in the background and
give the user the URL, or just hand them the command.

## Preparing data: the order that works

1. Confirm the setup: `samples/robot_profile.yaml` exists and is complete, and
   `~/.datahive/config.yaml` exists (never print the token). No profile →
   `datahive new-profile`, then fill it in with the user (`reference/robot-profile.md`).
2. Decide the source of the episodes:
   - **Recording with DataHive** (Runner manual/automatic mode, or `EpisodeWriter` in the user's
     own loop): the files come out correctly named. For automatic mode use the
     `datahive-auto-collect` skill.
   - **Converting existing data**: write each episode with `EpisodeWriter` (recommended — it
     snapshots the profile into the header for you) rather than hand-building HDF5. See
     `reference/layout-and-format.md`, "Converting existing data".
3. Put files in `samples/<session_id>/episodes/`, then `datahive check` each episode.
4. Annotate (`reference/annotation.md`), then `datahive validate`.
5. Give the user the `datahive upload` (or `datahive sync`) command.

## Reference files

Read the one you need, not all of them.

- `reference/layout-and-format.md` — folder layout, file naming, HDF5 groups and datasets,
  frequency / duration / video limits, and how to convert an existing dataset.
- `reference/robot-profile.md` — every profile field, legal values, and the questions to ask.
- `reference/annotation.md` — `trials.csv` columns, outcome and failure taxonomy, cross-field
  rules, the 13 tasks with their timeouts.
- `reference/troubleshooting.md` — each `check` / `validate` message, its cause and fix, plus
  mistakes that pass silently.

For automatic collection from a robot script, use the sibling skill `datahive-auto-collect`.
