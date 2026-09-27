from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.assurance.models import (
    AcceptanceState,
    BusinessRiskAcceptance,
    BusinessRiskAcceptanceItem,
)
from apps.audit.services import record
from apps.core.errors import ServiceError
from apps.reviews.models import ParticipantRole, Review, ReviewAssignment, ReviewState
from apps.reviews.services.common import require, transition

from .status import latest_risk, requirement_gaps, unresolved_technical_items


def eligible_business_owner(review, user, actor):
    return (
        user is not None
        and user.is_active
        and not user.is_superuser
        and not user.is_app_admin
        and user.has_role(Role.BUSINESS_OWNER)
        and user.pk != actor.pk
        and not review.assignments.filter(user=user, role=ParticipantRole.REVIEWER).exists()
    )


@transaction.atomic
def request_acceptance(
    actor, review, *, business_owner, requirements=(), technical_items=(), justification,
    compensating_controls, conditions, review_date, expiry_date, request=None,
):
    """Prepare and send a Formal Business Risk Acceptance request (spec 14)."""
    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "acceptance.request", review)
    errors = []
    gap_ids = {rr.pk for rr in requirement_gaps(review)}
    tech_ids = {t.pk for t in unresolved_technical_items(review)}
    requirements, technical_items = list(requirements), list(technical_items)
    if not requirements and not technical_items:
        errors.append("Select at least one unresolved requirement or technical finding.")
    for rr in requirements:
        if rr.review_id != review.pk or rr.pk not in gap_ids:
            errors.append(f"{rr.control.code} is not a current Security Gap in this review.")
    for item in technical_items:
        if item.review_id != review.pk or item.pk not in tech_ids:
            errors.append(f"{item.control.source_id} has no unresolved technical finding.")
    risk = latest_risk(review)
    if risk is None:
        errors.append("Record the risk judgement before requesting acceptance.")
    if not eligible_business_owner(review, business_owner, actor):
        errors.append("Choose an eligible Business Owner who is not a Reviewer on this review.")
    today = timezone.localdate()
    if review_date is None:
        errors.append("A review date is required.")
    if expiry_date is None:
        errors.append("An expiry date is required.")
    if review_date and expiry_date:
        if expiry_date <= today:
            errors.append("The expiry date must be in the future.")
        if review_date < today:
            errors.append("The review date cannot be in the past.")
        if review_date > expiry_date:
            errors.append("The review date must be on or before the expiry date.")
    for label, value in (("Justification", justification), ("Compensating controls", compensating_controls),
                         ("Conditions", conditions)):
        if not (value or "").strip():
            errors.append(f"{label} is required (use 'None' if there are none).")
    if errors:
        raise ServiceError("The acceptance request is incomplete.", errors)

    assignment, created = ReviewAssignment.objects.get_or_create(
        review=review, user=business_owner, role=ParticipantRole.BUSINESS_OWNER, defaults={"assigned_by": actor}
    )
    if created:
        record("review.participant_assigned", actor=actor, review=review, obj=assignment,
               detail={"user": business_owner.pk, "role": ParticipantRole.BUSINESS_OWNER}, request=request)
    acc = BusinessRiskAcceptance.objects.create(
        review=review, risk_record=risk, business_owner=business_owner, justification=justification,
        compensating_controls=compensating_controls, conditions=conditions, review_date=review_date,
        expiry_date=expiry_date, created_by=actor,
    )
    for rr in requirements:
        BusinessRiskAcceptanceItem.objects.create(acceptance=acc, review_requirement=rr)
    for item in technical_items:
        BusinessRiskAcceptanceItem.objects.create(acceptance=acc, technical_item=item)
    detail = {
        "risk_record": risk.pk,
        "requirements": sorted(rr.control.code for rr in requirements),
        "technical_items": sorted(t.control.source_id for t in technical_items),
        "business_owner": business_owner.pk,
    }
    record("acceptance.prepared", actor=actor, review=review, obj=acc, detail=detail, request=request)
    record("acceptance.requested", actor=actor, review=review, obj=acc, detail={"business_owner": business_owner.pk},
           request=request)
    transition(actor, review, ReviewState.AWAITING_ACCEPTANCE, request=request)
    return acc


@transaction.atomic
def decide_acceptance(actor, acceptance, decision, note, *, request=None):
    review = Review.objects.select_for_update().get(pk=acceptance.review_id)
    acceptance = BusinessRiskAcceptance.objects.select_for_update().get(pk=acceptance.pk)
    require(actor, "acceptance.decide", review, acceptance)
    if decision not in (AcceptanceState.ACCEPTED, AcceptanceState.DECLINED):
        raise ServiceError("Choose Accept or Decline.")
    if not note.strip():
        raise ServiceError("A decision note is required.")
    acceptance.state = decision
    acceptance.decision_note = note
    acceptance.decided_at = timezone.now()
    acceptance.save(update_fields=["state", "decision_note", "decided_at"])
    record("acceptance.decided", actor=actor, review=review, obj=acceptance, request=request,
           detail={"decision": decision, "risk_record": acceptance.risk_record_id})
    # Either decision returns the review to In Review (spec 5.1).
    if not review.acceptances.filter(state=AcceptanceState.PENDING).exists():
        transition(actor, review, ReviewState.IN_REVIEW, request=request)
    return acceptance
