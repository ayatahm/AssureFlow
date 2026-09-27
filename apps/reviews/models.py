from django.conf import settings
from django.db import models

from apps.content.models import Classification, RequirementLevel

User = settings.AUTH_USER_MODEL


class RequestType(models.TextChoices):
    NEW_SYSTEM = "NEW_SYSTEM", "New system or service"
    EXISTING_CHANGE = "EXISTING_CHANGE", "Change to an existing system"
    NEW_VENDOR_CLOUD = "NEW_VENDOR_CLOUD", "New vendor or cloud service"


class ReviewState(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    IN_ASSESSMENT = "IN_ASSESSMENT", "In Assessment"
    IN_REVIEW = "IN_REVIEW", "In Review"
    AWAITING_ACCEPTANCE = "AWAITING_ACCEPTANCE", "Awaiting Acceptance"
    COMPLETED = "COMPLETED", "Completed"
    WITHDRAWN = "WITHDRAWN", "Withdrawn"


FROZEN_STATES = (ReviewState.COMPLETED, ReviewState.WITHDRAWN)


class Outcome(models.TextChoices):
    MET = "MET", "Requirements met"
    MET_WITH_EXCEPTIONS = "MET_WITH_EXCEPTIONS", "Met with accepted exceptions"
    NOT_MET = "NOT_MET", "Requirements not met"


class ParticipantRole(models.TextChoices):
    REQUESTER = "REQUESTER", "Requester"
    REVIEWER = "REVIEWER", "Reviewer"
    BUSINESS_OWNER = "BUSINESS_OWNER", "Business Owner"


class ReferenceCounter(models.Model):
    year = models.PositiveIntegerField(unique=True)
    last = models.PositiveIntegerField(default=0)


class Review(models.Model):
    reference = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=200)
    purpose = models.TextField("Purpose / business case")
    goals = models.TextField("Goals / brief description")
    department = models.ForeignKey("accounts.Department", on_delete=models.PROTECT, related_name="reviews")
    project_owner = models.CharField("Project / activity owner", max_length=200)
    request_type = models.CharField(max_length=20, choices=RequestType.choices)
    impact = models.CharField(max_length=32, blank=True)
    target_date = models.DateField(null=True, blank=True)
    external_ref = models.CharField("External request / change reference", max_length=100, blank=True)
    state = models.CharField(max_length=24, choices=ReviewState.choices, default=ReviewState.DRAFT, db_index=True)

    rule_set = models.ForeignKey("content.RuleSet", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    technical_catalogue = models.ForeignKey(
        "content.TechnicalCatalogue", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    classification = models.CharField(max_length=16, choices=Classification.choices, blank=True)
    classification_reason = models.TextField(blank=True)
    routing = models.JSONField(default=dict, blank=True)
    third_party_ref = models.CharField(max_length=100, blank=True)
    generated_at = models.DateTimeField(null=True, blank=True)

    scope_confirmed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    scope_confirmed_at = models.DateTimeField(null=True, blank=True)
    scope_confirmation_note = models.TextField(blank=True)

    completed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=24, choices=Outcome.choices, blank=True)
    final_summary = models.TextField(blank=True)
    conditions = models.TextField(blank=True)
    # Set only by the completion service (spec 13.1).
    final_risk_record = models.ForeignKey(
        "assurance.RiskRecord", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    on_behalf = models.BooleanField(default=False, help_text="Intake entered by InfoSec on behalf of the department.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    withdrawn_reason = models.TextField(blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    linked_review = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="follow_ups")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference} {self.title}"

    @property
    def is_frozen(self):
        return self.state in FROZEN_STATES


class ReviewAssignment(models.Model):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="assignments")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="review_assignments")
    role = models.CharField(max_length=20, choices=ParticipantRole.choices)
    assigned_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["review", "user", "role"], name="uniq_assignment")]


class TriageAnswer(models.Model):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="triage_answers")
    question_code = models.CharField(max_length=32)
    value = models.CharField(max_length=32)
    entered_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    on_behalf = models.BooleanField(default=False)
    entered_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["review", "question_code"], name="uniq_triage_answer")]


