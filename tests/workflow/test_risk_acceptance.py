"""RiskRecord authority and Formal Business Risk Acceptance rules (spec 13, 14)."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.assurance.models import RiskRecord
from apps.assurance.services import acceptance
from apps.assurance.services import status as st
from apps.audit.models import ImmutableRecordError
from apps.core.errors import ServiceError
from apps.core.permissions import PermissionDenied
from apps.reviews.models import ReviewState
from tests.conftest import refresh

from .helpers import complete, make_gap, ready_to_complete, record_compliance, record_risk, request_acceptance

pytestmark = pytest.mark.django_db


def test_risk_records_append_only_and_latest_is_current(in_review, people):
    r1 = record_risk(in_review, people["reviewer"], "HIGH")
    r2 = record_risk(in_review, people["reviewer"], "LOW")
    assert st.latest_risk(in_review) == r2
    assert RiskRecord.objects.filter(review=in_review).count() == 2
    with pytest.raises(ImmutableRecordError):
        r1.residual = "LOW"
        r1.save()


def test_only_reviewer_records_risk(in_review, people):
    with pytest.raises(PermissionDenied):
        record_risk(in_review, people["requester"])


def test_current_risk_is_displayed_presented_and_pinned(in_review, people):
    reviewer, requester, owner = people["reviewer"], people["requester"], people["owner"]
    ready_to_complete(in_review, reviewer)
    rr, _, _ = make_gap(in_review, reviewer, requester)
    record_compliance(in_review, reviewer, rr)
    current = st.latest_risk(in_review)
    acc = request_acceptance(in_review, reviewer, owner, requirements=[rr])
    assert acc.risk_record == current
    acceptance.decide_acceptance(owner, acc, "ACCEPTED", "ok")
    snap = complete(refresh(in_review), reviewer)
    assert refresh(in_review).final_risk_record == current
    assert snap.data["risk"]["id"] == current.pk
    assert snap.data["acceptances"][0]["risk_record_id"] == current.pk


def test_acceptance_request_validation(in_review, people):
    reviewer, requester, owner = people["reviewer"], people["requester"], people["owner"]
    ready_to_complete(in_review, reviewer)
    verified_rr = in_review.requirements.get(control__code="SYN-DP-02")
    with pytest.raises(ServiceError) as exc:
        request_acceptance(in_review, reviewer, owner, requirements=[verified_rr])
    assert any("not a current Security Gap" in e for e in exc.value.errors)
    rr, _, _ = make_gap(in_review, reviewer, requester)
    # Business Owner must hold the role and must not be a Reviewer on the review
    with pytest.raises(ServiceError):
        request_acceptance(in_review, reviewer, people["reviewer2"], requirements=[rr])
    with pytest.raises(ServiceError):
        request_acceptance(in_review, reviewer, owner, requirements=[rr], expiry_days=-1)
    today = timezone.localdate()
    with pytest.raises(ServiceError):
        acceptance.request_acceptance(
            reviewer, in_review, business_owner=owner, requirements=[rr], justification="j",
            compensating_controls="c", conditions="None", review_date=today + timedelta(days=90),
            expiry_date=today + timedelta(days=30),
        )


def test_business_owner_assigned_and_terms_locked(in_review, people):
    reviewer, requester, owner = people["reviewer"], people["requester"], people["owner"]
    ready_to_complete(in_review, reviewer)
    rr, _, _ = make_gap(in_review, reviewer, requester)
    acc = request_acceptance(in_review, reviewer, owner, requirements=[rr])
    r = refresh(in_review)
    assert r.state == ReviewState.AWAITING_ACCEPTANCE
    assert r.assignments.filter(user=owner, role="BUSINESS_OWNER").exists()
    # While awaiting, the Reviewer cannot change risk, conclusions or request more.
    with pytest.raises(PermissionDenied):
        record_risk(r, reviewer)
    with pytest.raises(PermissionDenied):
        request_acceptance(r, reviewer, owner, requirements=[rr])


def test_decision_rules(in_review, people, make_user):
    from apps.accounts.models import Role

    reviewer, requester, owner = people["reviewer"], people["requester"], people["owner"]
    ready_to_complete(in_review, reviewer)
    rr, _, _ = make_gap(in_review, reviewer, requester)
    acc = request_acceptance(in_review, reviewer, owner, requirements=[rr])
    other_owner = make_user(Role.BUSINESS_OWNER)
    for who in (requester, reviewer, other_owner):
        with pytest.raises(PermissionDenied):
            acceptance.decide_acceptance(who, acc, "ACCEPTED", "note")
    with pytest.raises(ServiceError):
        acceptance.decide_acceptance(owner, acc, "ACCEPTED", "")
    acceptance.decide_acceptance(owner, acc, "DECLINED", "Not acceptable")
    acc.refresh_from_db()
    assert acc.state == "DECLINED" and acc.decided_at
    with pytest.raises(PermissionDenied):
        acceptance.decide_acceptance(owner, acc, "ACCEPTED", "flip")


def test_accepting_does_not_verify_or_close(in_review, people):
    reviewer, requester, owner = people["reviewer"], people["requester"], people["owner"]
    ready_to_complete(in_review, reviewer)
    rr, _, action = make_gap(in_review, reviewer, requester)
    acc = request_acceptance(in_review, reviewer, owner, requirements=[rr])
    acceptance.decide_acceptance(owner, acc, "ACCEPTED", "ok")
    action.refresh_from_db()
    assert action.state == "OPEN"
    assert st.current_conclusion(rr).status == "SECURITY_GAP"


def test_reviewer_cannot_be_business_owner_of_own_review(in_review, people, make_user):
    from apps.accounts.models import Role

    dual = make_user(Role.REVIEWER, Role.BUSINESS_OWNER)
    from apps.administration import services as admin_services

    admin = people["admin"]
    admin_services.assign(admin, in_review.pk, dual, "REVIEWER")
    ready_to_complete(in_review, people["reviewer"])
    rr, _, _ = make_gap(in_review, people["reviewer"], people["requester"])
    with pytest.raises(ServiceError):
        request_acceptance(in_review, people["reviewer"], dual, requirements=[rr])
