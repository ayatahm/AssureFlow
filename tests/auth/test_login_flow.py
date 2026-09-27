"""Full sign-in flow: password -> email code -> forced password change (spec 4, 30.2).

These run in the EMAIL_CODE_REQUIRED stage and are mandatory for release.
"""
import threading
from datetime import timedelta

import pytest
from django.core import mail
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role, SecuritySetting, SignInCodeDevice
from apps.audit.models import AuditEvent
from tests.conftest import PASSWORD, extract_code, full_login

pytestmark = [pytest.mark.django_db, pytest.mark.auth_verification]

PROTECTED = ["/dashboard/", "/reviews/", "/reviews/new/", "/admin/", "/administration/review-assignments/"]


def login_password(client, user, identifier=None, password=PASSWORD):
    return client.post("/account/login/", {"identifier": identifier or user.email, "password": password})


@pytest.fixture
def requester(make_user, content):
    return make_user(Role.REQUESTER, verified=False)


def test_no_signup_or_impersonation_routes(client):
    for url in ("/account/signup/", "/account/register/", "/signup/", "/admin/login-as/", "/account/impersonate/"):
        assert client.get(url).status_code in (302, 404)
    body = client.get("/account/login/").content.decode()
    assert "Sign up" not in body and "Sign in as" not in body and "role" not in body.lower()


def test_anonymous_redirected_from_protected_pages(client, content):
    for url in PROTECTED:
        resp = client.get(url)
        assert resp.status_code == 302 and "/account/login/" in resp["Location"], url


def test_login_by_email_or_employee_id(client, requester):
    resp = login_password(client, requester, identifier=requester.employee_id)
    assert resp["Location"] == "/account/verify/"
    assert len(mail.outbox) == 1 and mail.outbox[0].to == [requester.email]


def test_password_alone_grants_nothing(client, requester):
    login_password(client, requester)
    for url in PROTECTED:
        resp = client.get(url)
        assert resp.status_code == 302 and "/account/login/" in resp["Location"], url


def test_valid_code_completes_login_and_rotates_session(client, requester):
    login_password(client, requester)
    pending_key = client.session.session_key
    code = extract_code(mail.outbox[-1].body)
    resp = client.post("/account/verify/", {"code": code})
    assert resp.status_code == 302 and resp["Location"] == "/dashboard/"
    assert client.session.session_key != pending_key
    assert client.get("/dashboard/").status_code == 200
    actions = list(AuditEvent.objects.filter(actor=requester).values_list("action", flat=True))
    assert "user.code_sent" in actions and "user.code_verified" in actions
    login_events = AuditEvent.objects.filter(actor=requester, action="user.login")
    assert {e.detail["stage"] for e in login_events} == {"password_verified", "session_established"}


def test_code_is_single_use(client, requester):
    login_password(client, requester)
    code = extract_code(mail.outbox[-1].body)
    client.post("/account/verify/", {"code": code})
    other = Client()
    login_password(other, requester)
    # Old code was consumed and replaced by a new one; reusing the old one fails.
    resp = other.post("/account/verify/", {"code": code})
    assert resp.status_code == 200 and b"incorrect" in resp.content


def test_expired_code_refused(client, requester):
    login_password(client, requester)
    code = extract_code(mail.outbox[-1].body)
    SignInCodeDevice.objects.filter(user=requester).update(valid_until=timezone.now() - timedelta(seconds=1))
    resp = client.post("/account/verify/", {"code": code})
    assert resp.status_code == 200
    assert client.get("/dashboard/").status_code == 302


def test_code_never_logged_in_audit(client, requester):
    login_password(client, requester)
    code = extract_code(mail.outbox[-1].body)
    client.post("/account/verify/", {"code": "000000" if code != "000000" else "111111"})
    for e in AuditEvent.objects.all():
        text = str(e.detail)
        assert code not in text and PASSWORD not in text


def test_retry_limit_locks(client, requester, settings):
    policy = SecuritySetting.current()
    policy.otp_retry_limit = 3
    policy.save()
    login_password(client, requester)
    code = extract_code(mail.outbox[-1].body)
    wrong = "123456" if code != "123456" else "654321"
    for _ in range(3):
        client.post("/account/verify/", {"code": wrong})
    requester.refresh_from_db()
    assert requester.is_locked
    assert AuditEvent.objects.filter(actor=requester, action="user.locked").exists()
    # pending session was cleared and the correct code no longer works
    resp = client.post("/account/verify/", {"code": code})
    assert resp.status_code == 302 and resp["Location"] == "/account/login/"


def test_password_lockout(client, requester):
    policy = SecuritySetting.current()
    policy.lockout_threshold = 2
    policy.save()
    login_password(client, requester, password="wrong-password-1")
    login_password(client, requester, password="wrong-password-2")
    requester.refresh_from_db()
    assert requester.is_locked
    resp = login_password(client, requester)
    assert resp.status_code == 200  # correct password refused while locked
    assert len(mail.outbox) == 0


