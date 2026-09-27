from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.content.loader import build_engine_config
from apps.content.models import AssessmentQuestion, ControlRequirement, TechnicalCatalogue, TechnicalControl
from apps.core.errors import ServiceError
from apps.core.stage import is_password_only
from apps.engine import engine
from apps.reviews.models import (
    Applicability,
    ParticipantRole,
    Review,
    ReviewQuestion,
    ReviewRequirement,
    ReviewState,
    ReviewTechnicalItem,
)

from .common import require, transition
from .triage import current_answers, rule_set_for

REQUIRED_INTAKE = ("title", "purpose", "goals", "department_id", "project_owner", "request_type")


def generation_problems(review):
    problems = []
    for field in REQUIRED_INTAKE:
        if not getattr(review, field):
            problems.append(f"Intake field '{field.replace('_id', '')}' is required.")
    roles = set(review.assignments.values_list("role", flat=True))
    if ParticipantRole.REQUESTER not in roles or ParticipantRole.REVIEWER not in roles:
        problems.append("A Requester and a Reviewer must be assigned.")
    rs = rule_set_for(review)
    if rs is None:
        problems.append("No active rule baseline exists.")
    else:
        answers = current_answers(review)
        missing = [q.code for q in rs.triage_questions.all() if not answers.get(q.code)]
        if missing:
            problems.append(f"Triage answers missing: {', '.join(missing)}.")
    if (review.technical_catalogue or TechnicalCatalogue.objects.filter(is_active=True).first()) is None:
        problems.append("No active technical catalogue exists.")
    return problems


@transaction.atomic
def generate_assessment(actor, review, *, request=None):
    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "review.generate", review)
    problems = generation_problems(review)
    if problems:
        raise ServiceError("The assessment cannot be generated yet.", problems)

    rs = rule_set_for(review)
    catalogue = review.technical_catalogue or TechnicalCatalogue.objects.get(is_active=True)
    if is_password_only(settings) and not (rs.is_synthetic and catalogue.is_synthetic):
        raise ServiceError("Only synthetic content may be used in the local password-only stage.")

    try:
        result = engine.evaluate(build_engine_config(rs), current_answers(review))
    except engine.EngineError as exc:
        raise ServiceError(str(exc)) from exc

    controls = {c.code: c for c in ControlRequirement.objects.filter(rule_set=rs)}
    for i, c in enumerate(result.controls):
        ReviewRequirement.objects.create(
            review=review, control=controls[c.code], order=i,
            applicability=Applicability.KEEP if c.keep else Applicability.SKIP,
            level=c.level or "", asked=c.asked, reason=c.reason,
        )
    questions = {q.code: q for q in AssessmentQuestion.objects.filter(rule_set=rs)}
    for q in result.questions:
        ReviewQuestion.objects.create(review=review, question=questions[q.code], selection_reason=q.reason)

    tag_ids = review.scope_tags.values_list("scope_tag_id", flat=True)
    candidates = TechnicalControl.objects.filter(catalogue=catalogue, tags__in=tag_ids).distinct()
    for tc in candidates:
        ReviewTechnicalItem.objects.create(review=review, control=tc, included=True)

    review.rule_set = rs
    review.technical_catalogue = catalogue
    review.classification = result.classification
    review.classification_reason = result.classification_reason
    review.routing = result.routing
    review.generated_at = timezone.now()
    review.save()
    kept = sum(1 for c in result.controls if c.keep)
    record(
        "assessment.generated", actor=actor, review=review, obj=review, request=request,
        detail={
            "rule_set": rs.version, "technical_catalogue": catalogue.version,
            "classification": result.classification, "requirements": len(result.controls),
            "applicable": kept, "questions": len(result.questions), "technical_candidates": candidates.count(),
        },
    )
    transition(actor, review, ReviewState.IN_ASSESSMENT, request=request)
    return review, result
