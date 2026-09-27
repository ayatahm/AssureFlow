"""End-to-end journey through the real views/templates with Django's test client.

Covers the three critical journeys (spec 30.5) at HTTP level; the Playwright
versions live in tests/e2e.
"""
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role
from apps.assurance.models import Action, BusinessRiskAcceptance, Finding
from apps.content.models import ScopeTag
from apps.reviews.models import Applicability, Review, ReviewState
from tests.conftest import TRIAGE_BROAD, full_login

pytestmark = pytest.mark.django_db


def ok(resp, code=200):
    assert resp.status_code == code, (resp.status_code, resp.content[:2000])
    return resp


def client_for(user):
    c = Client()
    full_login(c, user)
    return c


def test_full_journey_with_accept_and_decline(people, content, dept):
    requester, reviewer, owner = people["requester"], people["reviewer"], people["owner"]
    rc, vc, oc = client_for(requester), client_for(reviewer), client_for(owner)

    # --- Requester: intake -> triage -> generate -> answer -> evidence -> submit
    ok(rc.get("/dashboard/"))
    ok(rc.get("/reviews/new/"))
    tags = list(ScopeTag.objects.filter(code__in=["APPLICATION", "PERSONAL_DATA"]).values_list("pk", flat=True))
    resp = rc.post("/reviews/new/", {
        "title": "Payments gateway", "request_type": "NEW_VENDOR_CLOUD", "department": dept.pk,
        "project_owner": "Head of Payments", "target_date": (timezone.localdate() + timedelta(days=40)).isoformat(),
        "purpose": "Vendor-hosted gateway", "goals": "Accept card payments", "external_ref": "CHG-1",
        "assigned_reviewer": reviewer.pk, "scope_tags": tags, "continue": "1",
    })
    ok(resp, 302)
    review = Review.objects.get(title="Payments gateway")
    ok(rc.get(f"/reviews/{review.pk}/"))
    ok(rc.get(f"/reviews/{review.pk}/intake/"))
    ok(rc.get(f"/reviews/{review.pk}/triage/"))
    ok(rc.post(f"/reviews/{review.pk}/triage/", TRIAGE_BROAD), 302)
    body = ok(rc.get(f"/reviews/{review.pk}/triage/")).content.decode()
    assert "Confidential" in body and "Generate Assessment" in body
    ok(rc.post(f"/reviews/{review.pk}/generate/"), 302)
    review.refresh_from_db()
    assert review.state == ReviewState.IN_ASSESSMENT
    ok(rc.get(f"/reviews/{review.pk}/assessment/"))
    for rq in review.questions.all():
        ok(rc.post(f"/reviews/{review.pk}/assessment/", {"question": rq.pk, "answer": "YES", "comment": ""}), 302)
    rq = review.questions.get(question__code="AQ-01")
    ok(rc.get(f"/reviews/{review.pk}/questions/{rq.pk}/why/"))
    rr = review.requirements.get(control__code="SYN-AC-01")
    ok(rc.get(f"/reviews/{review.pk}/evidence/upload/?target=requirement:{rr.pk}"))
    ok(rc.post(f"/reviews/{review.pk}/evidence/upload/", {
        "target": f"requirement:{rr.pk}", "next": "https://evil.example/",
        "file": SimpleUploadedFile("policy.pdf", b"%PDF-1.4 access policy"), "comment": "Access policy",
    }), 302)
    ok(rc.get(f"/reviews/{review.pk}/evidence/"))
    ok(rc.get(f"/reviews/{review.pk}/technical/"))
    ok(rc.post(f"/reviews/{review.pk}/submit/"), 302)
    review.refresh_from_db()
    assert review.state == ReviewState.IN_REVIEW

    # --- Reviewer: workbench, conclusions, finding, scope, risk
    ok(vc.get(f"/reviews/{review.pk}/workbench/"))
    for f in ("asked", "kept_not_asked", "skipped", "flagged", "unreviewed", "VERIFIED"):
        ok(vc.get(f"/reviews/{review.pk}/workbench/?filter={f}&level=REQUIRED"))
    for req in review.requirements.filter(applicability=Applicability.KEEP):
        ok(vc.get(f"/reviews/{review.pk}/controls/{req.pk}/conclude/"))
        status = "SECURITY_GAP" if req.pk == rr.pk else "VERIFIED"
        ok(vc.post(f"/reviews/{review.pk}/controls/{req.pk}/conclude/", {"status": status, "notes": "Reviewed"}), 302)
    ok(vc.get(f"/reviews/{review.pk}/controls/{rr.pk}/trace/"))
    ok(vc.get(f"/reviews/{review.pk}/findings/new/?target=requirement:{rr.pk}"))
    ok(vc.post(f"/reviews/{review.pk}/findings/new/?target=requirement:{rr.pk}", {
        "target": f"requirement:{rr.pk}", "title": "Shared admin accounts", "description": "Admins share one login",
        "priority": "HIGH", "owner": requester.pk, "due_date": (timezone.localdate() + timedelta(days=14)).isoformat(),
        "expected_outcome": "Named admin accounts",
    }), 302)
    finding = Finding.objects.get(review=review)
    action = Action.objects.get(finding=finding)
    for tab in ("findings", "actions", "closed"):
        ok(vc.get(f"/reviews/{review.pk}/findings/?tab={tab}"))
    ok(vc.get(f"/reviews/{review.pk}/findings/{finding.pk}/"))
    item = review.technical_items.filter(included=True).first()
    ok(vc.get(f"/reviews/{review.pk}/technical/"))
    ok(vc.get(f"/reviews/{review.pk}/technical/{item.pk}/"))
    ok(vc.post(f"/reviews/{review.pk}/technical/{item.pk}/", {"included": "on", "result": "NO_ISSUE", "notes": "ok"}), 302)
    ok(vc.post(f"/reviews/{review.pk}/scope/confirm/", {"tags": tags, "note": ""}), 302)
    review.refresh_from_db()
    assert review.scope_confirmed_at
    ok(vc.get(f"/reviews/{review.pk}/risk/"))
    ok(vc.post(f"/reviews/{review.pk}/risk/", {"inherent": "HIGH", "residual": "MODERATE", "rationale": "Judged"}), 302)
    ok(vc.get(f"/reviews/{review.pk}/compliance/{rr.pk}/"))
    ok(vc.post(f"/reviews/{review.pk}/compliance/{rr.pk}/", {"decision": "EXCEPTION_GRANTED", "note": "Vendor limit"}), 302)
    body = ok(vc.get(f"/reviews/{review.pk}/complete/")).content.decode()
    assert "Open with no alternative treatment" in body
    ok(vc.get(f"/reviews/{review.pk}/report/"))  # draft preview
    ok(vc.get(f"/reviews/{review.pk}/audit/"))

    # --- Requester: remediation evidence on the action, sees the finding
    ok(rc.get(f"/actions/{action.pk}/"))
    ok(rc.post(f"/reviews/{review.pk}/evidence/upload/", {
        "target": f"action:{action.pk}", "file": SimpleUploadedFile("fix.png", b"\x89PNG\r\n\x1a\n0000"),
    }), 302)

    # --- Reviewer: request acceptance
    ok(vc.get(f"/reviews/{review.pk}/acceptance/"))
    today = timezone.localdate()
    ok(vc.post(f"/reviews/{review.pk}/acceptance/", {
        "requirements": [rr.pk], "business_owner": owner.pk, "justification": "Go-live date fixed",
        "compensating_controls": "Session recording", "conditions": "Fix within 90 days",
        "review_date": (today + timedelta(days=30)).isoformat(), "expiry_date": (today + timedelta(days=90)).isoformat(),
    }), 302)
    review.refresh_from_db()
    assert review.state == ReviewState.AWAITING_ACCEPTANCE
    acc = BusinessRiskAcceptance.objects.get(review=review)

    # --- Business Owner: declines
    body = ok(oc.get("/dashboard/")).content.decode()
    assert acc.code in body
    body = ok(oc.get(f"/acceptances/{acc.pk}/decide/")).content.decode()
    assert "does not mark the security control as Verified" in body
    ok(oc.get(f"/reviews/{review.pk}/"))
    ok(oc.get(f"/reviews/{review.pk}/controls/{rr.pk}/trace/"))
    ok(oc.post(f"/acceptances/{acc.pk}/decide/", {"decision": "DECLINED", "note": "Too risky"}), 302)
    review.refresh_from_db()
    assert review.state == ReviewState.IN_REVIEW

    # --- Reviewer completes: Requirements not met
    ok(vc.get(f"/reviews/{review.pk}/complete/"))
    ok(vc.post(f"/reviews/{review.pk}/complete/", {"final_summary": "Declined exposure remains",
                                                   "conditions": "None"}), 302)
    review.refresh_from_db()
    assert review.state == ReviewState.COMPLETED and review.outcome == "NOT_MET"
    for c in (rc, vc, oc):
        body = ok(c.get(f"/reviews/{review.pk}/report/")).content.decode()
        assert "Requirements not met" in body and "Stored data matches recorded digest" in body
    body = ok(oc.get(f"/reviews/{review.pk}/report/")).content.decode()
    assert "Business Owner view" in body
    ok(vc.get(f"/reviews/{review.pk}/controls/{rr.pk}/trace/"))
    ok(rc.get("/reviews/?state=COMPLETED"))


