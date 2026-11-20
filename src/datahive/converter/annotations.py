"""Trial annotations: sidecar CSV, or an explicit assumption.

``datahive_episode_v1`` has no "unlabelled" state -- every episode needs a
trials row -- and the format's own guidance is that annotation is human
judgement. Third-party datasets often ship without outcomes, so the converter
refuses to guess unless it is told to, and records the assumption in the row.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from datahive.annotate import is_composed_assembly

OUTCOMES = ("success", "fail", "timeout", "safety_stop")
FAILURE_CAUSES = (
    "grasp_geometry",
    "kinematic_limit",
    "perception",
    "slip",
    "force_limit",
    "control_precision",
    "other",
)
SEVERITIES = ("minor", "moderate", "critical")
STRATEGIES = ("prehensile", "non_prehensile")

ASSUMED_NOTE = (
    "[conversion] outcome=success assumed: no annotation sidecar was supplied "
    "and --assume-success was set. Verify labels before submission."
)

KNOWN_COLUMNS = {
    "episode_index",
    "outcome",
    "strategy",
    "stage_reached",
    "failure_cause",
    "failure_cause_detail",
    "severity",
    "n_attempts",
    "n_regrasps",
    "notes",
}


class AnnotationError(ValueError):
    pass


@dataclass
class Annotation:
    outcome: str
    strategy: str
    stage_reached: int | None = None
    failure_cause: str | None = None
    failure_cause_detail: str = ""
    severity: str | None = None
    n_attempts: int | None = None
    n_regrasps: int | None = None
    notes: str = ""
    assumed: bool = False


@dataclass
class AnnotationPolicy:
    """Resolves the annotation for one source episode index."""

    attachment_id: str
    annotator_name: str
    operator_name: str
    strategy: str
    annotations: dict[int, Annotation] = field(default_factory=dict)
    assume_success: bool = False
    stage_reached: int | None = None
    composed: bool | None = None

    def __post_init__(self) -> None:
        if self.strategy not in STRATEGIES:
            raise AnnotationError(f"--strategy must be one of {STRATEGIES}, got {self.strategy!r}")
        self.composed = is_composed_assembly(self.attachment_id)
        if self.composed:
            if not self.annotations and self.assume_success and self.stage_reached is None:
                raise AnnotationError(
                    f"attachment {self.attachment_id!r} is a composed assembly, so stage_reached is "
                    "required. Pass --stage-reached with the final stage number."
                )
            missing = sorted(i for i, a in self.annotations.items() if a.stage_reached is None)
            if missing:
                raise AnnotationError(
                    f"attachment {self.attachment_id!r} is a composed assembly; every row needs "
                    f"stage_reached. Missing for source episodes: {missing[:10]}"
                )
        else:
            if self.stage_reached is not None:
                raise AnnotationError(
                    f"--stage-reached is only valid for composed assemblies; {self.attachment_id!r} is not one."
                )
            extra = sorted(i for i, a in self.annotations.items() if a.stage_reached is not None)
            if extra:
                raise AnnotationError(
                    f"attachment {self.attachment_id!r} is not a composed assembly; stage_reached is "
                    f"forbidden. Set for source episodes: {extra[:10]}"
                )

    def resolve(self, index: int) -> Annotation:
        annotation = self.annotations.get(index)
        if annotation is None:
            if not self.assume_success:
                raise AnnotationError(
                    f"no annotation row for source episode {index} and --assume-success was not passed"
                )
            annotation = Annotation(
                outcome="success",
                strategy=self.strategy,
                stage_reached=self.stage_reached,
                notes=ASSUMED_NOTE,
                assumed=True,
            )
        return annotation


def load_annotations(path: Path, *, default_strategy: str | None = None) -> dict[int, Annotation]:
    path = Path(path)
    if not path.exists():
        raise AnnotationError(
            f"annotation file not found: {path}\n"
            "  Create a sheet with `datahive convert-annotations-template --source <dataset> "
            f"--output {path}`, fill in the outcome column, then re-run with --annotations {path}.\n"
            "  If you have no labels yet, omit --annotations and pass --assume-success."
        )
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = {"episode_index", "outcome"} - set(reader.fieldnames or ())
        if missing:
            raise AnnotationError(f"{path.name} is missing required column(s): {sorted(missing)}")
        unknown = {c for c in (reader.fieldnames or ()) if c and c not in KNOWN_COLUMNS}
        if unknown:
            raise AnnotationError(f"{path.name} has unknown column(s): {sorted(unknown)}")
        annotations: dict[int, Annotation] = {}
        for line_no, row in enumerate(reader, 2):
            index_text = (row.get("episode_index") or "").strip()
            if not index_text:
                raise AnnotationError(f"{path.name}:{line_no}: episode_index is empty")
            try:
                index = int(index_text)
            except ValueError as err:
                raise AnnotationError(f"{path.name}:{line_no}: episode_index {index_text!r} is not an integer") from err
            if not (row.get("outcome") or "").strip():
                # A row from annotations-template that has not been filled in yet.
                # Treated as unannotated; the selection then decides whether that is fatal.
                continue
            if index in annotations:
                raise AnnotationError(f"{path.name}:{line_no}: duplicate episode_index {index}")
            annotations[index] = _annotation_from_row(
                row, f"{path.name}:{line_no}", default_strategy=default_strategy
            )
    return annotations


def _annotation_from_row(
    row: dict[str, str], where: str, *, default_strategy: str | None = None
) -> Annotation:
    def text(name: str) -> str:
        return (row.get(name) or "").strip()

    def integer(name: str) -> int | None:
        raw = text(name)
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError as err:
            raise AnnotationError(f"{where}: {name} {raw!r} is not an integer") from err
        if value < 0:
            raise AnnotationError(f"{where}: {name} must be >= 0")
        return value

    outcome = text("outcome")
    if outcome not in OUTCOMES:
        raise AnnotationError(f"{where}: outcome {outcome!r} is not one of {OUTCOMES}")
    strategy = text("strategy") or (default_strategy or "")
    if not strategy:
        raise AnnotationError(f"{where}: strategy is empty and no --strategy default was given")
    if strategy not in STRATEGIES:
        raise AnnotationError(f"{where}: strategy {strategy!r} is not one of {STRATEGIES}")
    failure_cause = text("failure_cause") or None
    severity = text("severity") or None
    detail = text("failure_cause_detail")
    if failure_cause and failure_cause not in FAILURE_CAUSES:
        raise AnnotationError(f"{where}: failure_cause {failure_cause!r} is not one of {FAILURE_CAUSES}")
    if severity and severity not in SEVERITIES:
        raise AnnotationError(f"{where}: severity {severity!r} is not one of {SEVERITIES}")

    if outcome == "success":
        if failure_cause or severity or detail:
            raise AnnotationError(f"{where}: success must not carry failure_cause, severity or detail")
    else:
        if not failure_cause:
            raise AnnotationError(f"{where}: outcome {outcome!r} requires failure_cause")
        if failure_cause == "other" and not detail:
            raise AnnotationError(f"{where}: failure_cause 'other' requires failure_cause_detail")

    return Annotation(
        outcome=outcome,
        strategy=strategy,
        stage_reached=integer("stage_reached"),
        failure_cause=failure_cause,
        failure_cause_detail=detail,
        severity=severity,
        n_attempts=integer("n_attempts"),
        n_regrasps=integer("n_regrasps"),
        notes=text("notes"),
    )


def fields_for(annotation: Annotation, *, policy: AnnotationPolicy) -> dict[str, Any]:
    """The ``fields`` dict ``datahive.annotate_episode`` expects."""
    if annotation.stage_reached is not None and not policy.composed:
        raise AnnotationError(
            f"stage_reached is set but attachment {policy.attachment_id!r} is not a composed assembly"
        )
    if policy.composed and annotation.outcome == "success" and annotation.stage_reached is None:
        raise AnnotationError(
            f"attachment {policy.attachment_id!r} is composed; a successful trial needs stage_reached"
        )
    fields: dict[str, Any] = {
        "attachment_id": policy.attachment_id,
        "operator_name": policy.operator_name,
        "annotator_name": policy.annotator_name,
        "outcome": annotation.outcome,
        "strategy": annotation.strategy,
        "notes": annotation.notes,
    }
    if annotation.stage_reached is not None:
        fields["stage_reached"] = annotation.stage_reached
    if annotation.failure_cause:
        fields["failure_cause"] = annotation.failure_cause
    if annotation.failure_cause_detail:
        fields["failure_cause_detail"] = annotation.failure_cause_detail
    if annotation.severity:
        fields["severity"] = annotation.severity
    if annotation.n_attempts is not None:
        fields["n_attempts"] = annotation.n_attempts
    if annotation.n_regrasps is not None:
        fields["n_regrasps"] = annotation.n_regrasps
    return fields
