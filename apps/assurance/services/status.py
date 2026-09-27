"""Derived assurance status. Nothing here is stored; it is computed from records."""
from django.db.models import Max
from django.utils import timezone

from apps.assurance.models import (
    AcceptanceState,
    ActionState,
    BusinessRiskAcceptanceItem,
    ConclusionStatus,
    Finding,
    RiskRecord,
)
from apps.reviews.models import Applicability


def latest_answer_time(rr):
    return (
        rr.review.questions.filter(question__in=rr.control.questions.all(), answered_at__isnull=False)
        .aggregate(m=Max("answered_at"))["m"]
    )


def conclusion_state(rr):
    """Return (current, latest).

    ``latest`` is the newest Conclusion row (history). ``current`` is the same
    row unless a linked answer changed after it was recorded, in which case the
    conclusion needs re-review and ``current`` is None (spec 16.1, 30.4).
    """
    latest = rr.conclusions.order_by("-decided_at", "-pk").first()
    if latest is None:
        return None, None
    answered = latest_answer_time(rr)
    if answered and answered > latest.decided_at:
        return None, latest
    return latest, latest


def current_conclusion(rr):
    return conclusion_state(rr)[0]


def current_compliance(rr):
    return rr.compliance_decisions.order_by("-decided_at", "-pk").first()


def latest_risk(review):
    return RiskRecord.objects.filter(review=review).order_by("-assessed_at", "-pk").first()


def applicable_requirements(review):
    return review.requirements.filter(applicability=Applicability.KEEP).select_related("control")


def requirement_gaps(review):
    """Applicable requirements whose current conclusion is Security Gap."""
    gaps = []
    for rr in applicable_requirements(review):
        current = current_conclusion(rr)
        if current and current.status == ConclusionStatus.SECURITY_GAP:
            gaps.append(rr)
    return gaps


def technical_finding_resolved(finding):
    actions = list(finding.actions.all())
    return bool(actions) and all(a.state == ActionState.CLOSED for a in actions)


def unresolved_technical_items(review):
    """Technical items with at least one confirmed finding lacking verified remediation."""
    items = {}
    for f in Finding.objects.filter(review=review, technical_item__isnull=False).select_related("technical_item"):
        if not technical_finding_resolved(f):
            items[f.technical_item_id] = f.technical_item
    return list(items.values())


def latest_acceptance_for(review, *, review_requirement=None, technical_item=None):
    qs = BusinessRiskAcceptanceItem.objects.filter(acceptance__review=review).select_related("acceptance")
    if review_requirement is not None:
        qs = qs.filter(review_requirement=review_requirement)
    else:
        qs = qs.filter(technical_item=technical_item)
    item = qs.order_by("-acceptance__created_at", "-acceptance__pk").first()
    return item.acceptance if item else None


# Treatment statuses
MISSING, PENDING, SUPERSEDED, EXPIRED, ACCEPTED, DECLINED = (
    "MISSING", "PENDING", "SUPERSEDED", "EXPIRED", "ACCEPTED", "DECLINED",
)
TREATMENT_LABELS = {
    MISSING: "No treatment decision",
    PENDING: "Decision pending",
    SUPERSEDED: "Decision refers to an older risk record",
    EXPIRED: "Acceptance expired",
    ACCEPTED: "Risk accepted",
    DECLINED: "Acceptance declined",
}


def treatment_status(acceptance, final_risk, today=None):
    today = today or timezone.localdate()
    if acceptance is None:
        return MISSING
    if acceptance.state == AcceptanceState.PENDING:
        return PENDING
    if final_risk is None or acceptance.risk_record_id != final_risk.pk:
        return SUPERSEDED
    if acceptance.state == AcceptanceState.DECLINED:
        return DECLINED
    if acceptance.expiry_date < today:
        return EXPIRED
    return ACCEPTED


def target_treatment(review, target, final_risk, today=None):
    from apps.reviews.models import ReviewRequirement

    if isinstance(target, ReviewRequirement):
        acc = latest_acceptance_for(review, review_requirement=target)
    else:
        acc = latest_acceptance_for(review, technical_item=target)
    return treatment_status(acc, final_risk, today), acc
