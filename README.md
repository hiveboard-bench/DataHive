# DataHive

`datahive-tools`: the client-side package a lab installs to collect,
validate, annotate, and upload HiveBoard manipulation episodes to its
private Hugging Face dataset repo.

> [!IMPORTANT]
> **Credits & Attribution**: All pipeline architecture, workflows, and core design in DataHive are directly adapted from and credited to [**Oopsie Data**](https://github.com/oopsie-data) (`oopsie-data` / `oopsie_data_tools`). Everything in DataHive — from the CLI workflow, robot profiles, and dataset indexing to the annotation GUI and Hugging Face Hub synchronization pipeline — was built upon the pipeline and concepts developed by the Oopsie Data team.

## Install

```
pip install -e .
```

## Quickstart

```
datahive init                 # one-time: writes ~/.datahive/config.yaml (0600)
datahive new-profile          # writes samples/robot_profile.yaml -- fill in ONCE
# ... record episodes into samples/{session}/episodes/{episode}.h5 + .mp4 ...
datahive check <episode_id>   # pre-annotation health check (frames, Hz, MP4s)
datahive annotate <episode_id>
datahive validate <episode_id>
datahive upload <episode_id>
datahive sync                 # reconcile all of samples/ with the Hub
datahive list
datahive delete <episode_id>
datahive interface --port 8000 # local web GUI, localhost only (alias: serve)
```

See `docs/MANUAL_SMOKE_TEST.md` for the manual (non-automated) end-to-end
check against a real, disposable Hugging Face repo.

## The sections of `datahive serve`

Opening the interface shows a start screen with two sections, plus an About and FAQ area. Both sections share the same `samples/` tree.

- **Runner** – runs HiveBoard trials. Create a session (operator and date; the lab ID comes from `datahive init` and the platform ID from the robot profile), generate the trial plan (5 trials for each chosen task, shuffled or ordered), then run each trial in one of two modes, switchable at any time:
  - **Manual**: 5 s countdown, stopwatch, sounds and automatic timeout in the browser (Space starts and stops), then record the outcome and **upload the HDF5 file and one video per camera in the robot profile**. The trial counts as valid only when all of them are uploaded and the episode passes validation.
  - **Automatic**: send the trial to your robot script (below). It records the episode, and you annotate it in the Annotate section, which opens on that episode.
- **Annotate** – review recorded episodes, annotate them, validate the data, and upload it to the Hub.

Robot side of automatic mode:

```python
from datahive.collect import CollectClient

robot = CollectClient("samples", base_url="http://127.0.0.1:8000")
while True:
    task = robot.wait_for_task()             # blocks until you press "Send to robot"
    with robot.new_writer(task) as writer:   # EpisodeWriter for that trial
        ...                                  # append_proprioception / append_command / attach_video
    robot.finish(writer)                     # hands the episode over, waits for the annotation
```

Trials are saved as rows of `samples/<session>/trials.csv`; all times are stored in UTC.

## Tests

```
pip install -e ".[dev]"
pytest
```

All tests run with no real network access and no real Hugging Face
credentials.

## Credits & Acknowledgments

All credit for the underlying pipeline architecture goes to the **Oopsie Data** (`oopsie-data` / `oopsie_data_tools`) project. DataHive was built on top of their pipeline patterns, including:

- **Interactive Annotation & GUI**: The web-based annotator interface, video playback controls, task picker, episode navigation, and failure classification.
- **Data Lifecycle**: The full `init` -> `new-profile` -> `annotate` -> `validate` -> `upload` -> `sync` pipeline.
- **Dataset Structure & Profiling**: Hardware platform specification snapshots, HDF5 episode storage, and Hub reconciliation.

We are deeply grateful to the Oopsie Data contributors for their pioneering work in robotic data pipelines.
