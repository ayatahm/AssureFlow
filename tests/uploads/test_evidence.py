"""Upload security and EvidenceLink integrity (spec 9, 30.3)."""
import hashlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from apps.assurance.models import Conclusion, Evidence, EvidenceLink, EvidencePolicy
from apps.assurance.services import evidence as ev
from apps.assurance.services import findings
from apps.audit.models import AuditEvent
from apps.core.errors import ServiceError
from apps.core.permissions import PermissionDenied
from apps.reviews.models import Applicability
from tests.conftest import full_login, intake_data, refresh, triage_and_generate
from tests.workflow.helpers import make_gap, ready_to_complete

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
EXE = b"MZ\x90\x00" + b"\x00" * 64


def upload(actor, review, target, content=PDF, name="doc.pdf"):
    return ev.upload_evidence(actor, review, target, SimpleUploadedFile(name, content, content_type="application/pdf"))


def rr_of(review):
    return review.requirements.filter(applicability=Applicability.KEEP).first()


def test_valid_pdf_upload_hash_and_private_storage(assessment_review, people, settings):
    link = upload(people["requester"], assessment_review, rr_of(assessment_review))
    e = link.evidence
    assert e.sha256 == hashlib.sha256(PDF).hexdigest()
    path = ev.evidence_path(e)
    assert path.read_bytes() == PDF
    assert str(path).startswith(str(settings.EVIDENCE_ROOT))
    assert e.stored_name != "doc.pdf" and len(e.stored_name) == 32
    assert AuditEvent.objects.filter(action="evidence.uploaded", object_id=str(e.pk)).exists()


def test_png_accepted(assessment_review, people):
    assert upload(people["requester"], assessment_review, rr_of(assessment_review), PNG, "shot.png").evidence.content_type == "image/png"


def test_executable_renamed_as_pdf_rejected(assessment_review, people, settings):
    with pytest.raises(ServiceError):
        upload(people["requester"], assessment_review, rr_of(assessment_review), EXE, "invoice.pdf")
    assert not Evidence.objects.exists()
    assert not any(settings.EVIDENCE_ROOT.glob("*")) if settings.EVIDENCE_ROOT.exists() else True


def test_disallowed_signature_by_policy(assessment_review, people):
    policy = EvidencePolicy.objects.get()
    policy.allowed_types = ["application/pdf"]
    policy.save()
    with pytest.raises(ServiceError):
        upload(people["requester"], assessment_review, rr_of(assessment_review), PNG, "x.png")


def test_oversize_rejected(assessment_review, people):
    policy = EvidencePolicy.objects.get()
    policy.max_bytes = 1024
    policy.save()
    with pytest.raises(ServiceError):
        upload(people["requester"], assessment_review, rr_of(assessment_review), PDF + b"0" * 2048)


def test_traversal_filename_does_not_change_storage_path(assessment_review, people, settings):
    link = upload(people["requester"], assessment_review, rr_of(assessment_review), PDF, "../../etc/passwd.pdf")
    e = link.evidence
    assert e.original_name == "passwd.pdf"
    assert ev.evidence_path(e).parent == settings.EVIDENCE_ROOT


def test_upload_never_creates_conclusion(assessment_review, people):
    upload(people["requester"], assessment_review, rr_of(assessment_review))
    assert not Conclusion.objects.exists()


def test_link_target_constraint_zero_two_three(assessment_review, people):
    link = upload(people["requester"], assessment_review, rr_of(assessment_review))
    item = assessment_review.technical_items.first()
    for kwargs in ({}, {"review_requirement": rr_of(assessment_review), "technical_item": item}):
        with pytest.raises(IntegrityError), transaction.atomic():
            EvidenceLink.objects.create(evidence=link.evidence, linked_by=people["requester"], **kwargs)


def test_link_with_three_targets_rejected(in_review, people):
    reviewer, requester = people["reviewer"], people["requester"]
    rr, finding, action = make_gap(in_review, reviewer, requester)
    e = upload(reviewer, in_review, rr).evidence
    with pytest.raises(IntegrityError), transaction.atomic():
        EvidenceLink.objects.create(evidence=e, linked_by=reviewer, review_requirement=rr,
                                    technical_item=in_review.technical_items.first(), action=action)


def test_duplicate_link_rejected(assessment_review, people):
    rr = rr_of(assessment_review)
    link = upload(people["requester"], assessment_review, rr)
    with pytest.raises(ServiceError):
        ev.link_existing(people["requester"], assessment_review, link.evidence, rr)