class ReviewScopeTag(models.Model):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="scope_tags")
    scope_tag = models.ForeignKey("content.ScopeTag", on_delete=models.PROTECT)
    proposed_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    proposed_at = models.DateTimeField(auto_now_add=True)
    confirmed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    removed_at_confirmation = models.BooleanField(default=False)

    class Meta:
        ordering = ["scope_tag__order"]
        constraints = [models.UniqueConstraint(fields=["review", "scope_tag"], name="uniq_review_scope_tag")]


class Applicability(models.TextChoices):
    KEEP = "KEEP", "Keep"
    SKIP = "SKIP", "Skip"


class ReviewRequirement(models.Model):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="requirements")
    control = models.ForeignKey("content.ControlRequirement", on_delete=models.PROTECT, related_name="+")
    applicability = models.CharField(max_length=4, choices=Applicability.choices)
    level = models.CharField(max_length=16, choices=RequirementLevel.choices, blank=True)
    asked = models.BooleanField(default=False)
    reason = models.TextField()
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "pk"]
        constraints = [models.UniqueConstraint(fields=["review", "control"], name="uniq_review_requirement")]

    def __str__(self):
        return self.control.code

    @property
    def is_applicable(self):
        return self.applicability == Applicability.KEEP


class Answer(models.TextChoices):
    YES = "YES", "Yes"
    NO = "NO", "No"
    UNSURE = "UNSURE", "Unsure"
    NOT_APPLICABLE = "NOT_APPLICABLE", "Not Applicable"


class ReviewQuestion(models.Model):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="questions")
    question = models.ForeignKey("content.AssessmentQuestion", on_delete=models.PROTECT, related_name="+")
    selection_reason = models.TextField(blank=True)
    answer = models.CharField(max_length=16, choices=Answer.choices, blank=True)
    comment = models.TextField(blank=True)
    answered_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    answered_at = models.DateTimeField(null=True, blank=True)
    on_behalf = models.BooleanField(default=False)

    class Meta:
        ordering = ["question__order", "pk"]
        constraints = [models.UniqueConstraint(fields=["review", "question"], name="uniq_review_question")]

    def __str__(self):
        return self.question.code


class FlagKind(models.TextChoices):
    POTENTIAL_GAP = "POTENTIAL_GAP", "Potential Security Gap"
    CLARIFICATION = "CLARIFICATION", "Clarification / Evidence Required"
    CONFIRM_NA = "CONFIRM_NA", "Reviewer confirmation required"


FLAG_FOR_ANSWER = {
    Answer.NO: FlagKind.POTENTIAL_GAP,
    Answer.UNSURE: FlagKind.CLARIFICATION,
    Answer.NOT_APPLICABLE: FlagKind.CONFIRM_NA,
}


class ReviewFlag(models.Model):
    review_requirement = models.ForeignKey(ReviewRequirement, on_delete=models.CASCADE, related_name="flags")
    kind = models.CharField(max_length=20, choices=FlagKind.choices)
    source_question = models.ForeignKey(ReviewQuestion, on_delete=models.CASCADE, related_name="flags")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)


class TechnicalResult(models.TextChoices):
    NOT_RECORDED = "", "Not recorded"
    NO_ISSUE = "NO_ISSUE", "No issue identified"
    ISSUE_IDENTIFIED = "ISSUE_IDENTIFIED", "Issue identified"
    NOT_TESTED = "NOT_TESTED", "Not tested"


class ReviewTechnicalItem(models.Model):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="technical_items")
    control = models.ForeignKey("content.TechnicalControl", on_delete=models.PROTECT, related_name="+")
    included = models.BooleanField(default=True)
    exclusion_reason = models.TextField(blank=True)
    added_by_reviewer = models.BooleanField(default=False)
    result = models.CharField(max_length=20, choices=TechnicalResult.choices, blank=True)
    notes = models.TextField(blank=True)
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["control__source_id"]
        constraints = [models.UniqueConstraint(fields=["review", "control"], name="uniq_review_tech_item")]

    def __str__(self):
        return self.control.source_id
