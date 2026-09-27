"""Workflow through services: allowed and refused transitions (spec 5, 30.4)."""
import pytest

from apps.assurance.models import ConclusionStatus
from apps.assurance.services import conclusions
from apps.audit.models import AuditEvent
from apps.core.errors import ServiceError
from apps.core.permissions import PermissionDenied
from apps.reviews.models import Applicability, ReviewFlag, ReviewState
from apps.reviews.services import assessment, generation, intake, triage
from tests.conftest import TRIAGE_BROAD, TRIAGE_MINIMAL, answer_all, intake_data, refresh, triage_and_generate

from .helpers import complete, confirm_scope, ready_to_complete, record_risk

pytestmark = pytest.mark.django_db


def test_create_review_assigns_participants_and_audits(draft_review, people):
    assert draft_review.state == ReviewState.DRAFT
    assert draft_review.reference.startswith("AF-")
    roles = dict(draft_review.assignments.values_list("user_id", "role"))
    assert roles[people["requester"].pk] == "REQUESTER" and roles[people["reviewer"].pk] == "REVIEWER"
    assert AuditEvent.objects.filter(review=draft_review, action="review.created").exists()


def test_reviewer_creates_on_behalf(content, people, dept):
    review = intake.create_review(people["reviewer"], intake_data(dept), reviewer=people["reviewer"],
                                  requester=people["requester"])
    assert review.on_behalf and review.created_by == people["reviewer"]


def test_requester_cannot_be_own_reviewer(content, make_user, dept):
    from apps.accounts.models import Role

    both = make_user(Role.REQUESTER, Role.REVIEWER)
    with pytest.raises(ServiceError):
        intake.create_review(both, intake_data(dept), reviewer=both)


def test_request_type_has_three_values():
    from apps.reviews.models import RequestType

    assert set(RequestType.values) == {"NEW_SYSTEM", "EXISTING_CHANGE", "NEW_VENDOR_CLOUD"}


def test_generation_requires_complete_triage(draft_review, people):
    triage.save_triage(people["requester"], draft_review, {"impact": "LOW"})
    with pytest.raises(ServiceError) as exc:
        generation.generate_assessment(people["requester"], draft_review)
    assert any("Triage answers missing" in e for e in exc.value.errors)


def test_generation_pins_versions_and_creates_rows(assessment_review, content):
    rs, cat = content
    r = assessment_review
    assert r.state == ReviewState.IN_ASSESSMENT
    assert r.rule_set == rs and r.technical_catalogue == cat
    assert r.classification == "CONFIDENTIAL"
    assert r.requirements.count() == rs.controls.count()  # skipped rows are kept for explainability
    assert r.requirements.filter(applicability=Applicability.SKIP).exists()
    codes = list(r.questions.values_list("question__code", flat=True))
    assert len(codes) == len(set(codes))
    assert r.technical_items.exists()
    assert AuditEvent.objects.filter(review=r, action="assessment.generated").exists()


def test_triage_locked_after_generation(assessment_review, people):
    with pytest.raises(PermissionDenied):
        triage.save_triage(people["requester"], assessment_review, {"impact": "HIGH"})


def test_minimal_triage_removes_questions(draft_review, people):
    r = triage_and_generate(draft_review, people["requester"], TRIAGE_MINIMAL)
    assert r.classification == "PUBLIC"
    total = r.rule_set.assessment_questions.count()
    assert r.questions.count() < total


def test_answer_flags(assessment_review, people):
    rq = assessment_review.questions.get(question__code="AQ-01")
    assessment.save_answer(people["requester"], assessment_review, rq, "NO")
    flag = ReviewFlag.objects.get(source_question=rq, resolved_at__isnull=True)
    assert flag.kind == "POTENTIAL_GAP"
    assessment.save_answer(people["requester"], assessment_review, rq, "UNSURE")
    assert ReviewFlag.objects.get(source_question=rq, resolved_at__isnull=True).kind == "CLARIFICATION"
    assessment.save_answer(people["requester"], assessment_review, rq, "YES")
    assert not ReviewFlag.objects.filter(source_question=rq, resolved_at__isnull=True).exists()
    # Flags never create conclusions
    assert not assessment_review.requirements.filter(conclusions__isnull=False).exists()


def test_reviewer_answer_on_behalf_records_provenance(assessment_review, people):
    rq = assessment_review.questions.first()
    assessment.save_answer(people["reviewer"], assessment_review, rq, "YES")
    rq.refresh_from_db()
    assert rq.on_behalf and rq.answered_by == people["reviewer"]


