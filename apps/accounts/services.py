"""Sign-in flow: password -> email code -> forced password change -> session (spec 4.2).

Between steps the browser holds only a *pending* session: Django's
``request.user`` stays anonymous, so no protected route is reachable.
"""
import secrets
from datetime import datetime, timedelta

import django_otp
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone

from apps.audit.services import record
from apps.core.stage import is_password_only

from .models import Role, SecuritySetting, SignInCodeDevice

PENDING_USER = "af_pending_user"
PENDING_VERSION = "af_pending_version"
PENDING_STARTED = "af_pending_started"
PENDING_CODE_OK = "af_pending_code_ok"
PENDING_ATTEMPTS = "af_pending_attempts"
PENDING_BACKEND = "af_pending_backend"
AUTH_COMPLETE = "af_auth_complete"
SESSION_STARTED = "af_session_started"
LAST_ACTIVITY = "af_last_activity"

PENDING_MAX_AGE = timedelta(minutes=15)
GENERIC_LOGIN_ERROR = "Sign-in failed. Check your details or contact the service desk if your account is locked."

User = get_user_model()


def find_user(identifier):
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    return User.objects.filter(Q(email__iexact=identifier) | Q(employee_id=identifier)).first()


def mask_email(email):
    local, _, domain = email.partition("@")
    shown = local[:2] if len(local) > 2 else local[:1]
    return f"{shown}{'•' * max(3, len(local) - len(shown))}@{domain}"


# --- step 1: password -------------------------------------------------------

def begin_login(request, identifier, password):
    """Validate credentials and create a pending-login session. Returns (ok, message)."""
    policy = SecuritySetting.current()
    candidate = find_user(identifier)

    if candidate is not None and candidate.is_locked:
        record("user.login_failed", actor=candidate, detail={"reason": "locked"}, request=request)
        return False, GENERIC_LOGIN_ERROR

    user = authenticate(request, identifier=identifier, password=password)
    if user is None:
        with transaction.atomic():
            if candidate is not None:
                locked = User.objects.select_for_update().get(pk=candidate.pk)
                locked.failed_logins += 1
                if locked.failed_logins >= policy.lockout_threshold:
                    locked.locked_until = timezone.now() + timedelta(minutes=policy.lockout_minutes)
                    locked.failed_logins = 0
                    locked.save(update_fields=["failed_logins", "locked_until"])
                    record("user.locked", actor=locked, detail={"reason": "password_failures"}, request=request)
                else:
                    locked.save(update_fields=["failed_logins"])
            record(
                "user.login_failed",
                actor=candidate,
                detail={"reason": "invalid_credentials", "known_account": candidate is not None},
                request=request,
            )
        return False, GENERIC_LOGIN_ERROR

    with transaction.atomic():
        user.failed_logins = 0
        user.locked_until = None
        user.save(update_fields=["failed_logins", "locked_until"])
        record("user.login", actor=user, detail={"stage": "password_verified"}, request=request)

    request.session.flush()  # new session id; drop anything from before
    request.session[PENDING_USER] = user.pk
    request.session[PENDING_VERSION] = user.session_version
    request.session[PENDING_STARTED] = timezone.now().isoformat()
    request.session[PENDING_BACKEND] = user.backend
    request.session[PENDING_ATTEMPTS] = 0
    if is_password_only(settings):
        request.session[PENDING_CODE_OK] = False
    else:
        send_code(request, user)
    return True, ""


def pending_user(request):
    """Return the user for a valid pending-login session, else None."""
    user_id = request.session.get(PENDING_USER)
    started = request.session.get(PENDING_STARTED)
    if not user_id or not started:
        return None
    try:
        started_at = datetime.fromisoformat(started)
    except ValueError:
        return None
    if timezone.now() - started_at > PENDING_MAX_AGE:
        clear_pending(request)
        return None
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None or user.session_version != request.session.get(PENDING_VERSION) or user.is_locked:
        clear_pending(request)
        return None
    return user


def clear_pending(request):
    for key in (PENDING_USER, PENDING_VERSION, PENDING_STARTED, PENDING_CODE_OK, PENDING_ATTEMPTS, PENDING_BACKEND):
        request.session.pop(key, None)


# --- step 2: email code -----------------------------------------------------

