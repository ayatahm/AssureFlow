from django.db import transaction
from django.utils import timezone

from apps.assurance.models import BusinessRiskAcceptance, Conclusion, Evidence, Finding, RiskRecord
from apps.audit.services import record
from apps.core.errors import ServiceError
from apps.reviews.models import (
    FLAG_FOR_ANSWER,
    Answer,
    Applicability,
    Review,
    ReviewFlag,
    ReviewQuestion,
    ReviewRequirement,
    ReviewState,
)

from .common import acting_on_behalf, require, transition


def linked_requirements(review_question):
    return ReviewRequirement.objects.filter(
        review_id=review_question.review_id,
        control__questions=review_question.question,
        applicability=Applicability.KEEP,
    )


@transaction.atomic
def save_answer(actor, review, review_question, answer, comment="", *, request=None):
    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "review.answer", review)
    if review_question.review_id != review.pk:
        raise ServiceError("Question does not belong to this review.")
    if answer not in Answer.values:
        raise ServiceError("Choose Yes, No, Unsure or Not Applicable.")
    rq = ReviewQuestion.objects.select_for_update().get(pk=review_question.pk)
    changed = rq.answer != answer or rq.comment != comment
    if not changed:
        return rq
    rq.answer = answer
    rq.comment = comment
    rq.answered_by = actor
    rq.answered_at = timezone.now()
    rq.on_behalf = acting_on_behalf(actor, review)
    rq.save()
    ReviewFlag.objects.filter(source_question=rq, resolved_at__isnull=True).update(resolved_at=timezone.now())
    record("answer.saved", actor=actor, review=review, obj=rq, request=request,
           detail={"question": rq.question.code, "answer": answer, "on_behalf": rq.on_behalf})
    kind = FLAG_FOR_ANSWER.get(answer)
    if kind:
        for rr in linked_requirements(rq):
            flag = ReviewFlag.objects.create(review_requirement=rr, kind=kind, source_question=rq)
            record("flag.created", actor=None, review=review, obj=flag,
                   detail={"requirement": rr.control.code, "kind": kind, "question": rq.question.code})
    return rq


def unanswered(review):
    return review.questions.filter(answer="")


@transaction.atomic
def submit_assessment(actor, review, *, request=None):
    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "review.submit", review)
    missing = list(unanswered(review).values_list("question__code", flat=True))
    if missing:
        raise ServiceError("Answer every question before submitting.", [f"Unanswered: {', '.join(missing)}"])
    transition(actor, review, ReviewState.IN_REVIEW, request=request)
    return review


def reset_blockers(review):
    blockers = []
    if Evidence.objects.filter(review=review).exists():
        blockers.append("Evidence has been uploaded.")
    if Conclusion.objects.filter(review_requirement__review=review).exists():
        blockers.append("Reviewer conclusions exist.")
    if Finding.objects.filter(review=review).exists():
        blockers.append("Findings or actions exist.")
    if BusinessRiskAcceptance.objects.filter(review=review).exists():
        blockers.append("Risk acceptance records exist.")
    if RiskRecord.objects.filter(review=review).exists():
        blockers.append("Risk judgements exist.")
    return blockers


@transaction.atomic
def return_to_draft(actor, review, reason, *, request=None):
    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "review.return_to_draft", review)
    if not reason.strip():
        raise ServiceError("A reason is required.")
    blockers = reset_blockers(review)
    if blockers:
        raise ServiceError(
            "This assessment already has assurance records. Raise a new linked review for material scope changes.",
            blockers,
        )
    detail = {
        "rule_set": review.rule_set.version if review.rule_set else None,
        "technical_catalogue": review.technical_catalogue.version if review.technical_catalogue else None,
        "discarded_requirements": review.requirements.count(),
        "discarded_questions": review.questions.count(),
    }
    ReviewFlag.objects.filter(review_requirement__review=review).delete()
    review.questions.all().delete()
    review.requirements.all().delete()
    review.technical_items.all().delete()
    # Versions stay pinned: regeneration uses the same rule/catalogue versions.
    review.classification = ""
    review.classification_reason = ""
    review.routing = {}
    review.generated_at = None
    review.save()
    transition(actor, review, ReviewState.DRAFT, reason=reason, request=request, detail=detail)
    return review


@transaction.atomic
def withdraw(actor, review, reason, *, request=None):
    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "review.withdraw", review)
    if not reason.strip():
        raise ServiceError("A reason is required.")
    review.withdrawn_reason = reason
    review.withdrawn_at = timezone.now()
    review.save(update_fields=["withdrawn_reason", "withdrawn_at", "updated_at"])
    transition(actor, review, ReviewState.WITHDRAWN, reason=reason, request=request)
    return review


@transaction.atomic
def return_to_requester(actor, review, reason, *, request=None):
    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "review.return_to_requester", review)
    if not reason.strip():
        raise ServiceError("A message to the Requester is required.")
    transition(actor, review, ReviewState.IN_ASSESSMENT, reason=reason, request=request)
    return review
