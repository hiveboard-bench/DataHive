"""Human-in-the-loop collection, modelled on oopsie-data's rollout annotator.

The browser (Collect view) and a robot script share a small state machine on
`datahive serve`:

    idle --submit--> pending --start--> running --annotating--> annotating --done--> idle

* The operator picks the next trial in the browser (submit).
* The robot script waits for it (CollectClient.wait_for_task), records the
  episode with EpisodeWriter, then reports it (finish).
* The browser shows the recorded episode for annotation; saving the annotation
  releases the robot script and returns to idle.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from datahive.errors import DatahiveError

STATUSES = ("idle", "pending", "running", "annotating")


class CollectError(DatahiveError):
    pass


class CollectRuntime:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = self._fresh()
        self._annotated: list[str] = []

    @staticmethod
    def _fresh() -> dict[str, Any]:
        return {"status": "idle", "task": None, "episode_id": None, "started_at": None,
                "_t0": None, "aborted": False, "seq": 0}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            s = {k: v for k, v in self._state.items() if not k.startswith("_")}
            s["elapsed_s"] = round(time.monotonic() - self._state["_t0"], 2) if self._state["_t0"] else None
            s["annotated_episode_ids"] = list(self._annotated)
            return s

    def submit(self, task: dict[str, Any]) -> None:
        with self._lock:
            if self._state["status"] != "idle":
                raise CollectError(f"Cannot queue a task while the collection is '{self._state['status']}'.")
            seq = self._state["seq"] + 1
            self._state = {**self._fresh(), "status": "pending", "task": task, "seq": seq}

    def start(self) -> None:
        with self._lock:
            if self._state["status"] != "pending":
                raise CollectError("No queued task to start.")
            self._state.update(status="running", _t0=time.monotonic(),
                               started_at=datetime.now(timezone.utc).isoformat())

    def annotating(self, episode_id: str) -> None:
        with self._lock:
            if self._state["status"] not in ("running", "pending"):
                raise CollectError(f"Cannot move to annotating from '{self._state['status']}'.")
            self._state.update(status="annotating", episode_id=episode_id, _t0=None)

    def mark_annotated(self, episode_id: str) -> bool:
        """Called when an annotation is saved. Releases the waiting robot script
        if it was for the episode being annotated."""
        with self._lock:
            if self._state["status"] != "annotating" or self._state["episode_id"] != episode_id:
                return False
            self._annotated = (self._annotated + [episode_id])[-50:]
            return True

    def done(self) -> None:
        """Returns to idle. A no-op unless annotating: the browser and the robot
        script may both post it, and a stale call must not disturb a newer task."""
        with self._lock:
            if self._state["status"] == "annotating":
                self._state = {**self._fresh(), "seq": self._state["seq"]}

    def abort(self) -> None:
        with self._lock:
            self._state = {**self._fresh(), "aborted": True, "seq": self._state["seq"]}


Request = Callable[[str, str, "dict | None"], Any]


class CollectClient:
    """Robot-side helper. Talks to a running `datahive serve` over HTTP."""

    def __init__(self, samples_root: Path | str, *, base_url: str = "http://127.0.0.1:8000",
                 request: Request | None = None, poll_s: float = 0.5) -> None:
        self.samples_root = Path(samples_root)
        self.base_url = base_url.rstrip("/")
        self.poll_s = poll_s
        self._request = request or self._http

    def _http(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def state(self) -> dict:
        return self._request("GET", "/api/collect/state", None)

    def wait_for_task(self, *, timeout_s: float | None = None) -> dict:
        """Blocks until the operator sends a trial, claims it and returns the task."""
        deadline = time.monotonic() + timeout_s if timeout_s else None
        while True:
            st = self.state()
            if st["status"] == "pending" and st.get("task"):
                self._request("POST", "/api/collect/start", {})
                return st["task"]
            if deadline and time.monotonic() > deadline:
                raise CollectError("Timed out waiting for a task from the browser.")
            time.sleep(self.poll_s)

    def new_writer(self, task: dict, *, episode_id: str | None = None, profile=None, policy: str | None = None):
        from datahive.episode import EpisodeWriter

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return EpisodeWriter(
            self.samples_root, task["session_id"], episode_id or f"t{task['trial_id']}_{stamp}",
            trial_id=str(task["trial_id"]), task_ids=[task["attachment_id"]], lab_id=task["lab_id"],
            profile=profile, policy=policy, collection_mode="automatic",
        )

    def finish(self, writer, *, wait_for_annotation: bool = True, timeout_s: float | None = None) -> bool:
        """Closes the episode, hands it to the browser for annotation and (by
        default) blocks until it is saved. Returns False if the operator aborted."""
        writer.close()
        episode_id = writer.episode_id
        self._request("POST", "/api/collect/annotating", {"episode_id": episode_id})
        if not wait_for_annotation:
            return True
        deadline = time.monotonic() + timeout_s if timeout_s else None
        try:
            while True:
                st = self.state()
                if episode_id in st["annotated_episode_ids"]:
                    return True
                if st.get("aborted") or st["status"] == "idle":
                    return False
                if deadline and time.monotonic() > deadline:
                    raise CollectError("Timed out waiting for the annotation.")
                time.sleep(self.poll_s)
        finally:
            self._request("POST", "/api/collect/done", {})
