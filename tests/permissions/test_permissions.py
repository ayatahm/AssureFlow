"""Role + assignment + state authorisation and separation of duties (spec 22, 30.2)."""
import pytest

from apps.accounts.models import Role
from apps.assurance.models import ConclusionStatus
from apps.assurance.services import compliance, conclusions, findings
from apps.core.errors import ServiceError
from apps.core.permissions import PermissionDenied, can, visible_reviews
from apps.reviews.models import Applicability
from tests.conftest import full_login, refresh
from tests.workflow.helpers import make_gap, ready_to_complete, request_acceptance

pytestmark = pytest.mark.django_db


def test_unknown_action_fails_closed(draft_review, people):
    assert not can(people["reviewer"], "review.delete_everything", draft_review)


def test_visibility_is_assignment_filtered(draft_review, people):
    assert draft_review in visible_reviews(people["requester"])
    assert draft_review in visible_reviews(people["reviewer"])
    assert draft_review not in visible_reviews(people["outsider"])
    assert not visible_reviews(people["admin"]).exists()


def test_unassigned_user_gets_404_for_review_trace_and_evidence(client, in_review, people):
    rr = in_review.requirements.filter(applicability=Applicability.KEEP).first()
    full_login(client, people["outsider"])
    for url in (f"/reviews/{in_review.pk}/", f"/reviews/{in_review.pk}/controls/{rr.pk}/trace/",
                f"/reviews/{in_review.pk}/evidence/", f"/reviews/{in_review.pk}/report/",
                f"/reviews/{in_review.pk}/workbench/"):
        assert client.get(url).status_code == 404, url


def test_requester_cannot_conclude_grant_exception_or_close(in_review, people):
    requester, reviewer = people["requester"], people["reviewer"]
    rr = in_review.requirements.get(control__code="SYN-AC-01")
    with pytest.raises(PermissionDenied):
        conclusions.record_conclusion(requester, in_review, rr, ConclusionStatus.VERIFIED)
    ready_to_complete(in_review, reviewer)
    rr, f, action = make_gap(in_review, reviewer, requester)
    with pytest.raises(PermissionDenied):
        compliance.record_compliance(requester, in_review, rr, "EXCEPTION_GRANTED", "please")
    compliance.record_compliance(requester, in_review, rr, "WILL_COMPLY", "")  # intent is permitted
    findings.submit_action(requester, action, "done")
    with pytest.raises(PermissionDenied):
        findings.close_action(requester, action)


def test_reviewer_cannot_verify_own_authored_evidence(in_review, people):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.assurance.services.evidence import upload_evidence
    from apps.administration.services import assign

    reviewer, reviewer2 = people["reviewer"], people["reviewer2"]
    rr = in_review.requirements.get(control__code="SYN-AC-01")
    upload_evidence(reviewer, in_review, rr, SimpleUploadedFile("p.pdf", b"%PDF-1.4 x"))
    with pytest.raises(PermissionDenied):
        conclusions.record_conclusion(reviewer, in_review, rr, ConclusionStatus.VERIFIED)
    conclusions.record_conclusion(reviewer, in_review, rr, ConclusionStatus.EVIDENCE_REQUIRED, "need more")
    assign(people["admin"], in_review.pk, reviewer2, "REVIEWER")
    conclusions.record_conclusion(reviewer2, in_review, rr, ConclusionStatus.VERIFIED)


def test_reviewer_who_answered_on_behalf_cannot_verify(assessment_review, people):
    from apps.reviews.services.assessment import save_answer, submit_assessment
    from tests.conftest import answer_all

    reviewer = people["reviewer"]
    answer_all(assessment_review, people["requester"])
    rq = assessment_review.questions.get(question__code="AQ-01")
    save_answer(reviewer, assessment_review, rq, "UNSURE", "clarified with the project team")
    r = submit_assessment(reviewer, assessment_review)
    rr = r.requirements.get(control__code="SYN-AC-01")
    assert not can(reviewer, "conclusion.verify", r, rr)
    with pytest.raises(PermissionDenied):
        conclusions.record_conclusion(reviewer, r, rr, ConclusionStatus.VERIFIED)


