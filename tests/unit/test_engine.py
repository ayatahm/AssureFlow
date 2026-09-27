"""Pure engine tests (spec 30.1). No database."""
import json
from pathlib import Path

import pytest

from apps.content.loader import file_to_engine_config
from apps.engine import engine

SEED = Path(__file__).resolve().parents[2] / "seed_demo" / "ruleset_demo.json"


@pytest.fixture(scope="module")
def cfg():
    return file_to_engine_config(json.loads(SEED.read_text(encoding="utf-8")))


def answers(**overrides):
    base = {"impact": "LOW", "value": "LOW", **{f"T{i}": "NO" for i in range(1, 9)}}
    base.update(overrides)
    return base


def test_engine_module_has_no_django_imports():
    source = Path(engine.__file__).read_text()
    assert "django" not in source.lower().replace("no django imports", "")


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({}, "PUBLIC"),
        ({"value": "MODERATE"}, "SENSITIVE"),
        ({"T2": "YES"}, "SENSITIVE"),
        ({"T1": "YES"}, "CONFIDENTIAL"),
        ({"impact": "HIGH"}, "CONFIDENTIAL"),
        ({"impact": "HIGH", "value": "HIGH", "T4": "YES"}, "SECRET"),
        ({"impact": "HIGH", "value": "MODERATE", "T4": "YES"}, "CONFIDENTIAL"),
    ],
)
def test_classification_combinations(cfg, overrides, expected):
    assert engine.evaluate(cfg, answers(**overrides)).classification == expected


def test_gate_skips_control_and_explains(cfg):
    result = engine.evaluate(cfg, answers(T1="NO"))
    dp = result.control("SYN-DP-01")
    assert dp.keep is False and dp.level is None and dp.asked is False
    assert "G-PD" in dp.reason and "T1 = NO" in dp.reason


def test_gate_passes_when_answer_yes(cfg):
    result = engine.evaluate(cfg, answers(T1="YES"))
    dp = result.control("SYN-DP-01")
    assert dp.keep and dp.level == "REQUIRED" and "G-PD" in dp.reason


def test_levels_follow_classification(cfg):
    public = engine.evaluate(cfg, answers())
    assert public.control("SYN-AC-01").level == "RECOMMENDED"
    sensitive = engine.evaluate(cfg, answers(value="MODERATE"))
    assert sensitive.control("SYN-AC-01").level == "REQUIRED"


def test_optional_controls_do_not_force_questions(cfg):
    result = engine.evaluate(cfg, answers())  # PUBLIC
    ac03 = result.control("SYN-AC-03")
    assert ac03.keep and ac03.level == "OPTIONAL"
    assert "AQ-03" not in result.question_codes
    assert ac03.asked is False


def test_always_ask_question_is_present(cfg):
    result = engine.evaluate(cfg, answers())
    assert result.question_codes[0] == "AQ-00"
    gv = result.control("SYN-GV-01")
    assert gv.level == "OPTIONAL" and gv.asked is True  # asked because the question is always asked


def test_shared_question_is_deduplicated_and_links_preserved(cfg):
    result = engine.evaluate(cfg, answers(impact="HIGH", value="HIGH", T4="YES"))  # SECRET: both LG controls REQUIRED
    assert result.question_codes.count("AQ-09") == 1
    q = next(q for q in result.questions if q.code == "AQ-09")
    assert set(q.driven_by) == {"SYN-LG-01", "SYN-LG-02"}
    assert result.control("SYN-LG-01").asked and result.control("SYN-LG-02").asked


def test_skipped_controls_ask_no_questions(cfg):
    result = engine.evaluate(cfg, answers(T3="NO"))
    assert "AQ-10" not in result.question_codes and "AQ-11" not in result.question_codes


def test_missing_answers_raise(cfg):
    partial = answers()
    del partial["T5"]
    del partial["impact"]
    with pytest.raises(engine.MissingAnswerError) as exc:
        engine.evaluate(cfg, partial)
    assert exc.value.missing == ["T5", "impact"]


def test_invalid_answer_raises(cfg):
    with pytest.raises(engine.InvalidAnswerError):
        engine.evaluate(cfg, answers(T1="MAYBE"))


def test_no_defaulting_of_blank_answers(cfg):
    with pytest.raises(engine.MissingAnswerError):
        engine.evaluate(cfg, answers(T2=""))


def test_rule_version_recorded(cfg):
    assert engine.evaluate(cfg, answers()).rule_version == "SYN-DEMO-1.0"


def test_unknown_gate_is_configuration_error(cfg):
    broken = dict(cfg, controls=[{"code": "X", "gates": ["NOPE"], "levels": {}, "questions": []}])
    with pytest.raises(engine.ConfigurationError):
        engine.evaluate(broken, answers())


def test_evaluation_is_deterministic(cfg):
    a = engine.evaluate(cfg, answers(T1="YES", T2="YES")).as_dict()
    b = engine.evaluate(cfg, answers(T2="YES", T1="YES")).as_dict()
    assert a == b
