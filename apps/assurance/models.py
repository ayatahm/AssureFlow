from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.audit.models import AppendOnlyModel

User = settings.AUTH_USER_MODEL


def exactly_one(*fields):
    """Q expression: exactly one of ``fields`` is non-null."""
    options = []
    for chosen in fields:
        q = Q(**{f"{chosen}__isnull": False})
        for other in fields:
            if other != chosen:
                q &= Q(**{f"{other}__isnull": True})
        options.append(q)
    result = options[0]
    for q in options[1:]:
        result |= q
    return result


class EvidencePolicy(models.Model):
    """Singleton upload policy (spec 9)."""

    TYPE_CHOICES = [("application/pdf", "PDF"), ("image/png", "PNG"), ("image/jpeg", "JPEG")]

    allowed_types = models.JSONField(default=list)
    max_bytes = models.PositiveIntegerField(default=10 * 1024 * 1024, validators=[MinValueValidator(1024)])
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "evidence policy"

    def __str__(self):
        return "Evidence policy"

    @classmethod
    def current(cls):
        obj = cls.objects.order_by("pk").first()
        return obj or cls(allowed_types=[t for t, _ in cls.TYPE_CHOICES])


class Evidence(models.Model):
    review = models.ForeignKey("reviews.Review", on_delete=models.PROTECT, related_name="evidence")
    original_name = models.CharField(max_length=255)
    stored_name = models.CharField(max_length=64, unique=True)
    content_type = models.CharField(max_length=64)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    comment = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]
        verbose_name_plural = "evidence"

    def __str__(self):
        return self.original_name


class EvidenceLink(models.Model):
    evidence = models.ForeignKey(Evidence, on_delete=models.PROTECT, related_name="links")
    review_requirement = models.ForeignKey(
        "reviews.ReviewRequirement", null=True, blank=True, on_delete=models.PROTECT, related_name="evidence_links"
    )
    technical_item = models.ForeignKey(
        "reviews.ReviewTechnicalItem", null=True, blank=True, on_delete=models.PROTECT, related_name="evidence_links"
    )
    action = models.ForeignKey("Action", null=True, blank=True, on_delete=models.PROTECT, related_name="evidence_links")
    linked_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(
                condition=exactly_one("review_requirement", "technical_item", "action"),
                name="evidence_link_exactly_one_target",
            ),
            models.UniqueConstraint(
                fields=["evidence", "review_requirement"],
                condition=Q(review_requirement__isnull=False),
                name="uniq_evidence_requirement",
            ),
            models.UniqueConstraint(
                fields=["evidence", "technical_item"],
                condition=Q(technical_item__isnull=False),
                name="uniq_evidence_technical_item",
            ),
            models.UniqueConstraint(
                fields=["evidence", "action"], condition=Q(action__isnull=False), name="uniq_evidence_action"
            ),
        ]

    @property
    def target(self):
        return self.review_requirement or self.technical_item or self.action


class ConclusionStatus(models.TextChoices):
    VERIFIED = "VERIFIED", "Verified"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED", "Evidence Required"
    SECURITY_GAP = "SECURITY_GAP", "Security Gap"
    NOT_APPLICABLE = "NOT_APPLICABLE", "Not Applicable"


class Conclusion(AppendOnlyModel):
    review_requirement = models.ForeignKey("reviews.ReviewRequirement", on_delete=models.PROTECT, related_name="conclusions")
    status = models.CharField(max_length=20, choices=ConclusionStatus.choices)
    notes = models.TextField(blank=True)
    decided_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["decided_at", "pk"]


class Priority(models.TextChoices):
    LOW = "LOW", "Low"
    MODERATE = "MODERATE", "Moderate"
    HIGH = "HIGH", "High"
    CRITICAL = "CRITICAL", "Critical"


class Finding(models.Model):
    review = models.ForeignKey("reviews.Review", on_delete=models.PROTECT, related_name="findings")
    review_requirement = models.ForeignKey(
        "reviews.ReviewRequirement", null=True, blank=True, on_delete=models.PROTECT, related_name="findings"
    )
    technical_item = models.ForeignKey(
        "reviews.ReviewTechnicalItem", null=True, blank=True, on_delete=models.PROTECT, related_name="findings"
    )
    title = models.CharField(max_length=200)
    description = models.TextField()
    priority = models.CharField(max_length=10, choices=Priority.choices)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(
                condition=exactly_one("review_requirement", "technical_item"), name="finding_exactly_one_origin"
            )
        ]

    def __str__(self):
        return f"F-{self.pk} {self.title}"

    @property
    def target(self):
        return self.review_requirement or self.technical_item

    @property
    def code(self):
        return f"F-{self.pk:03d}"


class ActionState(models.TextChoices):
    OPEN = "OPEN", "Open"
    SUBMITTED = "SUBMITTED", "Submitted"
    CLOSED = "CLOSED", "Closed"