def test_submit_requires_all_answers(assessment_review, people):
    with pytest.raises(ServiceError):
        assessment.submit_assessment(people["requester"], assessment_review)
    answer_all(assessment_review, people["requester"])
    r = assessment.submit_assessment(people["requester"], assessment_review)
    assert r.state == ReviewState.IN_REVIEW
    with pytest.raises(PermissionDenied):
        assessment.save_answer(people["requester"], r, r.questions.first(), "NO")


def test_return_to_draft_before_assurance_records(assessment_review, people):
    rs_version = assessment_review.rule_set.version
    with pytest.raises(PermissionDenied):
        assessment.return_to_draft(people["requester"], assessment_review, "fix")
    r = assessment.return_to_draft(people["reviewer"], assessment_review, "Triage was wrong")
    assert r.state == ReviewState.DRAFT and r.requirements.count() == 0 and r.questions.count() == 0
    assert r.rule_set.version == rs_version  # versions stay pinned
    ev = AuditEvent.objects.filter(review=r, action="review.transition").last()
    assert ev.detail["rule_set"] == rs_version and ev.detail["reason"] == "Triage was wrong"
    # regeneration re-uses the pinned version
    r = triage_and_generate(r, people["requester"], TRIAGE_BROAD)
    assert r.rule_set.version == rs_version


def test_return_to_draft_refused_once_evidence_exists(assessment_review, people):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.assurance.services.evidence import upload_evidence

    rr = assessment_review.requirements.filter(applicability=Applicability.KEEP).first()
    upload_evidence(people["requester"], assessment_review, rr,
                    SimpleUploadedFile("a.pdf", b"%PDF-1.4 test", content_type="application/pdf"))
    with pytest.raises(ServiceError):
        assessment.return_to_draft(people["reviewer"], assessment_review, "reset")


def test_withdraw_rules(draft_review, people, assessment_review):
    # Requester may withdraw only while Draft
    with pytest.raises(PermissionDenied):
        assessment.withdraw(people["requester"], assessment_review, "no longer needed")
    r = assessment.withdraw(people["reviewer"], assessment_review, "cancelled")
    assert r.state == ReviewState.WITHDRAWN
    with pytest.raises(PermissionDenied):
        assessment.withdraw(people["reviewer"], r, "again")


def test_withdraw_requires_reason(content, people, dept):
    review = intake.create_review(people["requester"], intake_data(dept), reviewer=people["reviewer"])
    with pytest.raises(ServiceError):
        assessment.withdraw(people["requester"], review, "  ")
    assert assessment.withdraw(people["requester"], review, "dup").state == ReviewState.WITHDRAWN


def test_return_to_requester_requires_rereview_of_changed_answers(in_review, people):
    reviewer, requester = people["reviewer"], people["requester"]
    rr = in_review.requirements.get(control__code="SYN-AC-01")
    conclusions.record_conclusion(reviewer, in_review, rr, ConclusionStatus.VERIFIED, "")
    assessment.return_to_requester(reviewer, in_review, "Please clarify AQ-01")
    rq = in_review.questions.get(question__code="AQ-01")
    assessment.save_answer(requester, refresh(in_review), rq, "NO")
    from apps.assurance.services.status import conclusion_state

    current, latest = conclusion_state(rr)
    assert current is None and latest.status == ConclusionStatus.VERIFIED  # history kept, not carried over
    assessment.submit_assessment(requester, refresh(in_review))
    ready_to_complete(refresh(in_review), reviewer)  # re-review records a new conclusion
    assert conclusion_state(rr)[0] is not None


def test_completed_review_is_read_only(in_review, people):
    reviewer = people["reviewer"]
    ready_to_complete(in_review, reviewer)
    complete(refresh(in_review), reviewer)
    r = refresh(in_review)
    assert r.state == ReviewState.COMPLETED
    rr = r.requirements.filter(applicability=Applicability.KEEP).first()
    with pytest.raises(PermissionDenied):
        conclusions.record_conclusion(reviewer, r, rr, ConclusionStatus.SECURITY_GAP, "late")
    with pytest.raises(PermissionDenied):
        record_risk(r, reviewer)
    with pytest.raises(PermissionDenied):
        confirm_scope(r, reviewer)
    with pytest.raises(PermissionDenied):
        assessment.withdraw(reviewer, r, "late")