def get_device(user):
    device, _ = SignInCodeDevice.objects.get_or_create(
        user=user, name="registered-email", defaults={"confirmed": True}
    )
    return device


def send_code(request, user):
    """Generate and email a one-time code to the account's registered email.

    Uses django-otp's cooldown (generate_is_allowed/cooldown_set) and token
    generation. The destination is always ``user.email``.
    """
    policy = SecuritySetting.current()
    with transaction.atomic():
        device = SignInCodeDevice.objects.select_for_update().get(pk=get_device(user).pk)
        allowed, info = device.generate_is_allowed()
        if not allowed:
            return False, "Please wait before requesting another code."
        device.email = None
        device.cooldown_set(commit=False)
        device.generate_token(length=6, valid_secs=policy.otp_lifetime_seconds, commit=True)
        body = render_to_string(
            "accounts/email/code.txt",
            {"token": device.token, "minutes": max(1, policy.otp_lifetime_seconds // 60), "user": user},
        )
        send_mail("AssureFlow sign-in code", body, settings.DEFAULT_FROM_EMAIL, [user.email])
        record("user.code_sent", actor=user, detail={"destination": mask_email(user.email)}, request=request)
    request.session[PENDING_ATTEMPTS] = 0
    return True, "A new code has been sent."


def verify_code(request, user, code):
    """Consume the code atomically. Returns (ok, message)."""
    policy = SecuritySetting.current()
    code = (code or "").strip()
    with transaction.atomic():
        device = SignInCodeDevice.objects.select_for_update().filter(user=user, name="registered-email").first()
        ok = bool(device and code.isdigit() and device.verify_token(code))
        if ok:
            request.session[PENDING_CODE_OK] = True
            record("user.code_verified", actor=user, request=request)
            return True, ""
        attempts = request.session.get(PENDING_ATTEMPTS, 0) + 1
        request.session[PENDING_ATTEMPTS] = attempts
        record("user.code_failed", actor=user, detail={"attempt": attempts}, request=request)
        if attempts >= policy.otp_retry_limit:
            if device:
                device.token = None
                device.save(update_fields=["token"])
            locked = User.objects.select_for_update().get(pk=user.pk)
            locked.locked_until = timezone.now() + timedelta(minutes=policy.lockout_minutes)
            locked.save(update_fields=["locked_until"])
            record("user.locked", actor=user, detail={"reason": "code_failures"}, request=request)
            clear_pending(request)
            return False, "Too many incorrect codes. Your account is temporarily locked."
    return False, "That code is incorrect or has expired."


def code_step_satisfied(request):
    if is_password_only(settings):
        return True
    return bool(request.session.get(PENDING_CODE_OK))


# --- step 3: password change / completion -----------------------------------

def needs_password_change(user):
    return user.must_change_password or user.password_expired(SecuritySetting.current().password_expiry_days)


def change_password(request, user, new_password):
    with transaction.atomic():
        user.set_password(new_password)
        user.must_change_password = False
        user.password_changed_at = timezone.now()
        user.save(update_fields=["password", "must_change_password", "password_changed_at"])
        record("user.password_changed", actor=user, request=request)


def complete_login(request, user):
    """Create the authenticated session: rotates the key and starts the absolute timer."""
    backend = request.session.get(PENDING_BACKEND) or settings.AUTHENTICATION_BACKENDS[0]
    clear_pending(request)
    django_login(request, user, backend=backend)  # cycles the session key
    if not is_password_only(settings):
        django_otp.login(request, get_device(user))
    now = timezone.now().isoformat()
    request.session[AUTH_COMPLETE] = True
    request.session[SESSION_STARTED] = now
    request.session[LAST_ACTIVITY] = now
    record(
        "user.login",
        actor=user,
        detail={"stage": "session_established", "email_code_verified": not is_password_only(settings)},
        request=request,
    )


def logout(request, reason="user"):
    user = request.user if request.user.is_authenticated else None
    if user is not None:
        record("user.logout", actor=user, detail={"reason": reason}, request=request)
    django_logout(request)


# --- administration helpers --------------------------------------------------

def generate_temporary_password():
    return secrets.token_urlsafe(12)


def roles_conflict(role_names):
    """Admin cannot be combined with a business-participant role (spec 3.4)."""
    names = set(role_names)
    return Role.ADMIN in names and bool(names & set(Role.BUSINESS))
