import tempfile
import unittest
from pathlib import Path

from ..annotations import (
    ASSUMED_NOTE,
    AnnotationError,
    AnnotationPolicy,
    fields_for,
    load_annotations,
)
from .fixtures import annotation_csv


def policy(**overrides):
    defaults = dict(
        attachment_id="valve_ball",
        annotator_name="ana",
        operator_name="ana",
        strategy="prehensile",
    )
    defaults.update(overrides)
    return AnnotationPolicy(**defaults)


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_loads_valid_rows(self):
        path = annotation_csv(
            self.tmp / "a.csv",
            [
                {"episode_index": 0, "outcome": "success", "strategy": "prehensile"},
                {"episode_index": 1, "outcome": "fail", "strategy": "prehensile",
                 "failure_cause": "slip", "severity": "minor"},
            ],
        )
        rows = load_annotations(path)
        self.assertEqual(rows[0].outcome, "success")
        self.assertEqual(rows[1].failure_cause, "slip")

    def test_rejects_duplicate_episode(self):
        path = annotation_csv(
            self.tmp / "a.csv",
            [
                {"episode_index": 0, "outcome": "success", "strategy": "prehensile"},
                {"episode_index": 0, "outcome": "success", "strategy": "prehensile"},
            ],
        )
        with self.assertRaisesRegex(AnnotationError, "duplicate"):
            load_annotations(path)

    def test_success_must_not_carry_failure_fields(self):
        path = annotation_csv(
            self.tmp / "a.csv",
            [{"episode_index": 0, "outcome": "success", "strategy": "prehensile", "failure_cause": "slip"}],
        )
        with self.assertRaisesRegex(AnnotationError, "must not carry"):
            load_annotations(path)

    def test_failure_requires_cause(self):
        path = annotation_csv(
            self.tmp / "a.csv",
            [{"episode_index": 0, "outcome": "fail", "strategy": "prehensile"}],
        )
        with self.assertRaisesRegex(AnnotationError, "requires failure_cause"):
            load_annotations(path)

    def test_other_cause_requires_detail(self):
        path = annotation_csv(
            self.tmp / "a.csv",
            [{"episode_index": 0, "outcome": "fail", "strategy": "prehensile", "failure_cause": "other"}],
        )
        with self.assertRaisesRegex(AnnotationError, "requires failure_cause_detail"):
            load_annotations(path)

    def test_rows_with_empty_outcome_are_treated_as_unannotated(self):
        path = annotation_csv(
            self.tmp / "a.csv",
            [
                {"episode_index": 0, "outcome": "", "strategy": "prehensile"},
                {"episode_index": 1, "outcome": "success", "strategy": "prehensile"},
            ],
        )
        rows = load_annotations(path, default_strategy="prehensile")
        self.assertEqual(sorted(rows), [1])

    def test_missing_strategy_falls_back_to_cli_default(self):
        path = annotation_csv(
            self.tmp / "a.csv",
            [{"episode_index": 0, "outcome": "success"}],
        )
        self.assertEqual(load_annotations(path, default_strategy="prehensile")[0].strategy, "prehensile")

    def test_missing_strategy_without_default_is_rejected(self):
        path = annotation_csv(self.tmp / "a.csv", [{"episode_index": 0, "outcome": "success"}])
        with self.assertRaisesRegex(AnnotationError, "strategy is empty"):
            load_annotations(path)

    def test_unknown_outcome_rejected(self):
        path = annotation_csv(
            self.tmp / "a.csv",
            [{"episode_index": 0, "outcome": "partial", "strategy": "prehensile"}],
        )
        with self.assertRaisesRegex(AnnotationError, "outcome"):
            load_annotations(path)

    def test_assume_success_records_the_assumption(self):
        annotation = policy(assume_success=True).resolve(7)
        self.assertTrue(annotation.assumed)
        self.assertEqual(annotation.outcome, "success")
        self.assertEqual(annotation.notes, ASSUMED_NOTE)
        self.assertIn("assumed", ASSUMED_NOTE)
        self.assertIn("Verify", ASSUMED_NOTE)

    def test_composed_attachment_requires_stage_reached(self):
        with self.assertRaisesRegex(AnnotationError, "stage_reached"):
            policy(attachment_id="button", assume_success=True)

    def test_stage_reached_forbidden_for_simple_attachment(self):
        with self.assertRaisesRegex(AnnotationError, "only valid for composed"):
            policy(stage_reached=2)

    def test_composed_success_needs_stage_in_fields(self):
        composed = policy(attachment_id="button", assume_success=True, stage_reached=2)
        annotation = composed.resolve(0)
        fields = fields_for(annotation, policy=composed)
        self.assertEqual(fields["stage_reached"], 2)
        self.assertEqual(fields["outcome"], "success")
        self.assertIn("assumed", fields["notes"])

    def test_missing_row_without_assumption_is_an_error(self):
        with self.assertRaisesRegex(AnnotationError, "no annotation row"):
            policy().resolve(3)


if __name__ == "__main__":
    unittest.main()
