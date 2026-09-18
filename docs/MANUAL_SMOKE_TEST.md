# Manual smoke test (real Hugging Face Hub)

This is **not automated** and must never run in CI — it makes real network
calls against a real, disposable Hugging Face dataset repo. Run it by hand
after any change that touches `datahive/hub.py` or the upload/sync/delete
paths, before releasing.

## Prerequisites

- A disposable private dataset repo, e.g. `sua-org/lab_test`, that you are
  OK deleting/recreating.
- A fine-grained Hugging Face token scoped to write access on that one repo.
- `pip install -e .` from the repo root.

## Steps

1. **Init.**
   ```
   datahive init --lab-id lab_test --repo-id sua-org/lab_test \
       --platform-id rig-01 --token <your token>
   ```
   Confirm it prints "Token verified with the Hugging Face Hub." and that
   `~/.datahive/config.yaml` is `0600`.

2. **Profile.**
   ```
   mkdir -p ~/dh-smoke && cd ~/dh-smoke
   datahive new-profile
   ```
   Fill in `samples/robot_profile.yaml` (at minimum `low_level.mode`,
   `manipulator.joint_names`, `cameras`).

3. **Build one real (or hand-built) episode** under
   `samples/session_001/episodes/ep_001.h5` (+ a matching
   `ep_001_cam_external.mp4`), using `datahive.EpisodeWriter` or your own
   recording code.

4. **Validate + annotate.**
   ```
   datahive annotate ep_001
   datahive validate ep_001
   ```

5. **Upload.**
   ```
   datahive upload ep_001
   ```
   Verify on the Hub web UI (`https://huggingface.co/datasets/sua-org/lab_test`)
   that `session_001/episodes/ep_001.h5`, the `.mp4`, and `trials.csv` are
   present.

6. **Sync shows uploaded.**
   ```
   datahive list
   ```
   Confirm `ep_001` shows `uploaded`.

7. **Remote-only detection.** From a second checkout (or after clearing the
   local index: `rm -rf samples/.datahive`), run:
   ```
   datahive sync
   ```
   Confirm `ep_001` is reported as `remote_only` and that nothing was
   downloaded.

8. **Delete.**
   ```
   datahive delete ep_001
   ```
   Confirm the files are gone from the Hub and `datahive list` no longer
   shows the episode.

9. **GUI parity.** Repeat steps 4–8 through `datahive serve` instead of the
   CLI, and confirm each change is immediately visible in `datahive list`
   in a separate terminal.

Clean up the `sua-org/lab_test` repo's contents when done.