def test_resend_has_cooldown_and_goes_to_registered_email(client, requester):
    login_password(client, requester)
    client.post("/account/verify/resend/")
    assert len(mail.outbox) == 1  # cooldown blocks immediate resend
    SignInCodeDevice.objects.filter(user=requester).update(last_generated_timestamp=timezone.now() - timedelta(hours=1))
    client.post("/account/verify/resend/")
    assert len(mail.outbox) == 2 and all(m.to == [requester.email] for m in mail.outbox)


def test_simultaneous_code_use_authenticates_one_session(transactional_db, make_user, content):
    user = make_user(Role.REQUESTER, verified=False)
    a, b = Client(), Client()
    login_password(a, user)
    SignInCodeDevice.objects.filter(user=user).update(last_generated_timestamp=timezone.now() - timedelta(hours=1))
    login_password(b, user)  # second pending session; newest code wins
    code = extract_code(mail.outbox[-1].body)
    results = []
    barrier = threading.Barrier(2)

    def verify(c):
        from django.db import connections

        barrier.wait()
        resp = c.post("/account/verify/", {"code": code})
        results.append(resp.status_code == 302 and resp["Location"] == "/dashboard/")
        connections.close_all()

    threads = [threading.Thread(target=verify, args=(c,)) for c in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1


def test_temporary_password_forces_change_before_access(client, make_user, content):
    user = make_user(Role.REQUESTER, verified=False, must_change_password=True)
    resp = full_login(client, user)
    assert resp["Location"] == "/account/password/change/"
    assert client.get("/dashboard/").status_code == 302
    resp = client.post("/account/password/change/", {
        "current_password": PASSWORD, "new_password1": "A-much-better-passphrase-77",
        "new_password2": "A-much-better-passphrase-77",
    })
    assert resp.status_code == 302 and resp["Location"] == "/dashboard/"
    assert client.get("/dashboard/").status_code == 200
    user.refresh_from_db()
    assert not user.must_change_password
    assert AuditEvent.objects.filter(actor=user, action="user.password_changed").exists()


def test_password_change_page_requires_code_first(client, make_user, content):
    user = make_user(Role.REQUESTER, verified=False, must_change_password=True)
    login_password(client, user)
    resp = client.get("/account/password/change/")
    assert resp.status_code == 302 and resp["Location"] == "/account/verify/"


def test_password_expiry_policy(client, make_user, content):
    policy = SecuritySetting.current()
    policy.password_expiry_days = 30
    policy.save()
    user = make_user(Role.REQUESTER, verified=False)
    type(user).objects.filter(pk=user.pk).update(password_changed_at=timezone.now() - timedelta(days=31))
    assert full_login(client, user)["Location"] == "/account/password/change/"


def test_idle_timeout(client, make_user, content):
    user = make_user(Role.REQUESTER, verified=False)
    full_login(client, user)
    session = client.session
    session["af_last_activity"] = (timezone.now() - timedelta(minutes=31)).isoformat()
    session.save()
    resp = client.get("/dashboard/")
    assert resp.status_code == 302 and "/account/login/" in resp["Location"]


def test_absolute_timeout_despite_activity(client, make_user, content):
    user = make_user(Role.REQUESTER, verified=False)
    full_login(client, user)
    session = client.session
    session["af_session_started"] = (timezone.now() - timedelta(minutes=481)).isoformat()
    session.save()
    resp = client.get("/dashboard/")
    assert resp.status_code == 302


def test_disabled_user_loses_session_immediately(client, make_user, content):
    user = make_user(Role.REQUESTER, verified=False)
    full_login(client, user)
    assert client.get("/dashboard/").status_code == 200
    type(user).objects.filter(pk=user.pk).update(is_active=False)
    assert client.get("/dashboard/").status_code == 302


def test_session_version_bump_ends_session(client, make_user, content):
    user = make_user(Role.REQUESTER, verified=False)
    full_login(client, user)
    type(user).objects.filter(pk=user.pk).update(session_version=user.session_version + 1)
    assert client.get("/dashboard/").status_code == 302


def test_logged_in_without_otp_device_session_is_rejected(client, make_user, content):
    """A session established by Django login alone (no code) never reaches protected pages."""
    user = make_user(Role.REQUESTER, verified=False)
    client.force_login(user)
    resp = client.get("/dashboard/")
    assert resp.status_code == 302 and "/account/login/" in resp["Location"]


def test_admin_route_requires_full_flow(client, make_user, content):
    admin = make_user(Role.ADMIN, verified=False)
    client.force_login(admin)
    assert client.get("/admin/").status_code == 302
    client.logout()
    full_login(client, admin)
    assert client.get("/admin/").status_code == 200


def test_csrf_enforced_on_modifying_request(make_user, content):
    user = make_user(Role.REQUESTER, verified=False)
    c = Client(enforce_csrf_checks=True)
    resp = c.post("/account/login/", {"identifier": user.email, "password": PASSWORD})
    assert resp.status_code == 403