def test_business_owner_accepts(people, content, dept, in_review):
    from tests.workflow.helpers import make_gap, ready_to_complete, record_compliance, request_acceptance

    reviewer, requester, owner = people["reviewer"], people["requester"], people["owner"]
    ready_to_complete(in_review, reviewer)
    rr, _, _ = make_gap(in_review, reviewer, requester)
    record_compliance(in_review, reviewer, rr)
    acc = request_acceptance(in_review, reviewer, owner, requirements=[rr])
    oc = client_for(owner)
    ok(oc.post(f"/acceptances/{acc.pk}/decide/", {"decision": "ACCEPTED", "note": "Accepted until fix"}), 302)
    vc = client_for(reviewer)
    ok(vc.post(f"/reviews/{in_review.pk}/complete/", {"final_summary": "Accepted", "conditions": "Fix in Q1"}), 302)
    in_review.refresh_from_db()
    assert in_review.outcome == "MET_WITH_EXCEPTIONS"


def test_action_submit_close_views(people, in_review):
    from tests.workflow.helpers import make_gap

    reviewer, requester = people["reviewer"], people["requester"]
    rr, f, action = make_gap(in_review, reviewer, requester)
    rc, vc = client_for(requester), client_for(reviewer)
    ok(rc.post(f"/actions/{action.pk}/submit/", {"note": "Implemented named accounts"}), 302)
    action.refresh_from_db()
    assert action.state == "SUBMITTED"
    ok(vc.get(f"/actions/{action.pk}/"))
    ok(vc.post(f"/actions/{action.pk}/close/", {"decision": "return", "note": "Need screenshot"}), 302)
    action.refresh_from_db()
    assert action.state == "OPEN"
    ok(rc.post(f"/actions/{action.pk}/submit/", {"note": "Added"}), 302)
    ok(vc.post(f"/actions/{action.pk}/close/", {"decision": "close", "note": "Verified"}), 302)
    action.refresh_from_db()
    assert action.state == "CLOSED"
    ok(vc.post(f"/actions/{action.pk}/reopen/", {"note": "Regressed"}), 302)
    action.refresh_from_db()
    assert action.state == "OPEN"
    # Requester cannot close
    assert rc.post(f"/actions/{action.pk}/close/", {"decision": "close"}).status_code == 403


