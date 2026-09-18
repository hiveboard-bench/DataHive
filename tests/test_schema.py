from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError as PydanticValidationError

from datahive.schema import TrialAnnotation


def base(**overrides):
    data = dict(
        trial_id="t1", lab_id="lab_test", platform_id="rig-01", attachment_id="peg_round",
        date=date.today().isoformat(), outcome="success", completion_time_s=10.0, strategy="prehensile",
    )
    data.update(overrides)
    return data


def test_success_requires_completion_time_and_forbids_failure_cause():
    TrialAnnotation.model_validate(base())
    with pytest.raises(PydanticValidationError):
        TrialAnnotation.model_validate(base(completion_time_s=None))
    with pytest.raises(PydanticValidationError):
        TrialAnnotation.model_validate(base(failure_cause="slip"))


@pytest.mark.parametrize("outcome", ["fail", "timeout", "safety_stop"])
def test_non_success_requires_failure_cause_and_forbids_completion_time(outcome):
    with pytest.raises(PydanticValidationError, match="failure_cause"):
        TrialAnnotation.model_validate(base(outcome=outcome, completion_time_s=None, failure_cause=None))
    with pytest.raises(PydanticValidationError, match="completion_time_s"):
        TrialAnnotation.model_validate(base(outcome=outcome, failure_cause="slip"))
    # Correct shape works.
    TrialAnnotation.model_validate(base(outcome=outcome, completion_time_s=None, failure_cause="slip"))


def test_stage_reached_forbidden_for_non_composed_assembly():
    with pytest.raises(PydanticValidationError, match="stage_reached"):
        TrialAnnotation.model_validate(base(stage_reached=1), context={"composed_assembly": False})
    TrialAnnotation.model_validate(base(stage_reached=None), context={"composed_assembly": False})


def test_stage_reached_required_for_composed_assembly():
    with pytest.raises(PydanticValidationError, match="stage_reached"):
        TrialAnnotation.model_validate(base(stage_reached=None), context={"composed_assembly": True})
    ann = TrialAnnotation.model_validate(base(stage_reached=0), context={"composed_assembly": True})
    assert ann.stage_reached == 0


def test_stage_reached_unconstrained_when_context_omitted():
    # Unknown attachment -> caller omits the context key entirely.
    TrialAnnotation.model_validate(base(stage_reached=None))
    TrialAnnotation.model_validate(base(stage_reached=2))


@pytest.mark.parametrize("field,value", [("outcome", "nope"), ("failure_cause", "nope"), ("strategy", "nope")])
def test_invalid_enum_values_rejected(field, value):
    data = base(outcome="fail", failure_cause="slip", completion_time_s=None)
    data[field] = value
    with pytest.raises(PydanticValidationError):
        TrialAnnotation.model_validate(data)


def test_csv_roundtrip():
    ann = TrialAnnotation.model_validate(base())
    row = ann.to_csv_row()
    assert row["failure_cause"] == ""
    ann2 = TrialAnnotation.from_csv_row(row)
    assert ann2.trial_id == ann.trial_id
    assert ann2.outcome == ann.outcome
    assert ann2.completion_time_s == ann.completion_time_s


def test_csv_roundtrip_failure_row():
    ann = TrialAnnotation.model_validate(base(outcome="fail", completion_time_s=None, failure_cause="perception"))
    row = ann.to_csv_row()
    assert row["completion_time_s"] == ""
    assert row["failure_cause"] == "perception"
    ann2 = TrialAnnotation.from_csv_row(row)
    assert ann2.failure_cause == ann.failure_cause


def test_severity_forbidden_on_success():
    with pytest.raises(PydanticValidationError, match="severity"):
        TrialAnnotation.model_validate(base(severity="minor"))


def test_severity_allowed_but_optional_on_failure():
    # Optional: a failed trial need not set severity...
    TrialAnnotation.model_validate(base(outcome="fail", completion_time_s=None, failure_cause="slip"))
    # ...but may.
    ann = TrialAnnotation.model_validate(
        base(outcome="fail", completion_time_s=None, failure_cause="slip", severity="critical")
    )
    assert ann.severity == "critical"


def test_invalid_severity_rejected():
    with pytest.raises(PydanticValidationError):
        TrialAnnotation.model_validate(
            base(outcome="fail", completion_time_s=None, failure_cause="slip", severity="nope")
        )


def test_operator_and_annotator_name_roundtrip():
    ann = TrialAnnotation.model_validate(base(operator_name="Alex", annotator_name="Sam"))
    row = ann.to_csv_row()
    assert row["operator_name"] == "Alex"
    assert row["annotator_name"] == "Sam"
    ann2 = TrialAnnotation.from_csv_row(row)
    assert ann2.operator_name == "Alex"
    assert ann2.annotator_name == "Sam"


def test_operator_and_annotator_name_default_blank():
    ann = TrialAnnotation.model_validate(base())
    assert ann.operator_name == ""
    assert ann.annotator_name == ""
