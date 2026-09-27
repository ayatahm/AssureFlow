"""Pure-Python applicability engine (spec 7.2). No Django imports.

``evaluate(rule_set, answers)`` takes a plain configuration dict and a dict of
triage answers and returns an ``EngineResult``. All organisation-specific logic
is configuration; this module only interprets it.

Configuration shape::

    {
      "version": "SYN-DEMO-1.0",
      "triage_questions": [{"code": "impact", "kind": "LEVEL", "options": [...]},
                           {"code": "T1", "kind": "YES_NO"}],
      "classification": {"default": "PUBLIC",
                         "rules": [{"when": <cond>, "result": "SECRET", "reason": "..."}]},
      "routing": [{"code": "third_party_review", "label": "...", "when": <cond>}],
      "gates": [{"code": "G-PD", "skip_when": <cond>, "note": "..."}],
      "controls": [{"code": "C-01", "gates": ["G-PD"],
                    "levels": {"PUBLIC": "OPTIONAL", ...}, "questions": ["AQ-01"]}],
      "always_ask": ["AQ-00"],
      "question_order": ["AQ-00", "AQ-01", ...]
    }

Conditions (``<cond>``)::

    {"q": "T1", "eq": "YES"}           answer equals value
    {"q": "impact", "in": ["HIGH"]}    answer in list
    {"all": [<cond>, ...]}             every sub-condition
    {"any": [<cond>, ...]}             at least one
    {"not": <cond>}                    negation
"""
from dataclasses import dataclass, field

CLASSIFICATIONS = ("PUBLIC", "SENSITIVE", "CONFIDENTIAL", "SECRET")
LEVELS = ("REQUIRED", "RECOMMENDED", "OPTIONAL")
QUESTION_DRIVING_LEVELS = ("REQUIRED", "RECOMMENDED")
YES_NO = ("YES", "NO")


class EngineError(Exception):
    pass


class MissingAnswerError(EngineError):
    def __init__(self, missing):
        self.missing = sorted(missing)
        super().__init__(f"Missing triage answers: {', '.join(self.missing)}")


class InvalidAnswerError(EngineError):
    pass


class ConfigurationError(EngineError):
    pass


@dataclass(frozen=True)
class ControlResult:
    code: str
    keep: bool
    level: str | None
    drives_questions: bool
    questions: tuple
    asked: bool
    reason: str


@dataclass(frozen=True)
class QuestionSelection:
    code: str
    driven_by: tuple
    always_ask: bool
    reason: str


@dataclass
class EngineResult:
    rule_version: str
    classification: str
    classification_reason: str
    routing: dict
    controls: list = field(default_factory=list)
    questions: list = field(default_factory=list)

    def control(self, code):
        for c in self.controls:
            if c.code == code:
                return c
        raise KeyError(code)

    @property
    def question_codes(self):
        return [q.code for q in self.questions]

    def as_dict(self):
        return {
            "rule_version": self.rule_version,
            "classification": self.classification,
            "routing": dict(sorted(self.routing.items())),
            "controls": {
                c.code: {"keep": c.keep, "level": c.level, "asked": c.asked} for c in self.controls
            },
            "questions": self.question_codes,
        }


def _eval(cond, answers):
    if not isinstance(cond, dict) or not cond:
        raise ConfigurationError(f"Invalid condition: {cond!r}")
    if "all" in cond:
        return all(_eval(c, answers) for c in cond["all"])
    if "any" in cond:
        return any(_eval(c, answers) for c in cond["any"])
    if "not" in cond:
        return not _eval(cond["not"], answers)
    if "q" in cond:
        code = cond["q"]
        if code not in answers:
            raise ConfigurationError(f"Condition references unknown triage question {code!r}")
        value = answers[code]
        if "eq" in cond:
            return value == cond["eq"]
        if "in" in cond:
            return value in cond["in"]
    raise ConfigurationError(f"Invalid condition: {cond!r}")


def describe(cond):
    """Human-readable rendering of a condition, used in explanations."""
    if "all" in cond:
        return " and ".join(describe(c) for c in cond["all"])
    if "any" in cond:
        return "(" + " or ".join(describe(c) for c in cond["any"]) + ")"
    if "not" in cond:
        return f"not ({describe(cond['not'])})"
    if "eq" in cond:
        return f"{cond['q']} = {cond['eq']}"
    return f"{cond['q']} in {'/'.join(cond['in'])}"


