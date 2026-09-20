# Protocol and debugging

Source: `src/datahive/collect.py` (runtime + `CollectClient`) and `interface/api.py`
(`/api/collect/*`). The GUI and the robot script share one `CollectRuntime` inside
`datahive interface`; state is in memory, so restarting the interface resets it to `idle`.

## States

| status | meaning | who moves it |
|---|---|---|
| `idle` | nothing queued | initial; `done`, `abort` |
| `pending` | operator sent a trial, waiting for the robot | browser `submit` |
| `running` | robot claimed the trial and is recording | script `start` (inside `wait_for_task`) |
| `annotating` | episode handed over, operator is annotating | script `annotating` (inside `finish`) |

Transitions: `submit` (only from idle, else 409) → `start` (only from pending) →
`annotating` (from running or pending) → `done` (only acts when annotating) → idle.
`abort` from anywhere → idle with `aborted: true`.

State snapshot (`GET /api/collect/state`):

```json
{"status": "pending", "task": {...}, "episode_id": null, "started_at": null,
 "aborted": false, "seq": 3, "elapsed_s": null, "annotated_episode_ids": []}
```

`seq` increments per submitted trial. `annotated_episode_ids` holds the last 50 annotated
episodes; `finish()` waits for its episode id to appear there.

## Endpoints

| method | path | body | effect |
|---|---|---|---|
| GET | `/api/collect/state` | | snapshot |
| POST | `/api/collect/submit` | `{session_id, trial_id?, instruction?}` | queue the next unrecorded trial (or the given one). 422 if the plan is finished, 409 if busy, 404 if no session |
| POST | `/api/collect/start` | `{}` | pending → running (409 if nothing queued) |
| POST | `/api/collect/annotating` | `{episode_id}` | running → annotating |
| POST | `/api/collect/done` | `{}` | annotating → idle |
| POST | `/api/collect/abort` | `{}` | reset, mark aborted |

Debug with `curl -s localhost:8000/api/collect/state`. Do not drive these by hand in a real
collection; use `CollectClient`.

## `CollectClient`

| method | behaviour |
|---|---|
| `CollectClient(samples_root, base_url="http://127.0.0.1:8000", poll_s=0.5)` | `samples_root` is where `EpisodeWriter` writes |
| `state()` | GET state |
| `wait_for_task(timeout_s=None)` | polls until `pending` with a task, POSTs `start`, returns the task; raises `CollectError` on timeout |
| `new_writer(task, episode_id=None, profile=None, policy=None)` | `EpisodeWriter` with `trial_id`, `task_ids=[attachment_id]`, `lab_id` from the task and `collection_mode="automatic"` |
| `finish(writer, wait_for_annotation=True, timeout_s=None)` | closes the writer, POSTs `annotating`, then waits until the episode is annotated (True) or the operator aborted / status is idle (False); always POSTs `done` at the end |

HTTP timeout is 10 s per request. A refused connection raises `URLError`: the interface is not
running, or the port differs.

## Symptoms

| symptom | cause |
|---|---|
| `wait_for_task` never returns | Nothing sent: check the trial screen is in *Automatic* mode and "Send to robot" was pressed; check `state()`; wrong `--port`/`base_url` |
| `URLError: Connection refused` | `datahive interface` not running, or on a different host/port (it listens on 127.0.0.1 only) |
| Browser says a trial is running forever | Script died mid-trial; press **Abort** |
| 409 "Cannot queue a task while the collection is 'X'" | Previous trial still `running`/`annotating`; finish it or **Abort** |
| Episode not visible in Annotate | Script wrote under a different `samples/` than the interface serves; compare `samples_root` and `--samples` |
| `finish()` returns immediately with `False` | Operator pressed Abort, or the interface restarted (state reset to idle) |
| `Camera 'X' ... no video` after recording | `attach_video` name is not in the profile's `cameras`, or the file name is not the profile name |
| `check` fails on rate/gaps | Recording loop too slow or blocking; see `adapting.md` Timing |

## Version notes

Automatic mode was modelled on oopsie-data's human-in-the-loop rollout annotator. The
protocol above is DataHive's own; do not assume oopsie's client API.
