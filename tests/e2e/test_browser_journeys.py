"""Critical browser journeys (spec 30.5), real email-code sign-in, no fake verification."""
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.reviews.models import Review, ReviewState
from tests.conftest import PASSWORD, extract_code

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def sign_in(page, live_server, user):
    page.goto(f"{live_server.url}/account/login/")
    page.fill("input[name=identifier]", user.email)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url("**/account/verify/")
    page.fill("input[name=code]", extract_code(mail.outbox[-1].body))
    page.click("button:has-text('Verify')")
    page.wait_for_url("**/dashboard/")


def test_requester_creates_and_submits_assessment(page, live_server, people, content, dept):
    requester, reviewer = people["requester"], people["reviewer"]
    sign_in(page, live_server, requester)
    page.click("text=+ New Security Review")
    page.fill("input[name=title]", "Browser journey system")
    page.check("input[name=request_type][value=EXISTING_CHANGE]")
    page.select_option("select[name=department]", str(dept.pk))
    page.fill("input[name=project_owner]", "Owner Name")
    page.fill("input[name=target_date]", (timezone.localdate() + timedelta(days=20)).isoformat())
    page.fill("textarea[name=purpose]", "Purpose text")
    page.fill("textarea[name=goals]", "Goals text")
    page.select_option("select[name=assigned_reviewer]", str(reviewer.pk))
    page.click("label:has-text('Application')")
    page.click("button[name=continue]")
    page.wait_for_url("**/triage/")
    for code, value in {"impact": "LOW", "value": "MODERATE"}.items():
        page.check(f"input[name={code}][value={value}]")
    for i in range(1, 9):
        page.check(f"input[name=T{i}][value={'YES' if i in (1, 5) else 'NO'}]")
    page.click("button:has-text('Save triage answers')")
    page.wait_for_selector("text=Classification preview")
    assert page.is_visible("text=Confidential")
    page.click("summary:has-text('Generate Assessment')")
    page.click("button:has-text('Confirm and generate')")
    page.wait_for_url("**/assessment/")
    count = page.locator(".question").count()
    assert count > 0
    for i in range(count):
        q = page.locator(".question").nth(i)
        q.locator("input[value=YES]").check()
        q.locator("button:has-text('Save answer')").click()
        page.wait_for_load_state()
    page.click("aside button:has-text('Submit for review')")
    page.wait_for_url("**/reviews/*/")
    review = Review.objects.get(title="Browser journey system")
    assert review.state == ReviewState.IN_REVIEW
    assert review.questions.exclude(answer="").count() == count


def test_reviewer_concludes_and_completes(page, live_server, people, in_review):
    reviewer = people["reviewer"]
    sign_in(page, live_server, reviewer)
    for rr in in_review.requirements.filter(applicability="KEEP"):
        page.goto(f"{live_server.url}/reviews/{in_review.pk}/controls/{rr.pk}/conclude/")
        page.check("input[name=status][value=VERIFIED]")
        page.fill("textarea[name=notes]", "Evidence reviewed")
        page.click("button:has-text('Record conclusion')")
        page.wait_for_selector("text=Conclusion recorded")
    page.goto(f"{live_server.url}/reviews/{in_review.pk}/technical/")
    page.click("button:has-text('Confirm scope')")
    page.wait_for_selector("text=Technical scope confirmed")
    page.goto(f"{live_server.url}/reviews/{in_review.pk}/risk/")
    page.select_option("select[name=inherent]", "MODERATE")
    page.select_option("select[name=residual]", "LOW")
    page.fill("textarea[name=rationale]", "Controls verified")
    page.click("button:has-text('Save risk record')")
    page.wait_for_selector("text=Risk judgement recorded")
    page.goto(f"{live_server.url}/reviews/{in_review.pk}/complete/")
    page.fill("textarea[name=final_summary]", "All applicable requirements verified.")
    page.click("summary:has-text('Complete Review')")
    page.click("button:has-text('Confirm completion')")
    page.wait_for_url("**/report/")
    assert page.is_visible("text=Requirements met")
    assert page.is_visible("text=Stored data matches recorded digest")
    in_review.refresh_from_db()
    assert in_review.state == ReviewState.COMPLETED


@pytest.mark.parametrize("decision, label", [("ACCEPTED", "Accept"), ("DECLINED", "Decline")])
def test_business_owner_decides(page, live_server, people, in_review, decision, label):
    from tests.workflow.helpers import make_gap, ready_to_complete, record_compliance, request_acceptance

    reviewer, requester, owner = people["reviewer"], people["requester"], people["owner"]
    ready_to_complete(in_review, reviewer)
    rr, _, _ = make_gap(in_review, reviewer, requester)
    record_compliance(in_review, reviewer, rr)
    acc = request_acceptance(in_review, reviewer, owner, requirements=[rr])
    sign_in(page, live_server, owner)
    page.click("text=Review & decide")
    assert page.is_visible("text=Accepting this risk does not mark the security control as Verified.")
    page.check(f"input[name=decision][value={decision}]")
    page.fill("textarea[name=note]", f"{label} recorded in browser test")
    page.click("button:has-text('Record decision')")
    page.wait_for_selector("text=Decision recorded")
    acc.refresh_from_db()
    in_review.refresh_from_db()
    assert acc.state == decision and in_review.state == ReviewState.IN_REVIEW