def test_nobody_closes_own_action(in_review, people):
    reviewer, requester = people["reviewer"], people["requester"]
    rr, f, action = make_gap(in_review, reviewer, requester)
    with pytest.raises(ServiceError):
        findings.add_action(reviewer, f, owner=reviewer, due_date=action.due_date, expected_outcome="x")
    findings.submit_action(requester, action, "done")
    with pytest.raises(PermissionDenied):
        findings.close_action(requester, action)
    findings.close_action(reviewer, action)
    action.refresh_from_db()
    assert action.state == "CLOSED"
    # closing did not verify the control
    from apps.assurance.services.status import current_conclusion

    assert current_conclusion(rr).status == ConclusionStatus.SECURITY_GAP
    findings.reopen_action(reviewer, action, "regressed")
    action.refresh_from_db()
    assert action.state == "OPEN"


def test_business_owner_sees_only_assigned_acceptance_items(client, in_review, people):
    reviewer, requester, owner = people["reviewer"], people["requester"], people["owner"]
    ready_to_complete(in_review, reviewer)
    rr, _, _ = make_gap(in_review, reviewer, requester)
    other_rr = in_review.requirements.get(control__code="SYN-DP-02")
    request_acceptance(in_review, reviewer, owner, requirements=[rr])
    full_login(client, owner)
    assert client.get(f"/reviews/{in_review.pk}/controls/{rr.pk}/trace/").status_code == 200
    assert client.get(f"/reviews/{in_review.pk}/controls/{other_rr.pk}/trace/").status_code == 404
    assert client.get(f"/reviews/{in_review.pk}/workbench/").status_code == 403
    assert client.get(f"/reviews/{in_review.pk}/audit/").status_code == 403


def test_requester_projection_excludes_workbench_and_audit(client, in_review, people):
    full_login(client, people["requester"])
    assert client.get(f"/reviews/{in_review.pk}/workbench/").status_code == 403
    assert client.get(f"/reviews/{in_review.pk}/audit/").status_code == 403
    rq = in_review.questions.first()
    resp = client.get(f"/reviews/{in_review.pk}/questions/{rq.pk}/why/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Workbench" not in body.split('class="tabs"')[1].split("</nav>")[0]
    assert "skip_when" not in body and "classification_config" not in body


def test_requester_sees_recorded_findings_and_conclusions(client, in_review, people):
    reviewer, requester = people["reviewer"], people["requester"]
    rr, f, action = make_gap(in_review, reviewer, requester)
    full_login(client, requester)
    body = client.get(f"/reviews/{in_review.pk}/controls/{rr.pk}/trace/").content.decode()
    assert "Security Gap" in body and f.title in body
    assert client.get(f"/reviews/{in_review.pk}/findings/{f.pk}/").status_code == 200
    assert client.get(f"/actions/{action.pk}/").status_code == 200


def test_removed_assignment_enforced_on_next_request(client, draft_review, people):
    from apps.reviews.models import ReviewAssignment

    full_login(client, people["requester"])
    assert client.get(f"/reviews/{draft_review.pk}/").status_code == 200
    ReviewAssignment.objects.filter(review=draft_review, user=people["requester"]).delete()
    assert client.get(f"/reviews/{draft_review.pk}/").status_code == 404


def test_removed_application_role_disables_assignment(draft_review, people):
    requester = people["requester"]
    requester.groups.clear()
    requester.clear_role_cache()
    assert not can(requester, "review.view", draft_review)


def test_admin_has_no_assessment_access(client, in_review, people):
    full_login(client, people["admin"])
    rr = in_review.requirements.first()
    for url in (f"/reviews/{in_review.pk}/", f"/reviews/{in_review.pk}/assessment/",
                f"/reviews/{in_review.pk}/controls/{rr.pk}/trace/", f"/reviews/{in_review.pk}/report/",
                "/reviews/new/"):
        assert client.get(url).status_code in (403, 404), url
    assert client.get("/reviews/").content.decode().count("<td class=\"mono nowrap\">") == 0


def test_hidden_field_tampering_cannot_move_answers_between_reviews(client, in_review, assessment_review, people,
                                                                    dept):
    from apps.reviews.services.intake import create_review
    from tests.conftest import triage_and_generate

    other = triage_and_generate(create_review(people["requester2"], {**__import__("tests.conftest", fromlist=["x"]).intake_data(dept)},
                                              reviewer=people["reviewer"]), people["requester2"])
    foreign_q = other.questions.first()
    full_login(client, people["requester"])
    mine = triage_and_generate(create_review(people["requester"], __import__("tests.conftest", fromlist=["x"]).intake_data(dept),
                                             reviewer=people["reviewer"]), people["requester"])
    resp = client.post(f"/reviews/{mine.pk}/assessment/", {"question": foreign_q.pk, "answer": "NO"})
    assert resp.status_code == 404
    foreign_q.refresh_from_db()
    assert foreign_q.answer == ""
