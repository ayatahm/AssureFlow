from datetime import timedelta

from django.utils import timezone

from apps.assurance.models import ConclusionStatus
from apps.assurance.services import acceptance, compliance, completion, conclusions, findings, risk
from apps.assurance.services import status as st
from apps.content.models import ScopeTag
from apps.reviews.services import technical


def conclude_all(review, reviewer, status=ConclusionStatus.VERIFIED, notes=""):
    for rr in st.applicable_requirements(review):
        conclusions.record_conclusion(reviewer, review, rr, status, notes or "checked")


def confirm_scope(review, reviewer, codes=("APPLICATION", "API")):
    return technical.confirm_scope(reviewer, review, ScopeTag.objects.filter(code__in=codes), "")


def record_risk(review, reviewer, residual="MODERATE"):
    return risk.record_risk(reviewer, review, inherent="HIGH", residual=residual, rationale="Reviewer judgement")


def ready_to_complete(review, reviewer):
    conclude_all(review, reviewer)
    confirm_scope(review, reviewer)
    record_risk(review, reviewer)


def complete(review, reviewer):
    return completion.complete_review(reviewer, review, final_summary="Summary", conditions="None")


def make_gap(review, reviewer, requester, code="SYN-AC-01", with_action=True):
    rr = review.requirements.get(control__code=code)
    conclusions.record_conclusion(reviewer, review, rr, ConclusionStatus.SECURITY_GAP, "Gap found")
    f = findings.raise_finding(reviewer, review, rr, title="Gap", description="Desc", priority="HIGH")
    action = None
    if with_action:
        action = findings.add_action(reviewer, f, owner=requester, due_date=timezone.localdate() + timedelta(days=10),
                                     expected_outcome="Fix it")
    return rr, f, action


def request_acceptance(review, reviewer, owner, requirements=(), technical_items=(), expiry_days=180):
    today = timezone.localdate()
    return acceptance.request_acceptance(
        reviewer, review, business_owner=owner, requirements=requirements, technical_items=technical_items,
        justification="Business need", compensating_controls="Monitoring", conditions="None",
        review_date=today + timedelta(days=30), expiry_date=today + timedelta(days=expiry_days),
    )


def record_compliance(review, actor, rr, decision="WILL_NOT_COMPLY"):
    return compliance.record_compliance(actor, review, rr, decision, "Cannot implement before go-live")