def test_reason_pages_and_withdraw(people, assessment_review):
    vc = client_for(people["reviewer"])
    ok(vc.get(f"/reviews/{assessment_review.pk}/return-to-draft/"))
    ok(vc.post(f"/reviews/{assessment_review.pk}/return-to-draft/", {"reason": "Wrong triage"}), 302)
    assessment_review.refresh_from_db()
    assert assessment_review.state == ReviewState.DRAFT
    ok(vc.get(f"/reviews/{assessment_review.pk}/withdraw/"))
    ok(vc.post(f"/reviews/{assessment_review.pk}/withdraw/", {"reason": "Cancelled"}), 302)
    assessment_review.refresh_from_db()
    assert assessment_review.state == ReviewState.WITHDRAWN
    ok(vc.get(f"/reviews/{assessment_review.pk}/"))


def test_admin_pages(people, in_review):
    ac = client_for(people["admin"])
    ok(ac.get("/dashboard/"))
    ok(ac.get("/administration/review-assignments/"))
    ok(ac.post("/administration/review-assignments/", {"review": in_review.pk, "user": people["reviewer2"].pk,
                                                         "role": "REVIEWER"}), 302)
    assert in_review.assignments.filter(user=people["reviewer2"]).exists()
    ok(ac.get("/administration/account-events/"))


def test_error_pages_render(client, people):
    full_login(client, people["requester"])
    resp = client.get("/reviews/999999/")
    assert resp.status_code == 404 and b"Not found" in resp.content
