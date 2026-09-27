"""Load versioned RuleSets / Technical Catalogues from JSON files (spec 21.2).

Loading is idempotent: the same version with the same source hash is a no-op;
the same version with different bytes is refused (versions are immutable).
Non-synthetic (private) content is refused in the password-only stage.
"""
import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.db import transaction

from apps.audit.services import record
from apps.core.stage import assert_private_content_allowed

from .models import (
    AssessmentQuestion,
    ComplianceGuidance,
    ControlRequirement,
    FrameworkReference,
    GateRule,
    RequirementQuestion,
    RuleSet,
    ScopeTag,
    TechnicalCatalogue,
    TechnicalControl,
    TriageQuestion,
)


class ContentLoadError(Exception):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_source(path):
    raw = Path(path).read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContentLoadError(f"{path} is not valid UTF-8 JSON: {exc}") from exc
    return raw, data


def file_to_engine_config(data):
    """Engine configuration straight from a rule-set source dict (used by tools/tests)."""
    return {
        "version": data["version"],
        "triage_questions": [
            {"code": q["code"], "kind": q["kind"], "options": q.get("options", [])} for q in data["triage_questions"]
        ],
        "classification": data["classification"],
        "routing": data.get("routing", []),
        "gates": [{"code": g["code"], "skip_when": g["skip_when"], "note": g.get("note", "")} for g in data["gates"]],
        "controls": [
            {"code": c["code"], "gates": c.get("gates", []), "levels": c["levels"], "questions": c.get("questions", [])}
            for c in data["controls"]
        ],
        "always_ask": [q["code"] for q in data["assessment_questions"] if q.get("always_ask")],
        "question_order": [q["code"] for q in data["assessment_questions"]],
    }


def build_engine_config(rule_set: RuleSet):
    """Engine configuration from a loaded (pinned) RuleSet."""
    controls = []
    for c in rule_set.controls.prefetch_related("questions").order_by("order", "code"):
        controls.append(
            {
                "code": c.code,
                "gates": c.applicability.get("gates", []),
                "levels": c.levels,
                "questions": [q.code for q in c.questions.order_by("order", "code")],
            }
        )
    questions = list(rule_set.assessment_questions.order_by("order", "code"))
    return {
        "version": rule_set.version,
        "triage_questions": [
            {"code": q.code, "kind": q.kind, "options": q.options} for q in rule_set.triage_questions.all()
        ],
        "classification": rule_set.classification_config,
        "routing": rule_set.routing_config,
        "gates": [
            {"code": g.code, "skip_when": g.condition["skip_when"], "note": g.source_note} for g in rule_set.gates.all()
        ],
        "controls": controls,
        "always_ask": [q.code for q in questions if q.always_ask],
        "question_order": [q.code for q in questions],
    }


def _guard_private(data):
    if not data.get("synthetic"):
        assert_private_content_allowed(settings)


