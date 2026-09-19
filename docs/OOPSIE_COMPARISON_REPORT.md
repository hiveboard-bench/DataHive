# DataHive vs oopsie-data-tools: annotation and organization report

Source reviewed: `oopsie-data-tools 1.1.0` (installed locally: schema, annotator server and UI, CLI,
validator docs, diversity check, skill docs). DataHive was compared against the current working tree.

Purpose differs: oopsie collects *failure* episodes for one shared taxonomy. DataHive collects
*HiveBoard trials*, whose `trials.csv` columns must match the benchmark. So the recommendations
below adopt oopsie's mechanisms, not its vocabulary.

## 1. How oopsie does it

**Organization**
- One HDF5 per episode, MP4s beside it, video paths stored *relative* to the `.h5`.
- Annotations live inside the episode: `episode_annotations/<annotator_name>/` as attrs.
  Multiple annotators per episode are supported, each with `source` (human or model name) and `timestamp`.
- Two version stamps: `schema = oopsie_failure_taxonomy_v2` and `taxonomy_schema = ..._v2`.
  Old v1 files are upcast on read and never rewritten.
- Stored values are stable slugs (`grasp`, `catastrophic`), never UI prose. Unknown legacy values are kept visible, not silently mapped to `other`.
- The robot profile is embedded in every episode and validated against the data in both directions
  (nothing missing, nothing undeclared, joint-name count == DOF).
- `restructure` and `--with-restructure` split directories that exceed HuggingFace's per-directory file limit.

**Annotation content**
- `outcome`: success, success_suboptimal, success_side_effect, failure. It also writes a numeric `success` float, and the validator checks the two agree.
- `failure_category` is a **list** (reaching, grasp, manipulation, sequencing_semantic, collision, hardware, not_attempted, other). It says *where in the task* it failed.
- `severity`: low, medium, catastrophic.
- `episode_description` (what happened) is separate from `additional_notes`.
- `language_instruction` is required, editable per episode, with a "recent instructions" picker.

**Annotator UI**
- Prev/Next with arrow keys, "Next unannotated", auto-advance after save, and a "hide fully annotated" filter with a shown count.
- Playback speed persisted across sessions, synced scrubbing across videos, video prefetch.
- Copy from a previous annotation. Completeness is tracked per episode ("tick level").
- In-the-loop mode: annotate right after each rollout while it is fresh.

**Quality gates**
- Validator: duration 1-600 s (not step count), video 180-1280 px, frame count vs T, video duration within 0.5 s of T/control_freq, frame counts across cameras within 1, finite numerics, unit quaternions, gripper domain, relative and contained video paths.
- Recorder validates *before* writing, so a bad episode leaves nothing on disk.
- `check_diversity` warns when one description is >80% of episodes (copy-paste or inattentive annotator). `--strict-diversity` makes it fatal.
- Error classification: `validation` (your data) vs `unexpected` (tool bug).

**Lab experience**
- `init` (0600 config), `show-config` (prints where credentials and profiles are resolved), `--config-dir`, `$HF_TOKEN` override, `submissions` (counts and last upload from Hub metadata), `inspect`, `--json` on validate/show-config/inspect, `install-skill` (agent guide), update check, extensive tests for each piece.

## 2. What to add (prioritized)

### P0: data quality risks that exist today
1. **"Copy from previous" needs a guardrail.** It is our fastest way to create the exact problem oopsie's diversity check exists to catch.
   - Mark copied fields as "copied, unreviewed" in the form.
   - Never copy `failure_cause_detail`/`notes` by default.
   - Add a diversity warning at validate/upload: same notes >80% of episodes, or one outcome/cause for every episode of a session.
2. **Cross-check the registry `timeout` against the episode.** `attachments.yaml` already has `timeout` per task, but nothing uses it.
   - `success` with duration > timeout is inconsistent.
   - `timeout` with duration much shorter than the limit is inconsistent.
   - Duration should match `completion_time_s` (we now derive it, so make the derived value the only source).
3. **Validate the actual video files, not only the profile.** From `validate.py` I only see profile-level camera consistency. Add per-file checks: file exists, resolution and fps match the header camera, frame count vs `n_steps`, duration vs episode duration, all cameras within 1 frame of each other.
4. **Validate numerics**: finite (no NaN/inf), same leading dimension across `/proprioception` and `/commands`, joint-name count == DOF, and duration bounds (oopsie uses 1-600 s; ours could use `0 < duration <= 1.5 x timeout`).
5. **Free-text identities cause silent splits.** `operator_name` and `annotator_name` are typed strings, so "Ana", "ana" and "Ana S." become three people. Keep a remembered roster (dropdown with "add new"), normalize whitespace and case, and warn on near-duplicates.

