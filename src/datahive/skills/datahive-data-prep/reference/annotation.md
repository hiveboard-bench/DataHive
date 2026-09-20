# Annotation (`trials.csv`)

One row per trial in `samples/<session>/trials.csv`, also mirrored inside the `.h5` under
`episode_annotations/<annotator>/`. `datahive annotate` (terminal) and the Annotate tab in
`datahive interface` both write it. The GUI opens straight on an episode and plays its video,
which is how a human should judge what happened.

**Annotation is human judgement.** Do not choose `outcome`, `failure_cause` or `severity`
yourself from a file. Ask the user, or prepare the command and let them decide.

```bash
datahive annotate <episode_id>                       # interactive prompts
datahive annotate <episode_id> --non-interactive --outcome success --attachment-id valve_ball \
  --strategy prehensile --operator-name "Ana" --annotator-name "Ana"
```

## Columns

`trial_id, lab_id, platform_id, attachment_id, date, operator_name, outcome, failure_cause,
failure_cause_detail, severity, completion_time_s, completion_source, n_attempts, n_regrasps,
stage_reached, strategy, annotator_name, notes, annotated_at, uploaded_at, schema_version`

`trial_id`, `lab_id`, `platform_id`, `date` are filled from the episode header. `annotated_at`
and `schema_version` are stamped automatically; `uploaded_at` by `upload`.

## Enumerations

- `outcome`: `success` | `fail` | `timeout` | `safety_stop`
- `failure_cause`: `grasp_geometry` | `kinematic_limit` | `perception` | `slip` | `force_limit`
  | `control_precision` | `other`
- `severity`: `minor` | `moderate` | `critical` (non-success only, optional)
- `strategy`: `prehensile` | `non_prehensile` (required for every trial)
- `completion_source`: `timer` | `hdf5` | `video`

## Cross-field rules

If `outcome` is `success`:
- `completion_time_s` is required (> 0). When omitted, it is filled from the episode duration
  with `completion_source: hdf5`. Runner manual mode uses `timer`.
- `failure_cause`, `failure_cause_detail` and `severity` must be blank.

If `outcome` is anything else:
- `failure_cause` is required; `completion_time_s` must be blank.
- `failure_cause: other` requires `failure_cause_detail` (say what actually happened).

Always:
- `operator_name` and `annotator_name` must be non-empty (checked by `validate`), and
  `operator_name` cannot be the Runner's placeholder `Unassigned`: set the real operator by
  editing the session plan (pencil icon) or when annotating.
- `n_attempts` and `n_regrasps` are integers >= 0 when given.
- `stage_reached` is an integer >= 0 and **required only for composed-assembly tasks**
  (`button`, `lock`, `drawer`, `shock_absorber`; use 0 if the first stage was never completed),
  and must be blank for the others.

## Agreement with the recording

- `success` on a task whose episode is longer than the task timeout (+1 s) is rejected: it
  should be `timeout`.
- `timeout` on an episode far shorter than the timeout (< 50%) gives a warning.
- `completion_time_s` cannot exceed the recording length + 1 s (except `timer` source).
- The outcome stored inside the `.h5` must match `trials.csv`; re-save the annotation if edited.
- Upload adds advisory warnings when >= 5 written notes are near-identical or all failures
  share one `failure_cause` (looks copy-pasted). They can be made blocking with
  `--strict-diversity`.

## The 13 tasks (`attachment_id`, timeout in seconds)

| family | attachment_id | timeout |
|---|---|---|
| Torque | `valve_ball` | 60 |
| Torque | `valve_ball_ring` | 90 |
| Torque | `valve_gate_small` | 90 |
| Torque | `valve_gate_large` | 120 |
| Torque | `circuit_breaker` | 60 |
| Precision | `light_bulb` | 120 |
| Precision | `thread_m8` | 120 |
| Precision | `thread_m30` | 120 |
| Precision | `peg_insertion` | 120 |
| Composed assembly (2 stages) | `button` | 60 |
| Composed assembly (3 stages) | `lock` | 180 |
| Composed assembly (3 stages) | `drawer` | 120 |
| Composed assembly (3 stages) | `shock_absorber` | 180 |

The authoritative list, success and reset descriptions is `src/datahive/attachments.yaml`; a
lab can override it with `samples/attachments.yaml`. An `attachment_id` not in the registry
only produces a warning (stage_reached is then not checked), but it will not match HiveBoard.