def test_cross_review_target_denied_before_writing(assessment_review, people, dept, settings):
    from apps.reviews.services.intake import create_review

    other = create_review(people["requester2"], intake_data(dept), reviewer=people["reviewer"])
    other = triage_and_generate(other, people["requester2"])
    foreign_rr = rr_of(other)
    with pytest.raises(PermissionDenied):
        upload(people["requester"], assessment_review, foreign_rr)
    with pytest.raises(ServiceError):
        ev.resolve_target(assessment_review, "requirement", foreign_rr.pk)
    assert not Evidence.objects.exists()


def test_cross_review_action_id_denied(in_review, people, dept):
    from apps.reviews.services.assessment import submit_assessment
    from apps.reviews.services.intake import create_review
    from tests.conftest import answer_all

    reviewer, requester = people["reviewer"], people["requester"]
    other = create_review(requester, intake_data(dept), reviewer=reviewer)
    other = triage_and_generate(other, requester)
    answer_all(other, requester)
    other = submit_assessment(requester, other)
    _, _, foreign_action = make_gap(other, reviewer, requester)
    with pytest.raises(ServiceError):
        ev.resolve_target(in_review, "action", foreign_action.pk)
    with pytest.raises(PermissionDenied):
        upload(requester, in_review, foreign_action)


def test_remediation_evidence_on_action_and_trace(in_review, people):
    from apps.reporting.queries import build_trace

    reviewer, requester = people["reviewer"], people["requester"]
    rr, finding, action = make_gap(in_review, reviewer, requester)
    link1 = upload(requester, in_review, action, PDF, "fix1.pdf")
    assert link1.action == action
    findings.submit_action(requester, action, "first attempt", [link1.pk])
    findings.return_action(reviewer, action, "not enough")
    link2 = upload(requester, in_review, action, PNG, "fix2.png")
    findings.submit_action(requester, action, "second attempt", [link1.pk, link2.pk])
    kinds = list(action.history.values_list("kind", "evidence_link_ids"))
    assert ("SUBMITTED", [link1.pk]) in kinds and ("SUBMITTED", [link1.pk, link2.pk]) in kinds
    audits = AuditEvent.objects.filter(action="action.submitted", object_id=str(action.pk))
    assert [a.detail["evidence_link_ids"] for a in audits] == [[link1.pk], [link1.pk, link2.pk]]
    trace = build_trace(in_review, rr, viewer_role="REVIEWER")
    names = [l.evidence.original_name for fx in trace["findings"] for ax in fx["actions"] for l in ax["evidence"]]
    assert names == ["fix1.pdf", "fix2.png"]


def test_submission_cannot_reference_other_actions_evidence(in_review, people):
    reviewer, requester = people["reviewer"], people["requester"]
    rr, f, a1 = make_gap(in_review, reviewer, requester, "SYN-AC-01")
    _, _, a2 = make_gap(in_review, reviewer, requester, "SYN-DP-02")
    l2 = upload(requester, in_review, a2)
    with pytest.raises(ServiceError):
        findings.submit_action(requester, a1, "done", [l2.pk])


def test_only_action_owner_uploads_remediation(in_review, people):
    reviewer, requester = people["reviewer"], people["requester"]
    rr, f, action = make_gap(in_review, reviewer, requester)
    with pytest.raises(PermissionDenied):
        upload(reviewer, in_review, action)


def test_download_private_and_audited(client, assessment_review, people):
    link = upload(people["requester"], assessment_review, rr_of(assessment_review))
    url = f"/evidence/{link.evidence.pk}/download/"
    assert client.get(url).status_code == 302  # anonymous -> login
    full_login(client, people["outsider"])
    assert client.get(url).status_code == 404
    client.logout()
    full_login(client, people["requester"])
    resp = client.get(url)
    assert resp.status_code == 200
    assert "attachment" in resp["Content-Disposition"]
    assert b"".join(resp.streaming_content) == PDF
    assert AuditEvent.objects.filter(action="evidence.downloaded", object_id=str(link.evidence.pk)).count() == 1


def test_no_media_url_exposes_evidence(client, assessment_review, people):
    link = upload(people["requester"], assessment_review, rr_of(assessment_review))
    full_login(client, people["requester"])
    assert client.get(f"/evidence/{link.evidence.stored_name}").status_code == 404
    assert client.get(f"/media/{link.evidence.stored_name}").status_code == 404


def test_verify_evidence_command(assessment_review, people):
    from django.core.management import CommandError, call_command

    link = upload(people["requester"], assessment_review, rr_of(assessment_review))
    call_command("verify_evidence")
    path = ev.evidence_path(link.evidence)
    path.write_bytes(PDF + b"tampered")
    with pytest.raises(CommandError):
        call_command("verify_evidence")
    path.unlink()
    with pytest.raises(CommandError):
        call_command("verify_evidence")