@transaction.atomic
def load_ruleset(path, *, actor=None, activate=True, baseline_path=None):
    raw, data = read_source(path)
    if data.get("format") != "assureflow-ruleset-v1":
        raise ContentLoadError("Unsupported rule-set format.")
    _guard_private(data)
    digest = sha256_bytes(raw)
    baseline_digest = sha256_bytes(Path(baseline_path).read_bytes()) if baseline_path else ""

    existing = RuleSet.objects.filter(version=data["version"]).first()
    if existing:
        if existing.source_sha256 != digest:
            raise ContentLoadError(
                f"RuleSet {data['version']} already loaded with different content; publish a new version."
            )
        if activate and not existing.is_active:
            _activate(RuleSet, existing, actor)
        return existing, False

    rs = RuleSet.objects.create(
        version=data["version"],
        label=data.get("label", data["version"]),
        source_sha256=digest,
        baseline_sha256=baseline_digest,
        loaded_by=actor,
        is_synthetic=bool(data.get("synthetic")),
        notes=data.get("notes", ""),
        classification_config=data["classification"],
        routing_config=data.get("routing", []),
    )
    for i, q in enumerate(data["triage_questions"]):
        TriageQuestion.objects.create(
            rule_set=rs, code=q["code"], text=q["text"], help_text=q.get("help_text", ""),
            kind=q["kind"], options=q.get("options", []), order=i,
        )
    for g in data["gates"]:
        GateRule.objects.create(
            rule_set=rs, code=g["code"], question_code=g.get("question", ""),
            condition={"skip_when": g["skip_when"]}, source_note=g.get("note", ""),
        )
    questions = {}
    for i, q in enumerate(data["assessment_questions"]):
        questions[q["code"]] = AssessmentQuestion.objects.create(
            rule_set=rs, code=q["code"], section=q["section"], text=q["text"],
            help_text=q.get("help_text", ""), order=i, always_ask=bool(q.get("always_ask")),
        )
    for i, c in enumerate(data["controls"]):
        control = ControlRequirement.objects.create(
            rule_set=rs, code=c["code"], title=c["title"], description=c.get("description", ""),
            domain=c.get("domain", ""), order=i, applicability={"gates": c.get("gates", [])}, levels=c["levels"],
        )
        for qcode in c.get("questions", []):
            if qcode not in questions:
                raise ContentLoadError(f"Control {c['code']} links unknown question {qcode}")
            RequirementQuestion.objects.create(requirement=control, question=questions[qcode])
        for ref in c.get("framework_refs", []):
            FrameworkReference.objects.create(
                requirement=control, framework=ref["framework"], reference=ref["reference"],
                description=ref.get("description", ""), mapping_version=ref.get("mapping_version", ""),
                edition=ref.get("edition") or "Not specified", source_note=ref.get("source_note", ""),
            )
    for g in data.get("compliance_guidance", []):
        ComplianceGuidance.objects.create(
            rule_set=rs, requirement_level=g["level"], decision_key=g["decision"], guidance_text=g["text"]
        )

    config = build_engine_config(rs)
    for c in config["controls"]:
        for gate in c["gates"]:
            if gate not in {g["code"] for g in config["gates"]}:
                raise ContentLoadError(f"Control {c['code']} references unknown gate {gate}")

    record("content.ruleset_loaded", actor=actor, obj=rs, detail={"version": rs.version, "synthetic": rs.is_synthetic})
    if activate:
        _activate(RuleSet, rs, actor)
    return rs, True


@transaction.atomic
def load_catalogue(path, *, actor=None, activate=True):
    raw, data = read_source(path)
    if data.get("format") != "assureflow-catalogue-v1":
        raise ContentLoadError("Unsupported catalogue format.")
    _guard_private(data)
    digest = sha256_bytes(raw)

    for i, t in enumerate(data.get("scope_tags", [])):
        ScopeTag.objects.update_or_create(code=t["code"], defaults={"label": t["label"], "order": i})

    existing = TechnicalCatalogue.objects.filter(version=data["version"]).first()
    if existing:
        if existing.source_sha256 != digest:
            raise ContentLoadError(
                f"Catalogue {data['version']} already loaded with different content; publish a new version."
            )
        if activate and not existing.is_active:
            _activate(TechnicalCatalogue, existing, actor)
        return existing, False

    cat = TechnicalCatalogue.objects.create(
        version=data["version"], label=data.get("label", data["version"]), source_sha256=digest,
        loaded_by=actor, is_synthetic=bool(data.get("synthetic")), notes=data.get("notes", ""),
    )
    tags = {t.code: t for t in ScopeTag.objects.all()}
    for c in data["controls"]:
        tc = TechnicalControl.objects.create(
            catalogue=cat, source_id=c["source_id"], raw_category=c.get("category", ""),
            risk_description=c.get("risk", ""), control_name=c["control_name"],
            control_description=c.get("description", ""), testing_process=c.get("testing", ""),
        )
        unknown = [t for t in c.get("tags", []) if t not in tags]
        if unknown:
            raise ContentLoadError(f"{c['source_id']} references unknown scope tags {unknown}")
        tc.tags.set([tags[t] for t in c.get("tags", [])])
    record("content.catalogue_loaded", actor=actor, obj=cat, detail={"version": cat.version, "synthetic": cat.is_synthetic})
    if activate:
        _activate(TechnicalCatalogue, cat, actor)
    return cat, True


def _activate(model, obj, actor):
    model.objects.filter(is_active=True).exclude(pk=obj.pk).update(is_active=False)
    obj.is_active = True
    obj.save(update_fields=["is_active"])
    record(f"content.{model.__name__.lower()}_activated", actor=actor, obj=obj, detail={"version": obj.version})