def validate_answers(rule_set, answers):
    missing = []
    for q in rule_set["triage_questions"]:
        code = q["code"]
        value = answers.get(code)
        if value in (None, ""):
            missing.append(code)
            continue
        allowed = YES_NO if q["kind"] == "YES_NO" else tuple(q.get("options", ()))
        if value not in allowed:
            raise InvalidAnswerError(f"Invalid answer {value!r} for {code}; expected one of {allowed}")
    if missing:
        raise MissingAnswerError(missing)


def classify(rule_set, answers):
    cfg = rule_set["classification"]
    for rule in cfg.get("rules", []):
        if _eval(rule["when"], answers):
            result = rule["result"]
            if result not in CLASSIFICATIONS:
                raise ConfigurationError(f"Unknown classification {result!r}")
            reason = rule.get("reason") or f"Matched rule: {describe(rule['when'])}"
            return result, reason
    default = cfg.get("default", "PUBLIC")
    return default, "No classification rule matched; default classification applied."


def evaluate(rule_set, answers):
    validate_answers(rule_set, answers)
    answers = {q["code"]: answers[q["code"]] for q in rule_set["triage_questions"]}

    classification, classification_reason = classify(rule_set, answers)
    routing = {r["code"]: bool(_eval(r["when"], answers)) for r in rule_set.get("routing", [])}
    gates = {g["code"]: g for g in rule_set.get("gates", [])}

    # Pass 1: applicability and level per control.
    partial = []
    driven = {}
    for control in rule_set["controls"]:
        code = control["code"]
        skipped_by = None
        passed = []
        for gate_code in control.get("gates", []):
            gate = gates.get(gate_code)
            if gate is None:
                raise ConfigurationError(f"Control {code} references unknown gate {gate_code}")
            if _eval(gate["skip_when"], answers):
                skipped_by = gate
                break
            passed.append(gate_code)
        questions = tuple(control.get("questions", ()))
        if skipped_by:
            reason = f"Skipped by gate {skipped_by['code']}: {describe(skipped_by['skip_when'])}."
            if skipped_by.get("note"):
                reason += f" {skipped_by['note']}"
            partial.append((code, False, None, False, questions, reason))
            continue
        level = control["levels"].get(classification)
        if level not in LEVELS:
            raise ConfigurationError(f"Control {code} has no valid level for {classification}")
        gate_text = f"gates passed ({', '.join(passed)})" if passed else "no applicability gate"
        reason = f"Kept: {gate_text}. Level {level} for classification {classification}."
        drives = level in QUESTION_DRIVING_LEVELS
        if drives:
            for q in questions:
                driven.setdefault(q, []).append(code)
        partial.append((code, True, level, drives, questions, reason))

    # Always-ask questions.
    always = list(rule_set.get("always_ask", []))
    selected = set(driven) | set(always)
    order = rule_set.get("question_order") or sorted(selected)
    unknown = selected - set(order)
    if unknown:
        raise ConfigurationError(f"Questions missing from question_order: {sorted(unknown)}")

    questions = []
    for q in order:
        if q not in selected:
            continue
        drivers = tuple(driven.get(q, ()))
        if drivers:
            reason = "Asked for applicable control(s) " + ", ".join(drivers) + "."
        else:
            reason = "Always asked for every assessment."
        questions.append(QuestionSelection(code=q, driven_by=drivers, always_ask=q in always, reason=reason))

    asked_codes = {q.code for q in questions}
    controls = [
        ControlResult(
            code=code,
            keep=keep,
            level=level,
            drives_questions=drives,
            questions=qs,
            asked=keep and any(q in asked_codes for q in qs),
            reason=reason,
        )
        for code, keep, level, drives, qs, reason in partial
    ]

    return EngineResult(
        rule_version=rule_set.get("version", ""),
        classification=classification,
        classification_reason=classification_reason,
        routing=routing,
        controls=controls,
        questions=questions,
    )


def preview(rule_set, answers):
    """Classification/routing preview on partial answers; returns None values where undecidable."""
    try:
        validate_answers(rule_set, answers)
    except MissingAnswerError:
        return None
    classification, reason = classify(rule_set, answers)
    routing = {r["code"]: bool(_eval(r["when"], answers)) for r in rule_set.get("routing", [])}
    return {"classification": classification, "reason": reason, "routing": routing}