class Action(models.Model):
    finding = models.ForeignKey(Finding, on_delete=models.PROTECT, related_name="actions")
    owner = models.ForeignKey(User, on_delete=models.PROTECT, related_name="owned_actions")
    due_date = models.DateField()
    expected_outcome = models.TextField()
    state = models.CharField(max_length=10, choices=ActionState.choices, default=ActionState.OPEN)
    submission_note = models.TextField(blank=True)
    submitted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    closed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"A-{self.pk}"

    @property
    def code(self):
        return f"A-{self.pk:03d}"

    @property
    def review(self):
        return self.finding.review


class ActionHistory(AppendOnlyModel):
    class Kind(models.TextChoices):
        CREATED = "CREATED", "Created"
        SUBMITTED = "SUBMITTED", "Submitted"
        RETURNED = "RETURNED", "Returned"
        CLOSED = "CLOSED", "Closed"
        REOPENED = "REOPENED", "Reopened"

    action = models.ForeignKey(Action, on_delete=models.PROTECT, related_name="history")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    note = models.TextField(blank=True)
    evidence_link_ids = models.JSONField(default=list, blank=True)
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at", "pk"]


class ComplianceDecisionValue(models.TextChoices):
    WILL_COMPLY = "WILL_COMPLY", "Will Comply"
    WILL_NOT_COMPLY = "WILL_NOT_COMPLY", "Will Not Comply"
    EXCEPTION_GRANTED = "EXCEPTION_GRANTED", "Exception Granted"


class ProjectComplianceDecision(AppendOnlyModel):
    review_requirement = models.ForeignKey(
        "reviews.ReviewRequirement", on_delete=models.PROTECT, related_name="compliance_decisions"
    )
    decision = models.CharField(max_length=20, choices=ComplianceDecisionValue.choices)
    note = models.TextField(blank=True)
    decided_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["decided_at", "pk"]


class RiskRating(models.TextChoices):
    LOW = "LOW", "Low"
    MODERATE = "MODERATE", "Moderate"
    HIGH = "HIGH", "High"
    CRITICAL = "CRITICAL", "Critical"


RISK_SCALE_VERSION = "prototype-4-level-v1"


class RiskRecord(AppendOnlyModel):
    """Authoritative, append-only Reviewer risk judgement (spec 13)."""

    review = models.ForeignKey("reviews.Review", on_delete=models.PROTECT, related_name="risk_records")
    inherent = models.CharField(max_length=10, choices=RiskRating.choices)
    residual = models.CharField(max_length=10, choices=RiskRating.choices)
    rationale = models.TextField()
    scale_version = models.CharField(max_length=64, default=RISK_SCALE_VERSION)
    scale_labels = models.JSONField(default=list)
    assessed_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    assessed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["assessed_at", "pk"]

    def __str__(self):
        return f"R-{self.pk}"


class AcceptanceState(models.TextChoices):
    PENDING = "PENDING", "Pending"
    ACCEPTED = "ACCEPTED", "Accepted"
    DECLINED = "DECLINED", "Declined"


class BusinessRiskAcceptance(models.Model):
    review = models.ForeignKey("reviews.Review", on_delete=models.PROTECT, related_name="acceptances")
    risk_record = models.ForeignKey(RiskRecord, on_delete=models.PROTECT, related_name="acceptances")
    business_owner = models.ForeignKey(User, on_delete=models.PROTECT, related_name="acceptance_requests")
    justification = models.TextField()
    compensating_controls = models.TextField()
    conditions = models.TextField()
    review_date = models.DateField()
    expiry_date = models.DateField()
    state = models.CharField(max_length=10, choices=AcceptanceState.choices, default=AcceptanceState.PENDING)
    decision_note = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"BRA-{self.pk}"

    @property
    def code(self):
        return f"BRA-{self.pk:03d}"


class BusinessRiskAcceptanceItem(models.Model):
    acceptance = models.ForeignKey(BusinessRiskAcceptance, on_delete=models.PROTECT, related_name="items")
    review_requirement = models.ForeignKey(
        "reviews.ReviewRequirement", null=True, blank=True, on_delete=models.PROTECT, related_name="acceptance_items"
    )
    technical_item = models.ForeignKey(
        "reviews.ReviewTechnicalItem", null=True, blank=True, on_delete=models.PROTECT, related_name="acceptance_items"
    )

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(
                condition=exactly_one("review_requirement", "technical_item"), name="acceptance_item_exactly_one"
            ),
            models.UniqueConstraint(
                fields=["acceptance", "review_requirement"],
                condition=Q(review_requirement__isnull=False),
                name="uniq_acceptance_requirement",
            ),
            models.UniqueConstraint(
                fields=["acceptance", "technical_item"],
                condition=Q(technical_item__isnull=False),
                name="uniq_acceptance_technical",
            ),
        ]

    @property
    def target(self):
        return self.review_requirement or self.technical_item
