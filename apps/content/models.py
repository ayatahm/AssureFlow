"""Versioned assessment content (spec 21.2).

Rule sets and technical catalogues are loaded by management commands from
JSON files. Only synthetic demo content lives in Git; approved private content
is loaded from ``private_data/`` on an approved machine.
"""
from django.conf import settings
from django.db import models


class Classification(models.TextChoices):
    PUBLIC = "PUBLIC", "Public"
    SENSITIVE = "SENSITIVE", "Sensitive"
    CONFIDENTIAL = "CONFIDENTIAL", "Confidential"
    SECRET = "SECRET", "Secret"


class RequirementLevel(models.TextChoices):
    REQUIRED = "REQUIRED", "Required"
    RECOMMENDED = "RECOMMENDED", "Recommended"
    OPTIONAL = "OPTIONAL", "Optional"


class RuleSet(models.Model):
    version = models.CharField(max_length=64, unique=True)
    label = models.CharField(max_length=200)
    source_sha256 = models.CharField(max_length=64)
    baseline_sha256 = models.CharField(max_length=64, blank=True)
    loaded_at = models.DateTimeField(auto_now_add=True)
    loaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_active = models.BooleanField(default=False)
    is_synthetic = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    # Engine configuration that is not naturally row-shaped (see engine/engine.py).
    classification_config = models.JSONField(default=dict)
    routing_config = models.JSONField(default=list)

    class Meta:
        ordering = ["-loaded_at"]
        constraints = [
            models.UniqueConstraint(fields=["is_active"], condition=models.Q(is_active=True), name="one_active_ruleset"),
        ]

    def __str__(self):
        return f"RuleSet {self.version}"


class TriageQuestion(models.Model):
    class Kind(models.TextChoices):
        LEVEL = "LEVEL", "Level"
        YES_NO = "YES_NO", "Yes / No"

    rule_set = models.ForeignKey(RuleSet, on_delete=models.CASCADE, related_name="triage_questions")
    code = models.CharField(max_length=32)
    text = models.TextField()
    help_text = models.TextField(blank=True)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    options = models.JSONField(default=list)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "code"]
        constraints = [models.UniqueConstraint(fields=["rule_set", "code"], name="uniq_triage_q")]

    def __str__(self):
        return self.code


class GateRule(models.Model):
    rule_set = models.ForeignKey(RuleSet, on_delete=models.CASCADE, related_name="gates")
    code = models.CharField(max_length=32)
    question_code = models.CharField(max_length=32)
    # Configuration, e.g. {"skip_when": {"q": "T1", "eq": "NO"}}
    condition = models.JSONField(default=dict)
    source_note = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["rule_set", "code"], name="uniq_gate")]

    def __str__(self):
        return self.code


class ControlRequirement(models.Model):
    rule_set = models.ForeignKey(RuleSet, on_delete=models.CASCADE, related_name="controls")
    code = models.CharField(max_length=32)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    domain = models.CharField(max_length=100, blank=True)
    order = models.PositiveIntegerField(default=0)
    # {"gates": ["G-PD", ...]}
    applicability = models.JSONField(default=dict)
    # {"PUBLIC": "OPTIONAL", "SENSITIVE": "RECOMMENDED", ...}
    levels = models.JSONField(default=dict)
    questions = models.ManyToManyField("AssessmentQuestion", through="RequirementQuestion", related_name="controls")

    class Meta:
        ordering = ["order", "code"]
        constraints = [models.UniqueConstraint(fields=["rule_set", "code"], name="uniq_control")]

    def __str__(self):
        return f"{self.code} {self.title}"


class FrameworkReference(models.Model):
    requirement = models.ForeignKey(ControlRequirement, on_delete=models.CASCADE, related_name="framework_refs")
    framework = models.CharField(max_length=100)
    reference = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    mapping_version = models.CharField(max_length=64, blank=True)
    edition = models.CharField(max_length=64, default="Not specified")
    source_note = models.TextField(blank=True)

    class Meta:
        ordering = ["framework", "reference"]


class AssessmentQuestion(models.Model):
    rule_set = models.ForeignKey(RuleSet, on_delete=models.CASCADE, related_name="assessment_questions")
    code = models.CharField(max_length=32)
    section = models.CharField(max_length=100)
    text = models.TextField()
    help_text = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    always_ask = models.BooleanField(default=False)

    class Meta:
        ordering = ["order", "code"]
        constraints = [models.UniqueConstraint(fields=["rule_set", "code"], name="uniq_assessment_q")]

    def __str__(self):
        return self.code


class RequirementQuestion(models.Model):
    requirement = models.ForeignKey(ControlRequirement, on_delete=models.CASCADE)
    question = models.ForeignKey(AssessmentQuestion, on_delete=models.CASCADE)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["requirement", "question"], name="uniq_req_question")]


class ComplianceGuidance(models.Model):
    rule_set = models.ForeignKey(RuleSet, on_delete=models.CASCADE, related_name="compliance_guidance")
    requirement_level = models.CharField(max_length=16, choices=RequirementLevel.choices)
    decision_key = models.CharField(max_length=32)
    guidance_text = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["rule_set", "requirement_level", "decision_key"], name="uniq_guidance"),
        ]


class TechnicalCatalogue(models.Model):
    version = models.CharField(max_length=64, unique=True)
    label = models.CharField(max_length=200)
    source_sha256 = models.CharField(max_length=64)
    loaded_at = models.DateTimeField(auto_now_add=True)
    loaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_active = models.BooleanField(default=False)
    is_synthetic = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-loaded_at"]
        constraints = [
            models.UniqueConstraint(fields=["is_active"], condition=models.Q(is_active=True), name="one_active_catalogue"),
        ]

    def __str__(self):
        return f"Technical Catalogue {self.version}"


class ScopeTag(models.Model):
    code = models.CharField(max_length=32, unique=True)
    label = models.CharField(max_length=100)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "code"]

    def __str__(self):
        return self.label


class TechnicalControl(models.Model):
    catalogue = models.ForeignKey(TechnicalCatalogue, on_delete=models.CASCADE, related_name="controls")
    source_id = models.CharField(max_length=32)
    raw_category = models.CharField(max_length=100, blank=True)
    tags = models.ManyToManyField(ScopeTag, related_name="technical_controls")
    risk_description = models.TextField(blank=True)
    control_name = models.CharField(max_length=255)
    control_description = models.TextField(blank=True)
    testing_process = models.TextField(blank=True)

    class Meta:
        ordering = ["source_id"]
        constraints = [models.UniqueConstraint(fields=["catalogue", "source_id"], name="uniq_tech_control")]

    def __str__(self):
        return f"{self.source_id} {self.control_name}"
