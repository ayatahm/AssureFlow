"""Completion guards and neutral outcome — every example in spec 15.3 plus 30.4 regressions."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.assurance.models import ActionState, ConclusionStatus
from apps.assurance.services import acceptance, completion, conclusions, findings
from apps.assurance.services import status as st
from apps.core.errors import ServiceError
from apps.reviews.models import Outcome, ReviewState
from tests.conftest import refresh

from .helpers import (
    complete,
    conclude_all,
    confirm_scope,
    make_gap,
    ready_to_complete,
    record_compliance,
    record_risk,
    request_acceptance,
)

pytestmark = pytest.mark.django_db


def blockers(review):
    return completion.evaluate_completion(refresh(review)).blockers


def test_all_verified_completes_requirements_met(in_review, people):
    ready_to_complete(in_review, people["reviewer"])
    snap = complete(refresh(in_review), people["reviewer"])
    r = refresh(in_review)
    assert r.state == ReviewState.COMPLETED and r.outcome == Outcome.MET
    assert r.final_risk_record == st.latest_risk(r)
    assert snap.revision == 1 and snap.data["outcome"]["code"] == "MET"


def _gap_with_decision(in_review, people, decision):
    reviewer, requester, owner = people["reviewer"], people["requester"], people["owner"]
    ready_to_complete(in_review, reviewer)
    rr, finding, action = make_gap(in_review, reviewer, requester)
    record_compliance(in_review, reviewer, rr, "EXCEPTION_GRANTED")
    acc = request_acceptance(in_review, reviewer, owner, requirements=[rr])
    assert refresh(in_review).state == ReviewState.AWAITING_ACCEPTANCE
    acceptance.decide_acceptance(owner, acc, decision, "Decision note")
    assert refresh(in_review).state == ReviewState.IN_REVIEW
    return rr, action, acc


def test_gap_with_valid_acceptance_completes_met_with_exceptions(in_review, people):
    rr, action, acc = _gap_with_decision(in_review, people, "ACCEPTED")
    complete(refresh(in_review), people["reviewer"])
    r = refresh(in_review)
    assert r.outcome == Outcome.MET_WITH_EXCEPTIONS
    action.refresh_from_db()
    assert action.state == ActionState.OPEN  # not auto-closed
    assert st.current_conclusion(rr).status == ConclusionStatus.SECURITY_GAP  # not verified
    snap = r.snapshots.get()
    finding = snap.data["findings"][0]
    assert finding["actions"][0]["state"] == "OPEN"
    assert finding["actions"][0]["alternative_treatment"]["status"] == "ACCEPTED"


def test_gap_with_declined_acceptance_completes_not_met(in_review, people):
    rr, action, acc = _gap_with_decision(in_review, people, "DECLINED")
    complete(refresh(in_review), people["reviewer"])
    r = refresh(in_review)
    assert r.outcome == Outcome.NOT_MET
    action.refresh_from_db()
    assert action.state == ActionState.OPEN
    snap = r.snapshots.get()
    assert snap.data["acceptances"][0]["state"] == "DECLINED"


def test_mixed_accept_and_decline_is_not_met(in_review, people):
    reviewer, requester, owner = people["reviewer"], people["requester"], people["owner"]
    ready_to_complete(in_review, reviewer)
    rr1, _, _ = make_gap(in_review, reviewer, requester, "SYN-AC-01")
    rr2, _, _ = make_gap(in_review, reviewer, requester, "SYN-DP-02")
    for rr in (rr1, rr2):
        record_compliance(in_review, reviewer, rr)
    a1 = request_acceptance(in_review, reviewer, owner, requirements=[rr1])
    acceptance.decide_acceptance(owner, a1, "ACCEPTED", "ok")
    a2 = request_acceptance(refresh(in_review), reviewer, owner, requirements=[rr2])
    acceptance.decide_acceptance(owner, a2, "DECLINED", "no")
    complete(refresh(in_review), reviewer)
    assert refresh(in_review).outcome == Outcome.NOT_MET


def test_open_action_without_treatment_blocks_even_if_never_submitted(in_review, people):
    reviewer, requester = people["reviewer"], people["requester"]
    ready_to_complete(in_review, reviewer)
    rr, finding, action = make_gap(in_review, reviewer, requester)
    # Even with the control later verified, an untreated OPEN action blocks.
    conclusions.record_conclusion(reviewer, in_review, rr, ConclusionStatus.VERIFIED, "")
    assert any("is Open with no alternative treatment" in b for b in blockers(in_review))
    with pytest.raises(ServiceError):
        complete(refresh(in_review), reviewer)


def test_submitted_action_always_blocks(in_review, people):
    reviewer, requester = people["reviewer"], people["requester"]
    ready_to_complete(in_review, reviewer)
    rr, finding, action = make_gap(in_review, reviewer, requester)
    findings.submit_action(requester, action, "Fixed")
    assert any("Submitted" in b for b in blockers(in_review))
    findings.close_action(reviewer, action, "Checked")
    conclusions.record_conclusion(reviewer, in_review, rr, ConclusionStatus.VERIFIED, "")
    assert blockers(in_review) == []
    complete(refresh(in_review), reviewer)
    assert refresh(in_review).outcome == Outcome.MET


def test_pending_acceptance_blocks(in_review, people):
    reviewer, requester, owner = people["reviewer"], people["requester"], people["owner"]
    ready_to_complete(in_review, reviewer)
    rr, _, _ = make_gap(in_review, reviewer, requester)
    record_compliance(in_review, reviewer, rr)
    request_acceptance(in_review, reviewer, owner, requirements=[rr])
    r = refresh(in_review)
    assert r.state == ReviewState.AWAITING_ACCEPTANCE
    assert any("Pending" in b for b in blockers(r))


def test_evidence_required_blocks(in_review, people):
    reviewer = people["reviewer"]
    ready_to_complete(in_review, reviewer)
    rr = in_review.requirements.get(control__code="SYN-AC-01")
    conclusions.record_conclusion(reviewer, in_review, rr, ConclusionStatus.EVIDENCE_REQUIRED, "Need policy")
    assert any("Evidence Required" in b for b in blockers(in_review))


def test_missing_applicable_conclusion_blocks_including_optional(in_review, people):
    reviewer = people["reviewer"]
    confirm_scope(in_review, reviewer)
    record_risk(in_review, reviewer)
    applicable = list(st.applicable_requirements(in_review))
    for rr in applicable[:-1]:
        conclusions.record_conclusion(reviewer, in_review, rr, ConclusionStatus.VERIFIED, "")
    assert any(applicable[-1].control.code in b for b in blockers(in_review))
    # An Optional control that did not drive a question still needs a conclusion.
    optional = [rr for rr in applicable if rr.level == "OPTIONAL"]
    assert optional, "fixture should include optional applicable controls"


def test_unanswered_question_blocks(in_review, people):
    rq = in_review.questions.first()
    # simulate a presented but unanswered question (e.g. data repaired by admin)
    type(rq).objects.filter(pk=rq.pk).update(answer="")
    assert any("unanswered" in b for b in blockers(in_review))


def test_scope_must_be_confirmed_and_empty_scope_needs_reason(in_review, people):
    from apps.reviews.services import technical

    reviewer = people["reviewer"]
    conclude_all(in_review, reviewer)
    record_risk(in_review, reviewer)
    assert any("Technical scope" in b for b in blockers(in_review))
    with pytest.raises(ServiceError):
        technical.confirm_scope(reviewer, in_review, [], "")
    technical.confirm_scope(reviewer, in_review, [], "No technical components in scope")
    assert blockers(in_review) == []


def test_risk_record_required(in_review, people):
    reviewer = people["reviewer"]
    conclude_all(in_review, reviewer)
    confirm_scope(in_review, reviewer)
    assert any("risk" in b.lower() for b in blockers(in_review))


def test_expired_acceptance_blocks(in_review, people):
    reviewer, requester, owner = people["reviewer"], people["requester"], people["owner"]
    rr, action, acc = _gap_with_decision(in_review, people, "ACCEPTED")
    future = timezone.localdate() + timedelta(days=400)
    chk = completion.evaluate_completion(refresh(in_review), today=future)
    assert any("expired" in b.lower() for b in chk.blockers)


def test_acceptance_for_older_risk_record_blocks(in_review, people):
    reviewer = people["reviewer"]
    rr, action, acc = _gap_with_decision(in_review, people, "ACCEPTED")
    record_risk(in_review, reviewer, residual="HIGH")  # later reassessment
    b = blockers(in_review)
    assert any("older risk record" in x for x in b)


def test_technical_finding_without_treatment_blocks(in_review, people):
    reviewer, requester, owner = people["reviewer"], people["requester"], people["owner"]
    ready_to_complete(in_review, reviewer)
    item = in_review.technical_items.filter(included=True).first()
    f = findings.raise_finding(reviewer, in_review, item, title="TLS weak", description="d", priority="MODERATE")
    assert any(item.control.source_id in b for b in blockers(in_review))
    # Technical-only acceptance item, no invented requirement link
    acc = request_acceptance(in_review, reviewer, owner, technical_items=[item])
    assert acc.items.get().technical_item == item and acc.items.get().review_requirement is None
    acceptance.decide_acceptance(owner, acc, "ACCEPTED", "ok")
    complete(refresh(in_review), reviewer)
    assert refresh(in_review).outcome == Outcome.MET_WITH_EXCEPTIONS


def test_technical_finding_with_verified_remediation_resolves(in_review, people):
    reviewer, requester = people["reviewer"], people["requester"]
    ready_to_complete(in_review, reviewer)
    item = in_review.technical_items.filter(included=True).first()
    f = findings.raise_finding(reviewer, in_review, item, title="Weak", description="d", priority="LOW")
    a = findings.add_action(reviewer, f, owner=requester, due_date=timezone.localdate(), expected_outcome="fix")
    findings.submit_action(requester, a, "done")
    findings.close_action(reviewer, a, "ok")
    complete(refresh(in_review), reviewer)
    assert refresh(in_review).outcome == Outcome.MET


def test_gap_without_compliance_decision_blocks(in_review, people):
    reviewer, requester, owner = people["reviewer"], people["requester"], people["owner"]
    ready_to_complete(in_review, reviewer)
    rr, _, _ = make_gap(in_review, reviewer, requester)
    acc = request_acceptance(in_review, reviewer, owner, requirements=[rr])
    acceptance.decide_acceptance(owner, acc, "ACCEPTED", "ok")
    assert any("Project Compliance Decision" in b for b in blockers(in_review))


def test_reviewer_exception_alone_is_not_acceptance(in_review, people):
    reviewer, requester = people["reviewer"], people["requester"]
    ready_to_complete(in_review, reviewer)
    rr, _, _ = make_gap(in_review, reviewer, requester)
    record_compliance(in_review, reviewer, rr, "EXCEPTION_GRANTED")
    assert any("No treatment decision" in b for b in blockers(in_review))


def test_later_decline_overrides_older_acceptance(in_review, people):
    reviewer, owner = people["reviewer"], people["owner"]
    rr, action, acc = _gap_with_decision(in_review, people, "ACCEPTED")
    acc2 = request_acceptance(refresh(in_review), reviewer, owner, requirements=[rr])
    acceptance.decide_acceptance(owner, acc2, "DECLINED", "changed my mind")
    complete(refresh(in_review), reviewer)
    assert refresh(in_review).outcome == Outcome.NOT_MET


def test_completion_rollback_on_failure(in_review, people, monkeypatch):
    from apps.audit.models import AuditEvent
    from apps.reporting import services as reporting_services

    reviewer = people["reviewer"]
    ready_to_complete(in_review, reviewer)
    before = AuditEvent.objects.count()

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(reporting_services, "issue_snapshot", boom)
    with pytest.raises(RuntimeError):
        complete(refresh(in_review), reviewer)
    r = refresh(in_review)
    assert r.state == ReviewState.IN_REVIEW and r.final_risk_record is None
    assert AuditEvent.objects.count() == before
    assert not r.snapshots.exists()


def test_only_assigned_reviewer_completes(in_review, people):
    from apps.core.permissions import PermissionDenied

    ready_to_complete(in_review, people["reviewer"])
    for who in ("requester", "reviewer2", "owner", "admin"):
        with pytest.raises(PermissionDenied):
            complete(refresh(in_review), people[who])
