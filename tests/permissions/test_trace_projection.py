"""Assurance Trace field projection per viewer (spec 16)."""
import pytest

from apps.reporting.queries import build_trace
from apps.reporting.services import project_report
from tests.conftest import full_login, refresh
from tests.workflow.helpers import complete, make_gap, ready_to_complete, record_compliance, request_acceptance

pytestmark = pytest.mark.django_db


def test_reviewer_trace_has_history_requester_does_not(in_review, people):
    reviewer, requester = people["reviewer"], people["requester"]
    rr, f, action = make_gap(in_review, reviewer, requester)
    full = build_trace(in_review, rr, viewer_role="REVIEWER")
    limited = build_trace(in_review, rr, viewer_role="REQUESTER")
    assert full["conclusion_history"] and full["framework_refs"]
    assert limited["conclusion_history"] == [] and limited["framework_refs"] == [] and limited["flags"] == []
    assert limited["conclusion"].status == "SECURITY_GAP"  # recorded conclusions stay visible
    assert limited["findings"][0]["finding"] == f


def test_trace_uses_pinned_risk_after_completion(in_review, people):
    from apps.assurance.services import status as st

    reviewer = people["reviewer"]
    ready_to_complete(in_review, reviewer)
    complete(refresh(in_review), reviewer)
    r = refresh(in_review)
    rr = st.applicable_requirements(r).first()
    t = build_trace(r, rr, viewer_role="REVIEWER")
    assert t["risk"] == r.final_risk_record and t["risk_pinned"]
    assert t["report_entry"]["id"] == rr.pk


def test_business_owner_report_projection(in_review, people):
    reviewer, requester, owner = people["reviewer"], people["requester"], people["owner"]
    ready_to_complete(in_review, reviewer)
    rr, _, _ = make_gap(in_review, reviewer, requester)
    record_compliance(in_review, reviewer, rr)
    acc = request_acceptance(in_review, reviewer, owner, requirements=[rr])
    from apps.assurance.services.acceptance import decide_acceptance

    decide_acceptance(owner, acc, "ACCEPTED", "ok")
    snap = complete(refresh(in_review), reviewer)
    projected = project_report(snap.data, role="BUSINESS_OWNER", user_id=owner.pk)
    assert [r["id"] for r in projected["requirements"]] == [rr.pk]
    assert len(projected["findings"]) == 1
    assert project_report(snap.data, role="BUSINESS_OWNER", user_id=people["requester2"].pk)["requirements"] == []
    assert project_report(snap.data, role=None) is None


def test_trace_page_renders_for_requester_without_workbench_link(client, in_review, people):
    rr = in_review.requirements.get(control__code="SYN-AC-01")
    full_login(client, people["requester"])
    body = client.get(f"/reviews/{in_review.pk}/controls/{rr.pk}/trace/").content.decode()
    assert "Why selected" in body and "Open in workbench" not in body and "Framework refs" not in body