### P1: standardization
6. **Version the annotation schema.** Add `schema_version` to `trials.csv` rows and to the annotation stored in the `.h5`, plus a read-time upcast, as oopsie does. The vocab will change (we already renamed things several times).
7. **Store annotations in the `.h5` too, not only in `trials.csv`.** The CSV alone can drift from the videos when files are moved or renamed. Put a copy in `episode_annotations/<annotator>/` with `source` and `timestamp` so the episode is self-describing. Keep the CSV as the HiveBoard export.
8. **Support multiple annotators per episode**, plus a disagreement report. This is the cheapest inter-annotator-agreement signal for a benchmark.
9. **Separate "where" from "why" in failures.** Our `failure_cause` (grasp_geometry, slip, perception...) is a cause. oopsie's category is a phase (reaching / grasp / manipulation / collision). Consider an optional multi-select `failure_phase`, and allow more than one cause. Real failures are rarely single-cause.
10. **Write a rubric.** Severity has no definition (oopsie defines low/medium/catastrophic with damage semantics). Add one-line definitions to the instant tooltips for severity, strategy and each cause, and link the same text in the README so annotators agree.
11. **`episode_description` vs `notes`**: require a short factual description for every non-success (what happened), and keep notes for anything else.
12. **Consider optional success quality** (clean / suboptimal / side-effect). HiveBoard's outcome may stay binary, but suboptimal successes are where policy differences show up. Store as an extra field without changing the HiveBoard columns.
13. **Timestamps**: store ISO-8601 with timezone (UTC) for `annotated_at`, `created_at` and `uploaded_at`, so days grouped in the left panel do not shift between labs.

### P1: annotator experience
14. **Next unannotated** button and **auto-advance after save** (checkbox). Biggest speed win for long sessions.
15. **Filter "hide annotated"** and show the shown count. We have status filters and stats; this is the one-click version.
16. **Keyboard shortcuts**: left/right episode, space play/pause all, 1-4 outcome, number keys for severity, Ctrl+Enter save.
17. **Synced scrubbing across cameras** and frame stepping, plus **remember** speed/layout/collapsed days between sessions (localStorage).
18. **Completeness indicator** per episode in the list (dots for outcome / cause / detail / stage), not just a status word.
19. **Recent tasks and notes picker** (oopsie has recent instructions). Fits our task picker.
20. **Pre-upload summary**: N episodes, N unannotated, N warnings, total size, and a diversity report before the confirm.

### P2: lab operations
21. **`datahive show-config`** (where the config/samples/profile were resolved, token masked) and a `--json` flag on validate/check/status, so scripts and agents can consume results. Separate `validation` (fix your data) from `unexpected` (report a bug) in the output.
22. **`datahive submissions`**: counts and last upload time from Hub metadata (no download). We already have sync status; this is the lab-level view.
23. **HF directory-size guard.** HuggingFace rejects directories with too many files. Check before upload and suggest a split, as `restructure` does.
24. **Update check** (cached, non-blocking) so labs do not run stale validators.
25. **Agent skill / short `docs/` guide** for annotators: outcome definitions, examples of each failure cause, what "stage reached" means.
26. **Contained, relative video paths**: reject absolute or escaping paths at validate.

## 3. Things I would fix now (self-critique)

- **Config inconsistency**: `lab_id` in `~/.datahive/config.yaml` is `USB_MB` while the repo is `HiveBoard/USP_MB`. One of them is a typo; lab_id ends up in every row, so confirm the real value.
- **`platform_id` was removed from `init` but is still required** in `TrialAnnotation`, `TRIAL_COLUMNS` and `EpisodeHeader`. Confirm it is filled from the profile; otherwise exports contain blanks or fail.
- **UI changes outpaced tests**: the full suite had 1 failing test (`test_robot_profile_redesigned_ui`) that I have not yet investigated. A red test on the UI contract will hide real regressions.
- **Vocabulary churn without versioning** (see item 6). Every past rename is currently unrecoverable from old files.
- **Progressive disclosure hides mandatory fields until an outcome is chosen.** Good for speed, but an episode saved with an outcome and skipped optional fields is indistinguishable from "reviewed and left empty". oopsie distinguishes "absent" from "annotated and empty"; we should too.
- **No automated cross-check between annotation and data** (item 2). Today an annotator can say `success` on an episode that timed out and validation passes.

## 4. Suggested order

1. Week 1: items 1, 2, 3, 4 (quality gates) and the two self-critique fixes above.
2. Week 2: items 14-18 (annotator speed) and 5 (roster).
3. Week 3: items 6-8 (schema version, annotations in `.h5`, multi-annotator).
4. Then P2 as needed by lab onboarding.
