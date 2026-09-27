from django.db import transaction

from apps.assurance.models import ComplianceDecisionValue, ProjectComplianceDecision
from apps.audit.services import record
from apps.content.models import ComplianceGuidance
from apps.core.errors import ServiceError
from apps.core.permissions import PermissionDenied, can
from apps.reviews.models import Review
from apps.reviews.services.common import require


def guidance_for(rr, decision):
    g = ComplianceGuidance.objects.filter(
        rule_set_id=rr.review.rule_set_id, requirement_level=rr.level, decision_key=decision
    ).first()
    return g.guidance_text if g else ""


def guidance_table(rr):
    return {
        g.decision_key: g.guidance_text
        for g in ComplianceGuidance.objects.filter(rule_set_id=rr.review.rule_set_id, requirement_level=rr.level)
    }


@transaction.atomic
def record_compliance(actor, review, rr, decision, note="", *, request=None):
    """Per-control Project Compliance Decision — not risk acceptance (spec 12)."""
    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "compliance.record", review, rr)
    if decision not in ComplianceDecisionValue.values:
        raise ServiceError("Invalid decision.")
    if decision == ComplianceDecisionValue.EXCEPTION_GRANTED and not can(
        actor, "compliance.grant_exception", review, rr
    ):
        raise PermissionDenied("compliance.grant_exception")
    if decision != ComplianceDecisionValue.WILL_COMPLY and not note.strip():
        raise ServiceError("A note is required for this decision.")
    pcd = ProjectComplianceDecision.objects.create(review_requirement=rr, decision=decision, note=note, decided_by=actor)
    record("compliance_decision.recorded", actor=actor, review=review, obj=pcd, request=request,
           detail={"requirement": rr.control.code, "level": rr.level, "decision": decision})
    return pcd
