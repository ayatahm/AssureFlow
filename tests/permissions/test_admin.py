"""Restricted Django Admin and metadata-only administration (spec 3.4, 24.4, 30.2)."""
import pytest
from django.contrib.auth.models import Group

from apps.accounts.models import Role, User
from apps.administration.admin_site import ALLOWED_ADMIN_MODELS, site
from apps.administration.services import assign
from apps.audit.models import AuditEvent
from apps.core.errors import ServiceError
from tests.conftest import full_login

pytestmark = pytest.mark.django_db


def test_only_allow_listed_models_registered():
    assert set(site._registry) == ALLOWED_ADMIN_MODELS
    names = {m.__name__ for m in site._registry}
    for forbidden in ("Review", "Evidence", "AuditEvent", "Group", "Permission", "EmailDevice", "ReportSnapshot",
                      "RiskRecord", "Conclusion", "BusinessRiskAcceptance", "ReviewAssignment"):
        assert forbidden not in names


@pytest.fixture
def admin_client(client, people):
    full_login(client, people["admin"])
    return client


def test_forbidden_admin_routes_return_404(admin_client, in_review):
    for url in (f"/admin/reviews/review/{in_review.pk}/change/", "/admin/reviews/review/",
                "/admin/assurance/evidence/", "/admin/audit/auditevent/", "/admin/auth/group/",
                "/admin/otp_email/emaildevice/", "/admin/reporting/reportsnapshot/",
                "/admin/reviews/reviewassignment/", "/admin/password_change/"):
        assert admin_client.get(url).status_code in (404, 302), url


def test_allowed_admin_pages_render(admin_client, content):
    for url in ("/admin/", "/admin/accounts/user/", "/admin/accounts/department/", "/admin/accounts/user/add/",
                "/admin/content/ruleset/", "/admin/content/technicalcatalogue/", "/admin/assurance/evidencepolicy/",
                "/admin/accounts/securitysetting/"):
        assert admin_client.get(url).status_code == 200, url


def test_version_metadata_hides_rule_content(admin_client, content):
    rs, _ = content
    body = admin_client.get(f"/admin/content/ruleset/{rs.pk}/change/").content.decode()
    assert rs.version in body
    assert "skip_when" not in body and "[Demo] Will the system" not in body and "notes" not in body.lower().split("<form")[1][:0]


def test_non_admin_cannot_reach_admin(client, people):
    full_login(client, people["reviewer"])
    assert client.get("/admin/").status_code == 302
    assert client.get("/administration/review-assignments/").status_code == 403


def test_user_form_has_no_superuser_or_permissions(admin_client, people):
    body = admin_client.get(f"/admin/accounts/user/{people['requester'].pk}/change/").content.decode()
    assert "is_superuser" not in body and "user_permissions" not in body and 'name="groups"' not in body
    assert "otp" not in body.lower()


def test_admin_cannot_edit_technical_superuser(admin_client, db):
    su = User.objects.create_superuser("root@example.test", "ROOT1", "Root-Password-123!")
    assert admin_client.get(f"/admin/accounts/user/{su.pk}/change/").status_code in (302, 403, 404)
    assert "root@example.test" not in admin_client.get("/admin/accounts/user/").content.decode()


def test_admin_cannot_combine_admin_with_business_role(admin_client, people):
    target = people["requester"]
    resp = admin_client.post(f"/admin/accounts/user/{target.pk}/change/", {
        "email": target.email, "employee_id": target.employee_id, "full_name": target.full_name,
        "department": target.department_id, "is_active": "on", "roles": [Role.ADMIN, Role.REQUESTER],
    })
    assert resp.status_code == 200 and "cannot be combined" in resp.content.decode()


def test_admin_role_change_audited_and_ends_sessions(admin_client, people):
    target = people["requester"]
    before = target.session_version
    resp = admin_client.post(f"/admin/accounts/user/{target.pk}/change/", {
        "email": target.email, "employee_id": target.employee_id, "full_name": target.full_name,
        "department": target.department_id, "is_active": "on", "roles": [Role.REQUESTER, Role.BUSINESS_OWNER],
    })
    assert resp.status_code == 302
    target.refresh_from_db()
    assert target.session_version == before + 1
    assert set(target.groups.values_list("name", flat=True)) == {Role.REQUESTER, Role.BUSINESS_OWNER}
    ev = AuditEvent.objects.filter(action="admin.user_changed").last()
    assert "roles" in ev.detail["changed"]


def test_admin_cannot_remove_own_role(admin_client, people):
    admin = people["admin"]
    admin_client.post(f"/admin/accounts/user/{admin.pk}/change/", {
        "email": admin.email, "employee_id": admin.employee_id, "full_name": admin.full_name,
        "department": admin.department_id, "roles": [Role.REQUESTER],
    })
    admin.refresh_from_db()
    assert list(admin.groups.values_list("name", flat=True)) == [Role.ADMIN] and admin.is_active


def test_create_user_issues_temporary_password(admin_client, dept):
    resp = admin_client.post("/admin/accounts/user/add/", {
        "email": "New.Person@Example.test", "employee_id": "E777", "full_name": "New Person",
        "department": dept.pk, "is_active": "on", "roles": [Role.REQUESTER],
    }, follow=True)
    assert resp.status_code == 200
    user = User.objects.get(employee_id="E777")
    assert user.email == "new.person@example.test" and user.must_change_password
    assert user.password.startswith("argon2")


def test_assignment_service_rules(draft_review, people, make_user):
    admin = people["admin"]
    with pytest.raises(ServiceError):
        assign(admin, draft_review.pk, admin, "REVIEWER")  # self-assignment
    other_admin = make_user(Role.ADMIN)
    with pytest.raises(ServiceError):
        assign(admin, draft_review.pk, other_admin, "REQUESTER")  # admin account as participant
    with pytest.raises(ServiceError):
        assign(admin, draft_review.pk, people["owner"], "REVIEWER")  # lacks role
    with pytest.raises(ServiceError):
        assign(admin, draft_review.pk, people["requester"], "REVIEWER")
    a = assign(admin, draft_review.pk, people["reviewer2"], "REVIEWER")
    assert AuditEvent.objects.filter(action="admin.assignment_changed", object_id=str(a.pk)).exists()


def test_assignment_page_is_metadata_only(admin_client, in_review):
    body = admin_client.get("/administration/review-assignments/").content.decode()
    assert in_review.reference in body
    assert in_review.purpose not in body and "[Demo] Is access granted" not in body


def test_account_events_exclude_assessment_payloads(admin_client, in_review):
    body = admin_client.get("/administration/account-events/").content.decode()
    assert "assessment.generated" not in body and "answer.saved" not in body
    assert "user.login" in body


def test_admin_cannot_read_evidence(admin_client, assessment_review, people):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.assurance.services.evidence import upload_evidence
    from apps.reviews.models import Applicability

    rr = assessment_review.requirements.filter(applicability=Applicability.KEEP).first()
    link = upload_evidence(people["requester"], assessment_review, rr, SimpleUploadedFile("secret.pdf", b"%PDF-1 x"))
    assert admin_client.get(f"/evidence/{link.evidence.pk}/download/").status_code == 404
    assert admin_client.get("/admin/assurance/evidencepolicy/").status_code == 200
    assert "secret.pdf" not in admin_client.get("/admin/assurance/evidencepolicy/").content.decode()


def test_groups_created_by_migration():
    assert set(Group.objects.values_list("name", flat=True)) >= set(Role.ALL)
